# hstack

My agent workflows, as a Claude Code plugin.

| Command | What it does |
| --- | --- |
| `/hstack:second-opinion` | Writes a self-contained prompt asking another agent to review your uncommitted changes, or whatever you name after the command. Paste the reviewer's reply back, and Claude triages it, fixes what holds up and decides whether another round is worth it. It won't start more than 3 rounds on its own. |
| `/hstack:dream` | Distills this project's recent Claude Code sessions into Markdown memory files in `.dreaming/memory/`, as Anthropic's "Dreaming with a dynamic workflow" cookbook does. See [skills/dream/README.md](skills/dream/README.md). |

`/hstack:dream` also adds two agents (`transcript-analyzer` and `memory-organizer`), one workflow
(`dream-workflow`) and one hook (`hooks/dream_guard.py`). Only `/hstack:dream` uses them. The hook stays silent
for every other agent's tool calls.

## Engineering principles

hstack carries the 24 engineering principles from [pstack](https://github.com/cursor/plugins/tree/main/pstack)
(MIT, see [LICENSE-pstack](LICENSE-pstack)), one skill each in `skills/principle-*/`, plus two companions,
`benchmark-checklist` and `typescript-best-practices`. Claude applies them on its own, without loading them all
into every session. Three paths reach them.

**Routers, chosen by task.** Five skills in `skills/principles-*/` that only Claude invokes. Their short
descriptions, about 1,000 characters together, are all that sits in every session. When the work matches one,
Claude loads it: a table from situation to principle file. Claude reads the file that matches the decision in
front of it, and the table stays in the conversation for later decisions in the same task.

| Router | Claude picks it for | Principles |
| --- | --- | --- |
| `principles-design-data` | types, data structures, signatures, validation, error handling, state, retries, shared state | foundational-thinking, model-the-domain, type-system-discipline, boundary-discipline, make-operations-idempotent, separate-before-serializing-shared-state |
| `principles-design-features` | product, UX and scope tradeoffs, a design with no precedent in the codebase, a new requirement in an existing design | experience-first, exhaust-the-design-space, redesign-from-first-principles |
| `principles-refactor-code` | refactoring, sizing a diff, code that is hard to trace, replacing an internal API | laziness-protocol, subtract-before-you-add, minimize-reader-load, migrate-callers-then-delete-legacy-apis |
| `principles-verify-work` | debugging, tests, measuring, fixes that keep failing, before calling work done | fix-root-causes, attack-the-premise, test-behavior-not-implementation, explain-the-number (+ benchmark-checklist), prove-it-works |
| `principles-plan-delivery` | phased rewrites and migrations, sweeps of similar edits, stacking commits and PRs, work a script could do, a repeated correction | outcome-oriented-execution, sequence-verifiable-units, build-the-lever, encode-lessons-in-structure |

**TypeScript, by file.** `typescript-best-practices` becomes available once Claude works with a `.ts` or `.tsx`
file, through its `paths` setting.

**A hook, for failed fixes.** `hooks/premise_cue.py` watches shell commands and file edits. When the same command,
in the same directory, has failed after two separate rounds of edits, it adds a note next to the failure pointing
Claude to `attack-the-premise`. It can't see a piped command's failure, such as `pytest | tail`, or an edit made
through the shell. The rules are in the script's docstring, and its tests run from this folder with
`python3 -m unittest discover -s hooks/tests -v`.

Every principle also runs by hand as `/hstack:principle-<name>`. Two are manual only: `guard-the-context-window`
and `never-block-on-the-human`, which overlap with Claude Code's own permission and subagent behavior.

To add a principle, put it in `skills/principle-<name>/SKILL.md` with `disable-model-invocation: true`, so its
description stays out of every session, and add a row for it to one `principles-*` router.
