"""Claude Code's session logs as the export that the cookbook's render_text takes.

This applies the cookbook's export rules (export_session and compact_events in memory_distill_utils.py) to
Claude Code's logs, and nothing more:

- Only the conversation is kept: user and assistant messages (their text, text documents and search results),
  tool calls and their results.
- Each subagent's log is a sub-thread of its session.
- Thinking and each record's bookkeeping (ids, times, usage, settings) are left out.

Some rules exist only because Claude Code's logs differ from a Managed Agents export:

- Text Claude Code put in the user's turn (skill bodies, compaction summaries, notices such as a background
  agent finishing) is left out, since the user did not write it. Images and documents that are not text are
  dropped too, since rendered as text they would be base64.
- A tool output Claude Code saved to a file is read back from the session's own folder, as a Managed Agents
  export would hold it. When the file is gone, or Claude Code saved only part of the output, the transcript says
  so in a NOTE ABOUT THIS FILE line.
- A forked session's copies of the messages it was forked from are left out: they are its parent's.
- The cookbook picks one agent's sessions by the agent's id. Here a project's sessions are the logs Claude Code
  keeps for its folder, started in that folder, each under its own session id; the ones /hstack:dream ran in
  are told apart.
"""

import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# The command a dream session starts with, and the agents /hstack:dream's workflow runs. Their logs are never
# part of a transcript.
DREAM_COMMAND = ("hstack:dream", "/hstack:dream")  # as Claude Code logs it: command-message, command-name
DREAM_AGENTS = {"hstack:transcript-analyzer", "hstack:memory-organizer"}
AGENT_NAME = "Claude Code"
# How Claude Code logs a slash command the user typed: a user message made of these tags only.
COMMAND_TAG = re.compile(r"<command-(message|name|args)>(.*?)</command-\1>", re.DOTALL)
# What Claude Code itself prints for a slash command ("Compacted", a model change...), logged in the user's turn.
COMMAND_OUTPUT = re.compile(r"<local-command-(stdout|stderr|caveat)>.*?</local-command-\1>", re.DOTALL)
SESSION_ID = re.compile(r"[0-9a-fA-F-]{36}")
# The preview Claude Code leaves in a tool result whose output it saved to a file. Its first line is either
# "Output too large (…). Full output saved to: <file>" or "Output exceeded the … persist limit; only the first
# … were saved to: <file>"; without a file, the output was not saved at all.
PERSISTED_OUTPUT = re.compile(r"<persisted-output>\s*(?P<line>[^\n]*)")
SAVED_TO = re.compile(r"saved to: (?P<path>[^\n]*?/tool-results/[^\s/]+)")
# Claude Code names a project's log folder after its path (NFC), every UTF-16 code unit but ASCII letters and
# digits made "-". A name over 200 units is cut there and given a hash suffix, which differs between builds.
# CLAUDE_CODE_PROJECT_DIR_NAME, when valid, names the folder outright.
MAX_FOLDER_NAME = 200
PROJECT_DIR_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
RESERVED_NAME = re.compile(r"(?:con|prn|aux|nul|com[0-9]|lpt[0-9])", re.IGNORECASE)


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _folder_name(project: Path) -> str:
    path = unicodedata.normalize("NFC", str(project))
    return "".join(
        char if char.isascii() and char.isalnum() else "--" if ord(char) > 0xFFFF else "-" for char in path
    )


def log_dirs(project: Path) -> list[Path]:
    """The folders that can hold the project's logs: the one named after its path (for a long path, any one
    named with its cut name and a hash suffix) and the one CLAUDE_CODE_PROJECT_DIR_NAME names. Two projects can
    map to one folder (client-a and client/a), so a log is the project's only if belongs_to says so."""
    projects = claude_home() / "projects"
    if not projects.is_dir():
        return []
    name = _folder_name(project)
    if len(name) <= MAX_FOLDER_NAME:
        found = [projects / name]
    else:
        prefix = name[:MAX_FOLDER_NAME] + "-"
        found = sorted(folder for folder in projects.iterdir() if folder.name.startswith(prefix))
    named = os.environ.get("CLAUDE_CODE_PROJECT_DIR_NAME") or ""
    if PROJECT_DIR_NAME.fullmatch(named) and not RESERVED_NAME.fullmatch(named):
        found.append(projects / named)
    unique = []
    for folder in found:
        if folder.is_dir() and folder not in unique:
            unique.append(folder)
    return unique


def session_logs(project: Path) -> list[Path]:
    """The project's session logs, one per session id: where two folders hold a log of the same session (after
    session history was moved, say), the one written last. A subagent's log lives in its session's folder."""
    newest: dict[str, Path] = {}
    for folder in log_dirs(project):
        for log in folder.glob("*.jsonl"):
            if log.name.startswith("agent-"):
                continue
            if log.stem not in newest or log.stat().st_mtime > newest[log.stem].stat().st_mtime:
                newest[log.stem] = log
    return [newest[sid] for sid in sorted(newest)]


def find_log(project: Path, session_id: str) -> Path | None:
    return next((log for log in session_logs(project) if log.stem == session_id), None)


def read_records(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a log still being written can end in a partial line
            if isinstance(record, dict):
                records.append(_queued_message(record) or record)
    return records


def _queued_message(record: dict) -> dict | None:
    """A message the user typed while Claude was working, as the user message it is. Claude Code logs it, once
    delivered, as an attachment of type queued_command whose origin is human; the same attachment type with
    another origin, such as an agent's hand-back, is not the user's."""
    queued = record.get("attachment") if record.get("type") == "attachment" else None
    if not isinstance(queued, dict) or queued.get("type") != "queued_command" or queued.get("isMeta"):
        return None
    origin, prompt = queued.get("origin"), queued.get("prompt")
    if not (isinstance(origin, dict) and origin.get("kind") == "human") or not isinstance(prompt, (str, list)):
        return None
    return {**record, "type": "user", "message": {"role": "user", "content": prompt}, "origin": origin}


def belongs_to(records: list[dict], project: Path) -> bool:
    """Whether a log is the project's: it was started in the project's folder. A log that names no folder is
    taken as the one Claude Code filed it under."""
    started_in = next((r["cwd"] for r in records if isinstance(r.get("cwd"), str)), None)
    if started_in is None:
        return True
    return unicodedata.normalize("NFC", started_in) == unicodedata.normalize("NFC", str(project))


def session_ids(records: list[dict]) -> set[str]:
    """The session ids a log's records carry. A subagent's records carry its parent session's."""
    return {r["sessionId"] for r in records if isinstance(r.get("sessionId"), str)}


def is_own_log(log: Path, records: list[dict]) -> bool:
    """Whether a log is a session's own: its records carry the session id it is named after. A log whose
    records carry only other sessions' ids, such as a subagent's filed beside the sessions, is not one."""
    ids = session_ids(records)
    return not ids or log.stem in ids


def _message_records(records: list[dict]):
    """The user and assistant records, including the ones Claude Code wrote itself."""
    for record in records:
        if record.get("type") in ("user", "assistant") and isinstance(record.get("message"), dict):
            yield record


def created_at(records: list[dict]) -> str | None:
    """When the session started, as a UTC ISO timestamp: its first message's time (for a fork, pass its
    own_records)."""
    return next(
        (r["timestamp"] for r in _message_records(records) if isinstance(r.get("timestamp"), str) and r["timestamp"]),
        None,
    )


def _fork_parents(records: list[dict]) -> list[str]:
    """The sessions this one was forked from: named by a derived-from record whose entries say via "fork" (a
    compacted session gets one too, via "compact", naming itself), or by a copied message's forkedFrom."""
    parents = []
    for record in records:
        sources = record.get("sessions") if record.get("type") == "derived-from" else None
        named = [s.get("id") for s in sources if isinstance(s, dict) and s.get("via") == "fork"] if isinstance(sources, list) else []
        forked = record.get("forkedFrom")
        if isinstance(forked, dict):
            named.append(forked.get("sessionId"))
        for parent in named:
            if isinstance(parent, str) and SESSION_ID.fullmatch(parent) and parent not in parents:
                parents.append(parent)
    return parents


def _parent_log(log: Path, parent: str) -> Path | None:
    """A fork's parent's log: beside the fork's, or failing that in another of Claude Code's log folders."""
    beside = log.parent / f"{parent}.jsonl"
    if beside.is_file():
        return beside
    projects = claude_home() / "projects"
    return next(iter(sorted(projects.glob(f"*/{parent}.jsonl"))), None) if projects.is_dir() else None


def _branch_parent(record: dict) -> str | None:
    """The record a record follows in the conversation. A compaction's boundary has no parent, and names the
    last record before it as its logical one."""
    parent = record.get("parentUuid")
    if parent is None:
        parent = record.get("logicalParentUuid")
    return parent if isinstance(parent, str) else None


def _rewound_away(records: list[dict]) -> set[int]:
    """The records (by id()) on branches the user rewound away from in this session, as the terminal's rewind
    leaves them: the log keeps the abandoned branch, and the message sent after the rewind hangs from the same
    record as the first message of that branch. The conversation now is the chain of records from the last
    message back to the start. Wherever a turn's start on that chain (a user record that is not a tool result: a
    message, a typed command, or the note Claude Code logs before one) has a sibling that is also a turn's
    start, the sibling and everything after it were rewound away. Other branches, such as parallel tool calls'
    results, hang beside a reply or a tool result, and are kept."""
    # Only records that carry the link to the one before them take part: a log written without links (older
    # versions, or by hand) has no branches to tell apart, and every message in it is kept.
    nodes = {
        r["uuid"]: r for r in records
        if isinstance(r.get("uuid"), str) and "parentUuid" in r and not r.get("isSidechain")
    }
    messages = [r for r in _message_records(records) if r.get("uuid") in nodes and nodes[r["uuid"]] is r]
    if not messages:
        return set()
    children: dict[str | None, list[dict]] = {}
    for record in nodes.values():
        children.setdefault(_branch_parent(record), []).append(record)
    current, uuid = set(), messages[-1]["uuid"]
    while uuid in nodes and uuid not in current:
        current.add(uuid)
        uuid = _branch_parent(nodes[uuid])
    rewound: set[int] = set()
    for uuid in current:
        record = nodes[uuid]
        if not _turn_start(record):
            continue
        for sibling in children.get(_branch_parent(record), []):
            if sibling["uuid"] in current or not _turn_start(sibling):
                continue
            stack = [sibling]
            while stack:
                gone = stack.pop()
                if id(gone) not in rewound:
                    rewound.add(id(gone))
                    stack.extend(children.get(gone["uuid"], []))
    return rewound


def _turn_start(record: dict) -> bool:
    """Whether a record starts a turn of the user's: a user record that is not a tool's result, or the boundary
    a /compact leaves."""
    if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
        return True
    content = record.get("message", {}).get("content") if record.get("type") == "user" else None
    if content is None:
        return False
    return not (isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content))


def own_records(log: Path, records: list[dict]) -> list[dict]:
    """The session's records without what is not this session's conversation now: the branches the user
    rewound away from (see _rewound_away), and the messages a fork copied from a session that still holds them
    (in its log, and not rewound away there), which are analyzed with that session. A copy either keeps its original's uuid (a fork made from the
    command line) or names it in forkedFrom (one made in VS Code). A copy whose original's log is gone is kept,
    since nothing else holds it; it keeps its original's time, so a run whose window began later leaves it out."""
    rewound = _rewound_away(records)
    originals = set()
    for parent in _fork_parents(records):
        if (parent_log := _parent_log(log, parent)) is not None:
            parent_records = read_records(parent_log)
            gone = _rewound_away(parent_records)  # what the parent took back is still the fork's
            originals |= {
                r["uuid"] for r in _message_records(parent_records) if isinstance(r.get("uuid"), str) and id(r) not in gone
            }

    def copied(record: dict) -> bool:
        forked = record.get("forkedFrom")
        return (forked.get("messageUuid") if isinstance(forked, dict) else record.get("uuid")) in originals

    return [r for r in records if id(r) not in rewound and not copied(r)]


def _text(record: dict) -> str:
    content = record["message"].get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        )
    return ""


def _invokes_dream(record: dict) -> bool:
    """Whether this record is the user running /hstack:dream: a message of nothing but Claude Code's command
    tags, its command-message and command-name naming it. A message that quotes a tag among words of its own,
    or quotes only one of them, is not."""
    if record["type"] != "user" or record.get("isSidechain") or record.get("isMeta"):
        return False
    text = _text(record)
    tags = {}
    for tag, value in COMMAND_TAG.findall(text):
        tags.setdefault(tag, []).append(value.strip())
    return (
        tags.get("message") == [DREAM_COMMAND[0]]
        and tags.get("name") == [DREAM_COMMAND[1]]
        and not COMMAND_TAG.sub("", text).strip()
    )


def runs_dream(records: list[dict]) -> bool:
    """Whether /hstack:dream was run anywhere in this session."""
    return any(_invokes_dream(r) for r in _message_records(records))


def _command_output(text: str) -> bool:
    """Whether a text block is only what Claude Code printed for a slash command: Claude Code wrote it."""
    return bool(COMMAND_OUTPUT.search(text)) and not COMMAND_OUTPUT.sub("", text).strip()


def _users_words(text: str) -> str:
    """The text without a typed command's tags and what Claude Code printed for it. The words typed after a
    command (its command-args) are the user's, and stay."""
    words = COMMAND_TAG.sub(lambda match: match.group(2) if match.group(1) == "args" else "", text)
    return COMMAND_OUTPUT.sub("", words).strip()


def _from_the_user(record: dict) -> bool:
    """Whether a user message holds something the user wrote or attached, not only a typed command's tags and
    what Claude Code printed for it."""
    if record["type"] != "user" or record.get("isMeta") or record.get("isCompactSummary") or _injected(record):
        return False
    content = record["message"].get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") in ("image", "document") for b in content):
        return True
    return bool(_users_words(_text(record)))


def is_dream_session(records: list[dict]) -> bool:
    """Whether /hstack:dream ran in this session before anything else was said in it, a reply or a message of
    the user's that got none (sent, then interrupted): everything in it is then the dream's. A session forked
    from a dream session holds the dream's copied start, so it is one too."""
    for record in _message_records(records):
        if record.get("isSidechain"):
            continue
        if _invokes_dream(record):
            return True
        if record["type"] == "assistant" or _from_the_user(record):
            return False
    return False


def talked_before_dream(records: list[dict]) -> bool:
    """Whether /hstack:dream was run in a session that already had a conversation."""
    return runs_dream(records) and not is_dream_session(records)


def _is_text_document(block) -> bool:
    source = block.get("source") if isinstance(block, dict) else None
    return block.get("type") == "document" and isinstance(source, dict) and source.get("type") == "text"


def _heavy(block) -> bool:
    """An image, or a document that is not text: rendered as text, either would be base64."""
    return isinstance(block, dict) and (
        block.get("type") == "image" or (block.get("type") == "document" and not _is_text_document(block))
    )


def _saved_file(named: str, session_dir: Path) -> Path | None:
    """The file a preview says the output was saved to. It is read only from a tool-results folder in this
    session's own folder, since a tool's output could imitate a preview naming any file, such as another
    project's saved output; failing that, the same file name in this session's tool-results folder (the log
    folder was moved, say). A fork's copied messages, whose previews name its parent's, are not exported."""
    own = session_dir.resolve()
    candidates = []
    try:
        candidates.append(Path(named).resolve())
        candidates.append((session_dir / "tool-results" / Path(named).name).resolve())
    except (OSError, ValueError):
        pass
    return next(
        (path for path in candidates if own in path.parents and path.parent.name == "tool-results" and path.is_file()),
        None,
    )


def _full_result(content, session_dir: Path, notes: dict):
    """A tool result's output as saved in full, and nothing heavy. notes counts the outputs whose saved file is
    gone or never was ("missing"), which keep their preview, and the ones saved only in part ("partial")."""
    if isinstance(content, str):
        match = PERSISTED_OUTPUT.match(content.lstrip())
        if match:
            saved_to = SAVED_TO.search(match["line"])
            saved = _saved_file(saved_to["path"], session_dir) if saved_to else None
            if saved is None:
                notes["missing"] += 1
                return content
            if "only the first" in match["line"]:
                notes["partial"] += 1
            return saved.read_text(encoding="utf-8", errors="replace")
        return content
    if isinstance(content, list):
        return [
            {**block, "text": _full_result(block["text"], session_dir, notes)}
            if isinstance(block, dict) and isinstance(block.get("text"), str)
            else block
            for block in content
            if not _heavy(block)
        ]
    return content


def _events(record: dict, session_dir: Path, notes: dict):
    """One message's content blocks as transcript events. Thinking, images, documents that are not text and any
    block type Claude Code adds later are left out, as compact_events leaves out what is not conversation."""
    content = record["message"].get("content")
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return
    message = "user.message" if record["type"] == "user" else "agent.message"
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and isinstance(block.get("text"), str):
            if record["type"] == "user" and _command_output(block["text"]):
                continue  # what Claude Code printed for a slash command, such as "Compacted": not the user's
            yield {"type": message, "content": block["text"]}
        elif _is_text_document(block) or kind == "search_result":
            yield {"type": message, "content": [block]}  # render_text writes their title and text
        elif kind == "tool_use":
            yield {
                "type": "agent.tool_use",
                "id": block.get("id"),
                "name": block.get("name"),
                "input": block.get("input"),
            }
        elif kind == "tool_result":
            yield {
                "type": "agent.tool_result",
                "tool_use_id": block.get("tool_use_id"),
                "content": _full_result(block.get("content"), session_dir, notes),
                "is_error": block.get("is_error"),
            }


def _injected(record: dict) -> bool:
    """Whether Claude Code put this message in the user's turn itself, such as the notice that a background
    agent finished. A record that names no origin (a tool result, a subagent's task, a log from a version
    before the field) counts as written for the conversation."""
    origin = record.get("origin")
    return record["type"] == "user" and isinstance(origin, dict) and origin.get("kind") not in (None, "human")


def _thread_events(records: list[dict], session_dir: Path, notes: dict, *, primary: bool) -> list[dict]:
    events = []
    for record in _message_records(records):
        # isMeta marks text Claude Code injected, such as a skill's body; isCompactSummary a summary its
        # model wrote of earlier messages; a non-human origin a notice Claude Code sent. None was written by
        # the user. (What it printed for a slash command is left out block by block, in _events.)
        if record.get("isMeta") or record.get("isCompactSummary") or _injected(record):
            continue
        if primary and record.get("isSidechain"):
            continue  # a subagent's record; subagents are threads of their own
        events.extend(_events(record, session_dir, notes))
    return events


def _meta(log: Path) -> dict:
    meta = log.with_suffix(".meta.json")
    try:
        loaded = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _time(record: dict) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(record.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _written(records: list[dict]) -> list[datetime | None]:
    """When each record was written, as far as the log tells: its own time, or the latest time of any message
    above it if that is later. A message typed while Claude worked carries the time it was typed but is logged
    only once delivered, after the messages Claude wrote meanwhile; a record cannot have been written before
    the lines above it. Only messages count as lines above: a fork's log opens with bookkeeping written when it
    was forked, then copies of its parent's messages, which keep their own, older times."""
    times, latest = [], None
    for record in records:
        moment = _time(record)
        if record.get("type") in ("user", "assistant") and moment is not None and (latest is None or moment > latest):
            latest = moment
        times.append(latest if latest is not None and (moment is None or latest > moment) else moment)
    return times


def _within(records: list[dict], window: tuple[datetime, datetime] | None) -> list[dict]:
    """The records without the messages written outside the window [since, until) (see _written): the ones
    before it belong to an earlier run's window, the ones after it to the next run's. A message with no time is
    kept."""
    if window is None:
        return records
    since, until = window
    return [
        r for r, t in zip(records, _written(records))
        if r.get("type") not in ("user", "assistant") or t is None or since <= t < until
    ]


def _subagent_threads(session_dir: Path, notes: dict, window: tuple[datetime, datetime] | None,
                      left_out: set[str]) -> list[dict]:
    """Each subagent's log in the session's folder as a sub-thread, earliest first, leaving out the dream's
    own agents and the ones launched by a tool call that is not this session's conversation now (left_out).
    Subagents log under subagents/, a workflow's agents in a folder of their own below it."""
    if not session_dir.is_dir():
        return []
    found = []
    for log in session_dir.rglob("agent-*.jsonl"):
        meta = _meta(log)
        if meta.get("agentType") in DREAM_AGENTS or meta.get("toolUseId") in left_out:
            continue
        if any(folder.name in left_out for folder in log.relative_to(session_dir).parents):
            continue  # an agent of a workflow whose launch was left out
        records = read_records(log)
        events = _thread_events(_within(records, window), session_dir, notes, primary=False)
        if events:
            found.append((created_at(records) or "", log, meta, events))
    return [
        {
            "id": log.stem.removeprefix("agent-"),
            "parent_thread_id": "primary",
            "agent_name": meta.get("agentType") or "subagent",
            "events": events,
        }
        for _, log, meta, events in sorted(found, key=lambda item: (item[0], str(item[1])))
    ]


def _note(notes: dict, continued_since: str | None) -> str | None:
    """The NOTE ABOUT THIS FILE line for a session continued from before the window and outputs not held in
    full, or None."""
    parts = []
    if continued_since:
        parts.append(
            f"this session began before {continued_since}; only what was said in it from then on is here"
        )
    if notes["missing"]:
        parts.append(
            f"{notes['missing']} tool output(s) of this session were not kept in full by Claude Code (the file it "
            "saved them to is gone, or it could not save them), so the transcript holds only their previews"
        )
    if notes["partial"]:
        parts.append(f"{notes['partial']} tool output(s) were saved by Claude Code only in part")
    return "; ".join(parts) + "." if parts else None


def _calls_left_out(records: list[dict], own: list[dict]) -> set[str]:
    """What names the agents launched by tool calls that own_records left out, such as on a branch the user
    rewound away from: the calls' ids, which a subagent's meta names, and the run ids their results give, which
    name a workflow's folder of agents."""
    kept = {id(r) for r in own}
    left_out = set()
    for r in _message_records(records):
        if id(r) in kept:
            continue
        content = r["message"].get("content") if r["type"] == "assistant" else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                left_out.add(block["id"])
        result = r.get("toolUseResult")
        if isinstance(result, dict) and isinstance(result.get("runId"), str):
            left_out.add(result["runId"])
    return left_out


def export(path: Path, records: list[dict] | None = None, window: tuple[datetime, datetime] | None = None) -> dict:
    """One session and its subagents as the dict render_text takes, holding only what was said in the window
    [since, until) when one is given. A fork's copies of its parent's messages and the branches the user
    rewound away from are left out (see own_records)."""
    records = read_records(path) if records is None else records
    own = own_records(path, records)
    taken = _within(own, window)
    continued = window is not None and any(
        r.get("type") in ("user", "assistant") and t is not None and t < window[0] for r, t in zip(own, _written(own))
    )
    session_dir = path.with_suffix("")
    custom_title = ai_title = ""
    for record in records:  # a name the user gave the session comes before the one Claude Code generated
        if record.get("type") == "custom-title":
            custom_title = record.get("customTitle") or custom_title
        elif record.get("type") == "ai-title":
            ai_title = record.get("aiTitle") or ai_title
    notes = {"missing": 0, "partial": 0}
    primary = {
        "id": "primary",
        "parent_thread_id": None,
        "agent_name": AGENT_NAME,
        "events": _thread_events(taken, session_dir, notes, primary=True),
    }
    exported = {
        "session": {"id": path.stem, "title": custom_title or ai_title, "created_at": created_at(own)},
        "agent": {"name": AGENT_NAME},
        "threads": [primary, *_subagent_threads(session_dir, notes, window, _calls_left_out(records, own))],
    }
    since = window[0].astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z") if window else None
    note = _note(notes, since if continued else None)
    if note:
        exported["export_note"] = note
    return exported


def has_conversation(exported: dict) -> bool:
    """Whether anything was said: a typed command with nothing after it, such as the /clear that opens the
    session /clear starts, is not a conversation."""
    for thread in exported["threads"]:
        for event in thread["events"]:
            content = event.get("content")
            if event["type"] == "user.message" and isinstance(content, str) and not _users_words(content):
                continue
            return True
    return False
