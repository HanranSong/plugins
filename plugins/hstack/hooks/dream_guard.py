#!/usr/bin/env python3
"""Keep /hstack:dream's agents inside the run's transcripts and the project's memory directory.

This stands in for the cookbook's sandbox. Managed Agents mounted the transcripts read-only and the memory
store read-write, and nothing else. A Claude Code subagent works on the real files, so this hook checks
each file tool call /hstack:dream's agents make:

- Read, Glob and Grep: only inside .dreaming/memory/ or the current run's transcripts (the run state.json
  names: the cookbook mounted only the files uploaded for that run).
- Write and Edit: only Markdown files inside .dreaming/memory/, never one named as an instruction file
  (CLAUDE.md, AGENTS.md...), which Claude Code would load from that folder as instructions.
- Nothing at all if .dreaming/memory/ or .dreaming/state/ is a link to another folder, which would widen both.

An allowed call is approved here, so the workflow does not stop for permission prompts; your own deny and ask
rules still apply. Every other agent's calls are left alone: the hook prints nothing for them.
Each decision about a dream agent's call is logged, without file contents, to .dreaming/state/guard.log.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DREAM_AGENTS = {"hstack:transcript-analyzer", "hstack:memory-organizer"}
READ_TOOLS = {"Read", "Glob", "Grep"}
WRITE_TOOLS = {"Write", "Edit"}
INSTRUCTION_FILES = {"claude.md", "claude.local.md", "agents.md", "agents.override.md"}


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _current_transcripts(state_dir: Path) -> Path | None:
    """The transcripts folder of the run prepare last started, as state.json names it; None when no run is on."""
    try:
        run = json.loads((state_dir / "state.json").read_text(encoding="utf-8")).get("current_run")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(run, str) or not run or not run.replace("-", "").replace("_", "").isalnum():
        return None
    return state_dir / "runs" / run / "transcripts"


def decide(data: dict, project: Path) -> tuple[str, str] | None:
    """("allow" or "deny", why) for a dream agent's call; None for any other agent's. project is resolved."""
    if data.get("agent_type") not in DREAM_AGENTS:
        return None
    tool = data.get("tool_name")
    given = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    memory = project / ".dreaming" / "memory"
    state_dir = project / ".dreaming" / "state"
    if memory.resolve() != memory or state_dir.resolve() != state_dir:
        return "deny", "The memory or state folder is a link to another folder, so /hstack:dream's agents may not use it."
    transcripts = _current_transcripts(state_dir)
    where = f"{memory}" + (f" or this run's transcripts, {transcripts}" if transcripts else "")

    if tool in WRITE_TOOLS:
        raw = given.get("file_path")
        if not isinstance(raw, str) or not raw:
            return "deny", "No file_path given."
        path = Path(raw).resolve()
        if not _inside(path, memory) or path == memory:
            return "deny", f"/hstack:dream's agents write only inside the memory directory, {memory}."
        if path.suffix != ".md":
            return "deny", "Memory files are Markdown: their names end in .md."
        if path.name.lower() in INSTRUCTION_FILES:
            return "deny", f"A memory file cannot be named {path.name}: Claude Code would load it as instructions."
        return "allow", "inside the memory directory"

    if tool in READ_TOOLS:
        raw = given.get("file_path") if tool == "Read" else given.get("path")
        if not isinstance(raw, str) or not raw:
            return "deny", f"Give an absolute path inside {where}."
        path = Path(raw).resolve()
        if not (_inside(path, memory) or (transcripts and _inside(path, transcripts))):
            return "deny", f"/hstack:dream's agents read only inside {where}."
        # A file pattern must not climb out of that folder: Glob's pattern, Grep's glob (Grep's own pattern
        # is a regular expression over file contents, not a path).
        key = {"Glob": "pattern", "Grep": "glob"}.get(tool)
        value = given.get(key) if key else None
        if isinstance(value, str) and (value.startswith(("/", "~")) or ".." in Path(value).parts):
            return "deny", f"Keep the {key} relative to the path, without '..'."
        return "allow", "inside the memory directory or the transcripts"

    return "deny", "/hstack:dream's agents use only Read, Write, Edit, Glob and Grep."


def _log(project: Path, data: dict, decision: tuple[str, str]) -> None:
    log = project / ".dreaming" / "state" / "guard.log"
    given = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),  # as precise as a run's start
        "agent_type": data.get("agent_type"),
        "agent_id": data.get("agent_id"),
        "tool": data.get("tool_name"),
        "target": given.get("file_path") or given.get("path"),
        "decision": decision[0],
        "why": decision[1],
    }
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as out:
            out.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict) or data.get("agent_type") not in DREAM_AGENTS:
        return 0  # not a dream agent: say nothing, so the call goes on as it would without this plugin
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".")
    try:
        project = project.resolve()
        decision = decide(data, project)
    except Exception as error:  # a check that breaks refuses the call: a crashed hook would let it through
        decision = ("deny", f"The guard could not check this call ({type(error).__name__}: {error}).")
    try:
        _log(project, data, decision)
    except Exception:  # a log that cannot be written must never keep the decision from being given
        pass
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision[0],
                    "permissionDecisionReason": decision[1],
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
