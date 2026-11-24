#!/usr/bin/env python3
"""Point Claude at the attack-the-premise principle when the same command keeps failing after fixes.

The principle applies when two or more fixes that rest on one premise have failed the same check. A hook cannot
see premises, but it can see the check: a shell command that exits non-zero. A check is the exact command text,
trimmed, run in the same working directory. For each check, this hook counts the failures that follow an earlier
failure of it with at least one file edit in between. Each one is a fix that did not work. On the second, it adds
a note next to the failed result asking Claude to read the principle if the failure has not moved. These don't
count: the first failure, which only reproduces the problem, and reruns with no edit in between. A success resets
the count, and so does each note, so the next note needs two more failed fixes.

hooks.json runs it on:
- PostToolUse for Edit, Write, MultiEdit and NotebookEdit: a file changed.
- PostToolUse for Bash: the command succeeded. Ignored when it hasn't finished or didn't plainly pass: started
  in the background, moved there at its timeout, interrupted, or an exit 1 that Claude Code reads as a normal
  result (grep, diff, test and a few others).
- PostToolUseFailure for Bash: the command failed. Only a failure whose first line is "Exit code N" counts.
  Interrupts are ignored, and so is a failure carrying Claude Code's own timeout notice: a whole line such as
  "Command timed out after 60s" or "Command timed out after 2m 0s". A test's own timeout message still counts.

A piped command such as `pytest | tail` exits with the last program's status, so its failures are invisible here.

Each agent, the main one and every subagent, keeps its own count in an append-only log in the plugin's data
folder, one file per session and agent. The count is rebuilt from the log on each failure, so concurrent hook
runs only ever append. Logs untouched for a week are deleted when a new one starts. The hook never blocks a tool,
and on input it does not understand it prints nothing.
"""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
EXIT_CODE = re.compile(r"Exit code [1-9][0-9]*\s*(\n|$)")
TIMED_OUT = re.compile(r"^Command timed out after (?:[0-9]+m )?[0-9]+(?:\.[0-9]+)?s[ \t]*$", re.MULTILINE)
NOT_A_PLAIN_PASS = ("backgroundTaskId", "interrupted", "returnCodeInterpretation")
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
FAILED_FIXES_FOR_A_NOTE = 2
KEEP_LOGS_SECONDS = 7 * 24 * 3600
PRINCIPLE = Path(__file__).resolve().parents[1] / "skills" / "principle-attack-the-premise" / "SKILL.md"


def failed_fixes(events: list[dict], key: str) -> int:
    """Failed fixes of the command `key` since its last success or note."""
    last_edit = last_run = -1
    failed_before = False
    fixes = 0
    for i, event in enumerate(events):
        kind = event.get("kind")
        if kind == "edit":
            last_edit = i
        elif event.get("key") != key:
            continue
        elif kind == "ok":
            failed_before, fixes, last_run = False, 0, i
        elif kind == "fail":
            if failed_before and last_edit > last_run:
                fixes += 1
            failed_before, last_run = True, i
        elif kind == "note":
            fixes = 0
    return fixes


def _log_path(data: dict) -> Path | None:
    """This session's and agent's log, deleting week-old logs when it starts a new one. None if it can't."""
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    session = data.get("session_id")
    agent = data.get("agent_id") or "main"
    if not base or not isinstance(session, str) or not isinstance(agent, str):
        return None
    if not SAFE_ID.fullmatch(session) or not SAFE_ID.fullmatch(agent):
        return None
    folder = Path(base) / "premise-cue"
    log = folder / f"{session}.{agent}.jsonl"
    if log.exists():
        return log
    try:
        folder.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - KEEP_LOGS_SECONDS
        for old in folder.glob("*.jsonl"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink(missing_ok=True)
            except FileNotFoundError:
                continue  # another hook run deleted it first
    except OSError:
        return None
    return log


def _append(log: Path, event: dict) -> None:
    """One write of one short line with O_APPEND, so concurrent hook runs don't interleave."""
    fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(event) + "\n").encode())
    finally:
        os.close(fd)


def _read(log: Path) -> list[dict]:
    events = []
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _note(command: str) -> str:
    shown = command.strip().splitlines()[0]
    if len(shown) > 100 or "\n" in command.strip():
        shown = shown[:100] + "…"
    return (
        f"hstack: `{shown}` has failed again after a second round of edits meant to fix it. If it is still the "
        "same failure and both fixes rested on the same assumption, don't write a third fix yet: Read "
        f"{PRINCIPLE} in full and follow it, writing the assumption down and testing it directly first. If the "
        "failure has moved on, such as a different test or error, your fixes worked: carry on."
    )


def handle(data: dict) -> str | None:
    """Record the event, and return the note to add if this failure is the second failed fix."""
    event, tool = data.get("hook_event_name"), data.get("tool_name")
    given = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    if event == "PostToolUse" and tool in EDIT_TOOLS:
        record = {"kind": "edit"}
    elif tool == "Bash" and isinstance(given.get("command"), str) and given["command"].strip():
        cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else ""
        key = hashlib.sha256(f"{cwd}\0{given['command'].strip()}".encode()).hexdigest()[:16]
        response = data.get("tool_response") if isinstance(data.get("tool_response"), dict) else {}
        error = data.get("error")
        if event == "PostToolUse" and not given.get("run_in_background") \
                and not any(response.get(field) for field in NOT_A_PLAIN_PASS):
            record = {"kind": "ok", "key": key}
        elif event == "PostToolUseFailure" and not data.get("is_interrupt") and isinstance(error, str) \
                and EXIT_CODE.match(error) and not TIMED_OUT.search(error):
            record = {"kind": "fail", "key": key}
        else:
            return None
    else:
        return None

    log = _log_path(data)
    if log is None:
        return None
    try:
        _append(log, record)
        if record["kind"] != "fail" or failed_fixes(_read(log), record["key"]) < FAILED_FIXES_FOR_A_NOTE:
            return None
        _append(log, {"kind": "note", "key": record["key"]})
    except OSError:
        return None
    return _note(given["command"])


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    note = handle(data)
    if note:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUseFailure", "additionalContext": note}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
