# From the cookbook to this plugin

This baseline ports Anthropic's [Dreaming with a dynamic
workflow](https://github.com/anthropics/claude-cookbooks/blob/main/managed_agents/CMA_dreaming_with_a_dynamic_workflow.ipynb)
from Claude Managed Agents to a Claude Code plugin. It keeps the cookbook's prompts, method and defaults, and
changes only what Managed Agents provides and Claude Code does not, plus the agents' model and effort. This file
lists every such change.

- **Source:** `anthropics/claude-cookbooks`, `main` at `8ad5de647d4f` (2026-11-20). The notebook last changed
  in `d4bd2fb364a7` (2026-11-04).
- **Taken unchanged:** `prompts/analyzer_system.md` and `prompts/organizer_system.md`, apart from the edits
  listed below. Also the transcript rendering in `memory_distill_utils.py` (lines 100 to 281, from its label
  rules to `render_text`), copied to `scripts/render.py`.
- **License:** MIT, Copyright (c) 2023 Anthropic. See `scripts/LICENSE-claude-cookbooks`.

## How the pieces map

| Cookbook (Managed Agents) | This plugin (Claude Code) |
| --- | --- |
| The notebook, run by hand, or `dreaming_nightly.py` from cron | `/hstack:dream`, run by hand in a session of its own (`SKILL.md`) |
| Export finished sessions with the API (`export_session`), render them (`render_text`), upload them | `dream.py prepare` reads Claude Code's logs (`claude_logs.py`), renders them with the same `render_text`, and writes them to the run's folder |
| `memory-distiller`, a Sonnet lead agent, writes a workflow from `work_request.md` and its `lead_system.md`, then starts it | A fixed workflow script, `workflows/dream-workflow.js`, started by the skill |
| `transcript-analyzer` and `memory-organizer`, Haiku, five file tools | Plugin agents `hstack:transcript-analyzer` and `hstack:memory-organizer`, Opus, the same five tools |
| One analyzer per transcript in groups of about 20, then the organizer once | The same, in the workflow script |
| A memory store, mounted read-write in the dream session | `.dreaming/memory/` in the project |
| A copy of the store's files in the run's records, and memory versions | `memory_before/` in the run's folder, for the latest 5 runs |
| Sandbox with no network, every tool call checked by the `auto` policy | The agents' five tools, plus a hook that keeps them inside the transcripts and memory (`hooks/dream_guard.py`) |
| `INSTRUCTIONS` → `{run_preferences}` in each agent's system prompt | Text after `/hstack:dream` → the same paragraph, at the end of each agent's task message |
| Check that every transcript was analyzed and the organizer finished; delete the `MOVED TO` placeholders | `dream.py finish` |
| The next night takes sessions created since the last run started; a failed night keeps its window | The same, kept in `.dreaming/state/state.json`. The first run takes the last 14 days, the notebook's default, and that start is saved at once, so a failed first run keeps it too |
| Records in a folder only you can read (`0700`, files `0600`) | The run state the same way: `.dreaming/state/` and each run's folder `0700`, transcripts `0600` |
| Mount the store read-only in the agent's sessions | Import `_index.md` from the project's `CLAUDE.md` (see the README) |

## The prompts

Each change replaces a Managed Agents phrase. Nothing else in either prompt changed.

### `analyzer_system.md` → `agents/transcript-analyzer.md`

| Cookbook | Plugin | Why |
| --- | --- | --- |
| "plus a shared memory directory mounted read-write." | "plus a shared memory directory you can read and write." | Nothing is mounted. |
| "a plain-text export of a Claude Managed Agents session;" | "a plain-text export of a Claude Code session;" | The sessions are Claude Code's, rendered in the same format. |
| "Your task message names the one transcript file you are assigned to, by its path under /mnt/session/uploads/. The shared memory directory you read and write is mounted at {mem_dir}." | "Your task message names the one transcript file you are assigned to, by its path, and the shared memory directory you read and write." | A plugin agent's system prompt is fixed, and the memory directory differs per project, so the task message names it. The cookbook's example task already does. |
| "{run_preferences}For your assigned transcript:" | "For your assigned transcript:" | Same reason: the preferences paragraph moved to the end of the task message. |

### `organizer_system.md` → `agents/memory-organizer.md`

| Cookbook | Plugin | Why |
| --- | --- | --- |
| "to a shared memory directory, mounted read-write at {mem_dir}." | "to a shared memory directory, the one your task message names." | As for the analyzer. |
| "write the file at its new path inside {mem_dir})" | "write the file at its new path inside that directory)" | As for the analyzer. |
| "The platform checks each write or edit before it runs;" | "Each write or edit is checked before it runs;" | The hook checks it, not the platform. |
| "{run_preferences}When you are done," | "When you are done," | The preferences paragraph moved to the task message. |

### What each agent file adds

- `tools: Read, Write, Edit, Glob, Grep`: the cookbook's five tools.
- `model: opus`, where the cookbook's `ANALYZER_MODEL` and `ORGANIZER_MODEL` are Claude Haiku 5.5. A run here
  uses the developer's Claude plan, not API billing, so it buys quality with usage. On Artificial Analysis
  (2026-11), Haiku 5.5 trails Opus 5.5 at the same effort on the benchmarks nearest this job, AA-Briefcase
  (agentic knowledge work), AutomationBench-AA and GDP.pdf (documents), and at max it still trails Opus 5.5 at
  medium. Sonnet 5.5 at high effort ties Opus 5.5 at medium on those, takes the same time per Intelligence Index
  task and writes more output tokens across the index.
- `effort`: medium for the analyzer, xhigh for the organizer. The cookbook names no effort level, and Managed
  Agents ran its agents at its default. Without this line, the agent's level would come from the session's
  settings, and the session's `/effort` level does not apply to an agent on another model. The line overrides the
  session's level but not `CLAUDE_CODE_EFFORT_LEVEL`, which, when set, decides both agents' level. The analyzer
  runs once per transcript, so it stays at Opus 5.5's default. The organizer runs once, and its merges and
  conflict calls decide what every later session reads, so it gets xhigh. Over high, Opus 5.5 at xhigh scores
  77 Elo more on AA-Briefcase, the benchmark nearest the organizer's job, and 1.8 points more on
  AutomationBench-AA, but 2.2 points less on GDP.pdf, for 1.7 times the time per Intelligence Index task and 1.8
  times the output tokens across it. The organizer is one agent at the end of a run, so that cost is paid once.
- `omitClaudeMd: true`: a Managed Agents agent sees only its own prompt. Without this line, a Claude Code
  subagent would also get the project's `CLAUDE.md`.
- A `description` saying that only `/hstack:dream`'s workflow uses the agent, since Claude can pick a plugin's
  agents for other work.

### The task messages

The cookbook's lead agent worded each agent's task itself, so the cookbook has no fixed task text. The workflow
uses these:

- **Analyzer:** the task the notebook shows its lead agent wrote, word for word, with this run's paths.
- **Organizer:** "Every analyzer has finished. Tidy the whole memory directory at `<memory>`." The cookbook shows
  no example of this one, so it was written for this port.
- **Preferences:** the paragraphs of the cookbook's `analyzer_preferences` and `organizer_preferences`, word for
  word, at the end of each task message. The analyzer's still says "the methodology below", as in the cookbook,
  where it came before the methodology.

## The lead agent's work

The workflow script replaces the lead agent, so `lead_system.md` and `work_request.md` are not used. The script
does what the work request asks: one analyzer per transcript, in groups of about 20, each group finishing before
the next starts, then the organizer once. A failed agent is recorded with its reason and does not stop the run.
It returns the fields of the lead agent's one-line report: `inputs_expected`, `inputs_processed`, `failures`,
`organized`, `index_entries` and `changes`.

`dream.py finish` reads that report from the file Claude Code saves for each workflow run
(`workflows/wf_<id>.json` in the dream session's folder, under `result.report`), so no model copies it. That
file is not documented: when it is missing or reads differently, Claude passes the report in instead.

These cookbook steps existed because a model wrote and ran the workflow, so they are left out:

- asking the lead agent to start the run again when its report counted nothing as processed;
- asking it to print the workflow it wrote;
- comparing its report with the platform's thread records. The counts now come from the script itself.

## The session export

`claude_logs.py` applies the rules of the cookbook's `export_session` and `compact_events` to Claude Code's
logs, and nothing more:

- **Only the conversation is kept:** user and assistant messages (their text, text documents and search
  results, which the cookbook's `render_text` writes out), tool calls and tool results. Other records (settings,
  file snapshots, the copies of API requests, titles) are left out, as `compact_events` keeps only conversation
  events.
- **Each subagent is a sub-thread.**
- **Thinking and each record's bookkeeping** (ids, times, usage) are left out, as the cookbook leaves out
  thinking events and `processed_at`.

These rules exist because Claude Code's logs differ from a Managed Agents export:

- **Text Claude Code put in the user's turn is left out:** skill bodies (`isMeta`), compaction summaries
  (`isCompactSummary`), and notices it sends itself, such as a background agent finishing (a user message whose
  `origin` is not `human`). The user did not write them. A log that Claude Code trimmed therefore loses what
  only its summary held.
- **Images and documents that are not text are dropped.** Rendered as text, they would be base64.
- **A tool output saved to a file is read back.** Claude Code moves large outputs out of the log, and a
  Managed Agents export holds them whole. Both of Claude Code's previews are read ("Full output saved to" and
  "only the first … were saved to"), from the file they name if it is in a `tool-results` folder of Claude
  Code's own logs (a fork's previews name its parent session's), and never from anywhere else, since a tool's
  output could imitate a preview. When the file is gone or was never written, the transcript keeps the
  preview; when only part was saved, it holds that part. Either way a `NOTE ABOUT THIS FILE` line, which the
  analyzer's prompt already describes, says so, and the run's report notes it.
- **Which sessions are the project's.** The cookbook picks one agent's sessions by its id. Here they are:
  - the logs in the project's log folder, named as Claude Code names it (the path in NFC, every UTF-16 unit but
    an ASCII letter or digit made `-`, a name over 200 units cut and given a hash suffix), or the folder
    `CLAUDE_CODE_PROJECT_DIR_NAME` names;
  - that were started in the project's folder, since two folders can share a log folder (`client-a` and
    `client/a`);
  - one per session id, the newest where two log folders hold the same session;
  - each a session's own log, whose records carry the id it is named after.
- **When a session started** is its first message's time. A forked session's copied messages are marked
  `forkedFrom` and keep their old times, so a fork starts at its first message that is not a copy. A fork with
  none yet has nothing of its own to dream over and is not taken until it has.

## Added for Claude Code

The cookbook did not need these, because Managed Agents provided them:

- **Dream sessions are never analyzed.** The cookbook reads one agent's sessions, and the dream runs under other
  agents. In Claude Code every session in the folder looks alike, so `prepare` leaves out:
  - the session it runs in, which it records in `state.json`;
  - every session recorded there before;
  - any session where `/hstack:dream` came before any reply, known by the whole message Claude Code logs for a
    typed command (its `command-message` and `command-name`), not by text that quotes it. That includes a
    session forked from a dream session, which starts with the dream's copied command;
  - any log whose records carry one of those sessions' ids, all of them known before any log is taken;
  - the logs of the dream's own agents, in any session.

  It refuses to run in a session that already has a conversation, since that conversation would then never be
  analyzed.
- **Each run is closed.** `prepare` records the run the agents may read (`current_run`), `finish` closes it,
  and the next `prepare` closes any run that never finished.
- **Only the organizer's placeholders are deleted.** The cookbook deletes the placeholders its session wrote
  during the organizer's run, and its delete carries the content's hash. Here `finish` deletes only placeholders
  the hook logged the organizer writing during this run, moving each aside first and deleting it only if it is
  still the placeholder, so a note restored at its path meanwhile stays.
- **Linked folders are refused.** If `.dreaming/` or its `memory/`, `state/` or `runs/` is a link to another
  folder, `prepare` and `finish` stop before changing anything, as the hook refuses the agents everything.
- **The hook** stands in for the sandbox. As the cookbook mounted only the current run's uploads, the agents
  may read only the current run's transcripts. It refuses everything if `.dreaming/memory/` or
  `.dreaming/state/` is a link to another folder. It also refuses memory
  files named as instruction files (`CLAUDE.md`, `AGENTS.md`...): Claude Code would load such a file from
  `.dreaming/memory/` as instructions, which a memory store never is.
- **`.dreaming/state/.gitignore`** keeps the run state, with its full copies of the sessions, out of the
  project's git. Memory itself is not ignored: committing it is the project's choice.
- **Only the latest 5 runs keep their transcripts and their copy of memory.** The cookbook kept its records in
  a temporary folder that the system clears. Here they are inside the project, where a session that searches it
  could read the transcripts back into the next dream.

## Left out

- The cost estimate and `BUDGET_USD`: a run counts against your Claude plan, not an API budget.
- The nightly script and cron.
- Memory versions and redaction, which are Managed Agents features. `memory_before/` is the copy kept instead.
- Creating, archiving and deleting agents, environments, uploads and sessions.

## Known differences in behavior

- **Long lines.** Claude Code's Read tool shortens lines over 2,000 characters, so an analyzer misses the end
  of a very long single-line tool input or output.
- **Concurrency.** A workflow runs at most 16 agents at once by default, and fewer on a machine with few CPUs.
  Managed Agents ran up to 64 threads. See the README.
- **Structured results.** The agents return their results through the workflow's structured output, which
  Claude Code adds to their prompt. The cookbook's agents returned them through Managed Agents' workflow steps.
- **One user per project folder.** The window is kept in the project's `.dreaming/state/`, while the sessions
  come from the Claude Code configuration of whoever runs the dream. If two users dream in one shared folder,
  one user's complete run moves the window past the other's sessions. The cookbook kept its window in each
  user's home folder.
