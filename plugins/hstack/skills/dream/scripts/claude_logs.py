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
- A tool output Claude Code saved to a file is read back, as a Managed Agents export would hold it. When the
  file is gone, or Claude Code saved only part of the output, the transcript says so in a NOTE ABOUT THIS FILE
  line.
- The cookbook picks one agent's sessions by the agent's id. Here a project's sessions are the logs Claude Code
  keeps for its folder, started in that folder, each under its own session id; the ones /hstack:dream ran in
  are told apart.
"""

import json
import os
import re
import unicodedata
from pathlib import Path

# The command a dream session starts with, and the agents /hstack:dream's workflow runs. Their logs are never
# part of a transcript.
DREAM_COMMAND = ("hstack:dream", "/hstack:dream")  # as Claude Code logs it: command-message, command-name
DREAM_AGENTS = {"hstack:transcript-analyzer", "hstack:memory-organizer"}
AGENT_NAME = "Claude Code"
# How Claude Code logs a slash command the user typed: a user message made of these tags only.
COMMAND_TAG = re.compile(r"<command-(message|name|args)>(.*?)</command-\1>", re.DOTALL)
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
                records.append(record)
    return records


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
    """When the session started, as a UTC ISO timestamp: its first message's time. A forked session opens with
    copies of the messages it was forked from, marked forkedFrom and keeping their old times: it starts at its
    first message that is not a copy, and has no start (nothing of its own to dream over) until it has one."""
    own = (r for r in _message_records(records) if not r.get("forkedFrom"))
    return next((r["timestamp"] for r in own if isinstance(r.get("timestamp"), str) and r["timestamp"]), None)


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


def is_dream_session(records: list[dict]) -> bool:
    """Whether /hstack:dream ran in this session before any reply: everything in it is then the dream's. A
    session forked from a dream session holds the dream's copied start, so it is one too."""
    for record in _message_records(records):
        if record.get("isSidechain"):
            continue
        if record["type"] == "assistant":
            return False
        if _invokes_dream(record):
            return True
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
    """The file a preview says the output was saved to. It is read only from a tool-results folder of Claude
    Code's own logs (a forked session's previews name its parent's), since a tool's output could imitate a
    preview naming any file; failing that, the same file name in this session's tool-results folder."""
    projects = (claude_home() / "projects").resolve()
    try:
        path = Path(named).resolve()
    except (OSError, ValueError):
        path = None
    if path and projects in path.parents and path.parent.name == "tool-results" and path.is_file():
        return path
    own = session_dir / "tool-results" / Path(named).name
    return own if own.is_file() else None


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
        # the user.
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


def _subagent_threads(session_dir: Path, notes: dict) -> list[dict]:
    """Each subagent's log in the session's folder as a sub-thread, earliest first, leaving out the dream's
    own agents. Subagents log under subagents/, a workflow's agents in a folder of their own below it."""
    if not session_dir.is_dir():
        return []
    found = []
    for log in session_dir.rglob("agent-*.jsonl"):
        meta = _meta(log)
        if meta.get("agentType") in DREAM_AGENTS:
            continue
        records = read_records(log)
        events = _thread_events(records, session_dir, notes, primary=False)
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


def _note(notes: dict) -> str | None:
    """The NOTE ABOUT THIS FILE line for outputs not held in full, or None."""
    parts = []
    if notes["missing"]:
        parts.append(
            f"{notes['missing']} tool output(s) of this session were not kept in full by Claude Code (the file it "
            "saved them to is gone, or it could not save them), so the transcript holds only their previews"
        )
    if notes["partial"]:
        parts.append(f"{notes['partial']} tool output(s) were saved by Claude Code only in part")
    return "; ".join(parts) + "." if parts else None


def export(path: Path, records: list[dict] | None = None) -> dict:
    """One session and its subagents as the dict render_text takes."""
    records = read_records(path) if records is None else records
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
        "events": _thread_events(records, session_dir, notes, primary=True),
    }
    exported = {
        "session": {"id": path.stem, "title": custom_title or ai_title, "created_at": created_at(records)},
        "agent": {"name": AGENT_NAME},
        "threads": [primary, *_subagent_threads(session_dir, notes)],
    }
    note = _note(notes)
    if note:
        exported["export_note"] = note
    return exported


def has_conversation(exported: dict) -> bool:
    return any(thread["events"] for thread in exported["threads"])
