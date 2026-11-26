#!/usr/bin/env python3
"""The steps around /hstack:dream's workflow. Each prints one JSON object.

prepare  Takes what was said in this project's Claude Code sessions since the last complete run started (the
         last 14 days on the first run): new sessions, and the new part of continued ones. It leaves out dream
         sessions, writes one transcript file per session, copies memory, and prints the workflow's args.
finish   Reads the workflow's report from what Claude Code saved of the workflow run (or, failing that, from
         --report), deletes the organizer's "MOVED TO" placeholder files, writes the run's report, and moves
         the window on only if every transcript was analyzed and the organizer finished. A run that is not
         complete keeps its window, so the next run takes the same sessions. Only the latest runs keep their
         transcripts and their copy of memory.
show     Prints one session's transcript as the analyzers would read it. It writes nothing.

Everything is kept in the project's .dreaming/ folder: memory/ is what sessions read, state/ is this
script's (state.json, and runs/<run>/ with the transcripts, the copy of memory and the report).
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import claude_logs
from render import render_text

GROUP_SIZE = 20  # the cookbook's work request: "Work through the transcripts in groups of about 20"
FIRST_WINDOW = timedelta(days=14)  # the notebook's default when no COOKBOOK_SINCE is given
# The cookbook's: the one-line file the organizer leaves where it merged or renamed a file, and how big such
# a file can be.
STUB = re.compile(r"MOVED TO \[\[([^\]\n]+)\]\]\.?")
STUB_MAX_BYTES = 300
INDEX_ENTRY = re.compile(r"\s*(?:[-*+]\s+|\d+[.)]\s+)?\[\[")  # the cookbook's index_entries
SESSION_ID = re.compile(r"[0-9a-fA-F-]{36}")
# The latest runs keep their transcripts and their copy of memory; older ones keep only their small files. The
# transcripts are full copies of every session, and a session that searches the project could read them back.
RUNS_KEPT_WHOLE = 5
RECENT_SECONDS = 600  # how recently a dream session's log changed for it to count as the one running now


class Paths:
    def __init__(self, project: Path):
        self.project = project
        self.data = project / ".dreaming"
        self.memory = self.data / "memory"
        self.state_dir = self.data / "state"
        self.state = self.state_dir / "state.json"
        self.runs = self.state_dir / "runs"


def _iso(moment: datetime) -> str:
    """A UTC ISO timestamp to the millisecond, as Claude Code times messages: a window cut to the second would
    take again a session that started in the same second as the run before it."""
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_time(text) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _load_state(paths: Paths) -> dict:
    state = json.loads(paths.state.read_text(encoding="utf-8")) if paths.state.is_file() else {}
    state.setdefault("last_run", None)  # when the last complete run started: the next window's start
    state.setdefault("dream_sessions", [])  # the sessions /hstack:dream ran in, never analyzed
    state.setdefault("current_run", None)  # the prepared run whose transcripts the agents may read
    return state


def _save_state(paths: Paths, state: dict) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    temporary = paths.state.with_suffix(".tmp")  # a run killed mid-write cannot leave a broken file
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(paths.state)


def _linked_folder(paths: Paths) -> dict | None:
    """An error if .dreaming/ or one of its folders is a link to another folder. The hook refuses the agents
    everything then, and prepare and finish would change the linked folder's files and permissions."""
    for folder in (paths.data, paths.memory, paths.state_dir, paths.runs):
        if folder.is_symlink():
            return {
                "status": "error",
                "message": f"{folder} is a link to another folder. /hstack:dream works only on real folders inside "
                "the project: replace the link with a folder, then run it again.",
            }
    return None


def _private_dir(path: Path) -> Path:
    """A folder only you can open, as the cookbook keeps its records (mode 0700)."""
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _private_write(path: Path, text: str) -> None:
    """A file only you can read (mode 0600), as the cookbook writes its transcripts. A lone surrogate, such as
    half an emoji cut off in a tool's output, is written escaped instead of stopping the run."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(descriptor, "w", encoding="utf-8", errors="backslashreplace") as out:
        out.write(text)


def _set_up(paths: Paths) -> None:
    paths.memory.mkdir(parents=True, exist_ok=True)
    _private_dir(paths.state_dir)
    _private_dir(paths.runs)
    # The run state holds full copies of every session's transcript, which must never reach the project's git by
    # accident. Memory is left to the project: committing it is how its changes get a history.
    ignore = paths.state_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# Written by /hstack:dream: full copies of your sessions are kept here.\n*\n")


def _new_run(paths: Paths, started: datetime) -> Path:
    run_id = started.strftime("%Y%m%d-%H%M%S")
    run_dir, suffix = paths.runs / run_id, 1
    while run_dir.exists():
        suffix += 1
        run_dir = paths.runs / f"{run_id}-{suffix}"
    _private_dir(run_dir)
    _private_dir(run_dir / "transcripts")
    return run_dir


def _current_session(project: Path, logs: list[Path], given: str | None, started: datetime) -> str | None:
    """The session /hstack:dream is running in: the id the skill passed, or else the project's session that ran
    /hstack:dream whose log changed most recently, if it changed in the last few minutes."""
    if given and SESSION_ID.fullmatch(given):
        return given
    recent = []
    for log in logs:
        if started.timestamp() - log.stat().st_mtime < RECENT_SECONDS:
            records = claude_logs.read_records(log)
            if claude_logs.belongs_to(records, project) and claude_logs.runs_dream(records):
                recent.append(log)
    return max(recent, key=lambda log: log.stat().st_mtime).stem if recent else None


def prepare(project: Path, session: str | None = None, dry_run: bool = False, started: datetime | None = None) -> dict:
    paths = Paths(project)
    started = started or datetime.now(timezone.utc)
    started = started.replace(microsecond=started.microsecond // 1000 * 1000)  # as precisely as it is saved
    if not dry_run and (linked := _linked_folder(paths)):
        return linked
    logs = claude_logs.session_logs(project)
    if not logs and not dry_run:
        return {
            "status": "nothing",
            "message": f"No Claude Code session was started in {project}, so there is nothing to dream over. "
            "Run /hstack:dream from the folder you start Claude Code in.",
        }

    current = None if dry_run else _current_session(project, logs, session, started)
    if current:
        log = claude_logs.find_log(project, current)
        if log and claude_logs.talked_before_dream(claude_logs.read_records(log)):
            return {
                "status": "refused",
                "message": "This session already has a conversation, and a dream session is never analyzed, so "
                "running /hstack:dream here would keep what you did in it out of memory for good. Run /clear, "
                "then /hstack:dream, or start a new Claude Code session for it.",
            }
    if dry_run:
        return _take(project, paths, _load_state(paths), logs, None, started, dry_run=True)

    state = _load_state(paths)
    if current and current not in state["dream_sessions"]:
        state["dream_sessions"].append(current)
    _set_up(paths)
    return _take(project, paths, state, logs, current, started, dry_run=False)


def _take(project: Path, paths: Paths, state: dict, logs: list[Path], current: str | None, started: datetime, *,
          dry_run: bool) -> dict:
    """What was said in the project's sessions from the window's start up to this run's start, one transcript
    per session: new sessions, and the new part of sessions that were continued. The run's start is the next
    window's start, so each message belongs to exactly one window, and a message written at that moment, or
    while this run runs, is the next run's."""
    since = _parse_time(state["last_run"]) or started - FIRST_WINDOW
    if not dry_run:
        # The first window's start is kept from the first run on, as the cookbook's nightly script keeps a failed
        # night's: a run that fails must not let the 14 days slide past the oldest sessions.
        state["last_run"] = state["last_run"] or _iso(since)
        state["current_run"] = None  # a run abandoned before its finish is closed to the agents now
        _save_state(paths, state)  # the dream session is recorded first, so even a run that dies here skips it

    # First, which logs in the window are the project's, and which of those are dreams, known by content. Every
    # dream is known before any log is taken, so no log carrying a dream's session id gets through.
    dream = set(state["dream_sessions"])
    candidates, skipped = [], []
    for log in logs:
        written = [log.stat().st_mtime, *(p.stat().st_mtime for p in log.with_suffix("").rglob("agent-*.jsonl"))]
        if max(written) < since.timestamp():
            continue  # neither it nor its subagents' logs changed since the window began: not read at all
        records = claude_logs.read_records(log)
        if not claude_logs.belongs_to(records, project):
            continue  # another folder whose name maps to the same log folder
        if not claude_logs.is_own_log(log, records):
            skipped.append({"id": log.stem, "reason": "not a session's own log: its records carry other sessions' ids"})
            continue
        if claude_logs.is_dream_session(records):
            dream.add(log.stem)
        candidates.append((log, claude_logs.session_ids(records)))
    newly_dream = sorted(dream - set(state["dream_sessions"]))

    # Then each session is exported, rendered and written in turn, so only one is held in memory at a time.
    sessions, inputs, run_dir = [], [], None
    for log, ids in candidates:
        if log.stem in dream or ids & dream:
            skipped.append({"id": log.stem, "reason": "a /hstack:dream session"})
            continue
        records = claude_logs.read_records(log)
        exported = claude_logs.export(log, records, window=(since, started))
        if not any(thread["events"] for thread in exported["threads"]):
            continue  # nothing said in the window: an earlier run took it all, or it is the next run's
        if not claude_logs.has_conversation(exported):
            skipped.append({"id": log.stem, "reason": "no conversation"})
            continue
        text = render_text(exported)
        info = {"id": log.stem, "title": exported["session"]["title"], "created": exported["session"]["created_at"]}
        if exported.get("export_note"):
            info["note"] = exported["export_note"]
        if dry_run:
            info["transcript_chars"] = len(text)
        else:
            run_dir = run_dir or _new_run(paths, started)
            path = run_dir / "transcripts" / f"{log.stem}.txt"
            _private_write(path, text)
            inputs.append(str(path))
        sessions.append(info)

    if dry_run:
        return {
            "status": "dry run",
            "message": f"{len(sessions)} session(s) with messages since {_iso(since)} would be analyzed. Nothing was "
            "written.",
            "sessions": sessions,
            "skipped": skipped,
        }

    state["dream_sessions"].extend(newly_dream)
    if not sessions:
        state["last_run"] = _iso(started)  # as the cookbook's nightly script does after a night with nothing
        _save_state(paths, state)
        return {
            "status": "nothing",
            "message": f"No session has messages since {_iso(since)} left to dream over.",
            "skipped": skipped,
        }

    # The cookbook saves the store's files first. Links are copied as links, so a broken one cannot stop the run.
    shutil.copytree(paths.memory, run_dir / "memory_before", symlinks=True)
    run = {
        "id": run_dir.name,
        "project": str(project),
        "session": current,
        "started": _iso(started),
        "since": _iso(since),
        "sessions": sessions,
        "inputs": inputs,
        "skipped": skipped,
        "finished": None,
    }
    (run_dir / "run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    state["current_run"] = run_dir.name  # the only run whose transcripts the agents may read (the hook checks)
    _save_state(paths, state)
    _prune_runs(paths)  # also here, so that runs which never finished are pruned too
    return {
        "status": "ready",
        "run_id": run_dir.name,
        "message": f"{len(inputs)} session(s) with messages since {_iso(since)} to dream over.",
        "sessions": sessions,
        "skipped": skipped,
        "workflow_args": {"memory_dir": str(paths.memory), "transcripts": inputs, "group_size": GROUP_SIZE},
    }


def _prune_runs(paths: Paths) -> None:
    """Delete the transcripts and the copy of memory of every run but the latest RUNS_KEPT_WHOLE."""
    runs = sorted(path for path in paths.runs.iterdir() if path.is_dir())  # named by start time: oldest first
    for old in runs[:-RUNS_KEPT_WHOLE]:
        for part in ("transcripts", "memory_before"):
            shutil.rmtree(old / part, ignore_errors=True)


def _slugs_for(relative: Path) -> set[str]:
    """The cookbook's slugs_for: a file answers to its name without .md and to its path joined by hyphens."""
    stem = relative.with_suffix("")
    return {stem.name, "-".join(stem.parts)}


def _organizer_writes(paths: Paths, since: str) -> set[Path]:
    """The files the organizer wrote or edited since the run started, as the hook logged them. The cookbook
    deletes only the placeholders its own session wrote during the organizer's run; anything else in memory,
    whoever wrote it, is not this run's to delete."""
    start, written = _parse_time(since), set()
    try:
        lines = (paths.state_dir / "guard.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return written
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(entry, dict)
            and entry.get("agent_type") == "hstack:memory-organizer"
            and entry.get("tool") in ("Write", "Edit")
            and entry.get("decision") == "allow"
            and isinstance(entry.get("target"), str)
            and (when := _parse_time(entry.get("time"))) is not None
            and start is not None
            and when >= start
        ):
            written.add(Path(entry["target"]).resolve())
    return written


def _sweep_stubs(memory: Path, written: set[Path]) -> tuple[list, list]:
    """Delete the placeholder files the organizer wrote whose target exists, as the cookbook's sweep_stubs
    does. Placeholders it did not write are left alone."""
    files = sorted(path for path in memory.rglob("*") if path.is_file() and not path.is_symlink())
    answers = [(path, _slugs_for(path.relative_to(memory))) for path in files]
    removed, left = [], []
    for path in files:
        if path.resolve() not in written or path.stat().st_size > STUB_MAX_BYTES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        match = STUB.fullmatch(text.strip())
        if not match:
            continue
        target = match.group(1).strip()
        relative = str(path.relative_to(memory))
        if not any(target in slugs and other != path for other, slugs in answers):
            left.append({"file": relative, "target": target, "why": "missing target"})
        elif _unlink_if_unchanged(path, text):
            removed.append({"file": relative, "target": target})
    return removed, left


def _unlink_if_unchanged(path: Path, text: str) -> bool:
    """Delete a placeholder only if it still holds what was read, as the cookbook's delete carries the content's
    hash. It is first moved aside, so that a file saved at its path from then on is a new file, which stays.
    What was moved aside is deleted only if it is the placeholder; otherwise it goes back, unless a new file has
    taken its place, in which case it stays beside it as a hidden .removing file."""
    aside = path.with_name(f".{path.name}.removing")
    try:
        os.rename(path, aside)
    except OSError:
        return False
    try:
        same = aside.read_text(encoding="utf-8", errors="replace") == text
    except OSError:
        same = False
    if same:
        aside.unlink()
        return True
    try:
        os.link(aside, path)  # creates path only if nothing is there now
        aside.unlink()
    except OSError:
        pass
    return False


def _index_entries(memory: Path) -> int | None:
    index = memory / "_index.md"
    if not index.is_file():
        return None
    return sum(1 for line in index.read_text(encoding="utf-8", errors="replace").splitlines() if INDEX_ENTRY.match(line))


def _saved_report(project: Path, inputs: list[str]) -> dict | None:
    """The report the workflow returned, as Claude Code saved it: each workflow run's args and result are in a
    workflows/wf_<id>.json file in its session's folder. That file is not documented, so anything unexpected in
    it counts as no saved report, and the report Claude passes in is used instead."""
    found = []
    for path in (path for folder in claude_logs.log_dirs(project) for path in folder.glob("*/workflows/wf_*.json")):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            mtime = path.stat().st_mtime
        except (OSError, ValueError):
            continue
        args = saved.get("args") if isinstance(saved, dict) else None
        result = saved.get("result") if isinstance(saved, dict) else None
        if (
            isinstance(args, dict)
            and args.get("transcripts") == inputs
            and isinstance(result, dict)
            and isinstance(result.get("report"), dict)
        ):
            found.append((mtime, result["report"]))
    return max(found, key=lambda item: item[0])[1] if found else None


def _passed_report(report_text: str) -> tuple[dict | None, list[str]]:
    """The report Claude passed in, and what was wrong with it."""
    try:
        report = json.loads(report_text)
    except json.JSONDecodeError as error:
        return None, [f"the workflow's report could not be read ({error})"]
    if isinstance(report, dict) and isinstance(report.get("report"), dict):
        report = report["report"]  # the workflow's whole return value was passed
    if not isinstance(report, dict):
        return None, ["the workflow's report is not a JSON object"]
    return report, []


def finish(
    project: Path,
    run_id: str,
    report_text: str | None = None,
    *,
    no_report: bool = False,
    finished: datetime | None = None,
) -> dict:
    paths = Paths(project)
    if linked := _linked_folder(paths):
        return linked
    run_dir = paths.runs / run_id
    if not re.fullmatch(r"[\w-]+", run_id) or not (run_dir / "run.json").is_file():
        return {"status": "error", "message": f"There is no prepared run {run_id!r} in {paths.runs}."}
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    finished = finished or datetime.now(timezone.utc)

    # The workflow's own result, as Claude Code saved it, comes first: it is not copied by a model.
    report, problems, source, note = _saved_report(project, run["inputs"]), [], None, None
    passed = report_text if report_text and report_text.strip() else None
    if report is not None:
        source = "Claude Code's saved result of the workflow run"
        if passed and _passed_report(passed)[0] not in (None, report):
            note = "The report passed in differed from the saved result; the saved result was used."
    elif passed:
        report, problems = _passed_report(passed)
        source = "passed in by Claude"
    elif no_report:
        problems.append("the workflow returned no report: it failed or was stopped")
    else:
        return {
            "status": "report needed",
            "message": "Claude Code's saved result of this run's workflow was not found. Run finish again with the "
            "workflow's report on standard input (--report -), or with --no-report if it returned none.",
        }

    inputs = run["inputs"]
    failures = []
    if report is not None:
        failures = [f for f in report.get("failures") or [] if isinstance(f, dict)]
        failed = {f.get("input") for f in failures}
        if report.get("inputs_expected") != len(inputs):
            problems.append(f"the workflow expected {report.get('inputs_expected')} transcripts, not {len(inputs)}")
        if failures:
            problems.append(
                f"{len(failures)} of {len(inputs)} transcript(s) were not analyzed: "
                + "; ".join(f"{Path(str(f.get('input'))).name}: {f.get('reason')}" for f in failures)
            )
        if failed - set(inputs):
            problems.append("the report names transcripts this run did not prepare")
        if report.get("inputs_processed") != len(inputs) - len(failed & set(inputs)):
            problems.append(
                f"the report counts {report.get('inputs_processed')} transcript(s) processed, which does not "
                "match its failures"
            )
        if not report.get("organized"):
            reason = report.get("organizer_error")
            problems.append("the organizer did not finish" + (f": {reason}" if reason else ""))

    removed, left = ([], [])
    if report is not None and report.get("organized"):
        removed, left = _sweep_stubs(paths.memory, _organizer_writes(paths, run["started"]))
    for stub in left:
        problems.append(
            f"the placeholder file {stub['file']} points to [[{stub['target']}]], which does not exist, so what it "
            f"held may be lost: restore it from {run_dir / 'memory_before'}"
        )
    entries = _index_entries(paths.memory)
    complete = not problems

    state = _load_state(paths)
    if complete:
        state["last_run"] = run["started"]
    if state.get("current_run") == run["id"]:
        state["current_run"] = None  # its agents are done: no transcript is open to them any more
    _save_state(paths, state)
    run.update(
        finished=_iso(finished),
        complete=complete,
        problems=problems,
        stubs_removed=removed,
        index_entries=entries,
        report_source=source,
        report_note=note,
    )
    (run_dir / "run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    if report is not None:
        (run_dir / "workflow_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _write_report(run_dir, run, report, failures)
    _prune_runs(paths)

    if complete:
        message = (
            f"Dreamed over {len(inputs)} session(s). Memory is in {paths.memory}; _index.md lists "
            f"{entries if entries is not None else 'no'} file(s). The next run takes what was said since "
            f"{run['started']}."
        )
    else:
        message = (
            f"The run is not complete, so the next run takes the same messages again (said since "
            f"{run['since']}). Memory as it was before this run is in {run_dir / 'memory_before'}."
        )
    return {
        "status": "complete" if complete else "incomplete",
        "message": message,
        "problems": problems,
        "placeholders_deleted": len(removed),
        "index_entries": entries,
        "report": str(run_dir / "report.md"),
    }


def _write_report(run_dir: Path, run: dict, report: dict | None, failures: list) -> None:
    lines = [
        f"# Dream run {run['id']}",
        "",
        f"- Project: {run['project']}",
        f"- Started: {run['started']}; finished: {run['finished']}",
        f"- Sessions with messages since {run['since']}: {len(run['inputs'])} analyzed",
        f"- Result: {'complete' if run['complete'] else 'not complete: the next run takes the same sessions again'}",
        f"- _index.md lists {run['index_entries'] if run['index_entries'] is not None else 'no'} file(s)",
        f"- Placeholder files deleted: {len(run['stubs_removed'])}",
        f"- The workflow's report: {run['report_source'] or 'none'}",
        *([f"- {run['report_note']}"] if run["report_note"] else []),
        "- Also in this folder: run.json, workflow_report.json and, for the latest "
        f"{RUNS_KEPT_WHOLE} runs only, transcripts/ (what the analyzers read) and memory_before/ (memory as it was "
        "before this run)",
        "",
        "## Sessions",
        "",
        *(
            f"- {s['id']}: {' '.join((s.get('title') or '').split()) or '(no title)'}"
            + (f" ({s['note']})" if s.get("note") else "")
            for s in run["sessions"]
        ),
    ]
    if run["problems"]:
        lines += ["", "## Problems", "", *(f"- {p}" for p in run["problems"])]
    if failures:
        lines += ["", "## Not analyzed", "", *(f"- {f.get('input')}: {f.get('reason')}" for f in failures)]
    if report is not None:
        changes = [c for c in report.get("changes") or [] if isinstance(c, str)]
        lines += ["", "## Organizer changes", "", *(f"- {c}" for c in changes)] if changes else []
    if run["stubs_removed"]:
        lines += ["", "## Placeholder files deleted", ""]
        lines += [f"- {s['file']} (moved to [[{s['target']}]])" for s in run["stubs_removed"]]
    if run["skipped"]:
        lines += ["", "## Left out", "", *(f"- {s['id']}: {s['reason']}" for s in run["skipped"])]
    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8", errors="backslashreplace")


def show(project: Path, session: str) -> str:
    log = claude_logs.find_log(project, session) if SESSION_ID.fullmatch(session) else None
    if log is None:
        raise SystemExit(f"No session {session!r} among {project}'s Claude Code logs")
    records = claude_logs.read_records(log)
    if claude_logs.is_dream_session(records) or session in _load_state(Paths(project))["dream_sessions"]:
        print("Note: this is a /hstack:dream session, which a run never analyzes.", file=sys.stderr)
    return render_text(claude_logs.export(log, records))


def main() -> int:
    parser = argparse.ArgumentParser(prog="dream.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--project", default=".", help="the project's root folder (default: the current folder)")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_command = commands.add_parser("prepare", help="prepare a run and print the workflow's args")
    prepare_command.add_argument("--session", help="the id of the session /hstack:dream runs in")
    prepare_command.add_argument("--dry-run", action="store_true", help="say what a run would take; write nothing")
    finish_command = commands.add_parser("finish", help="finish a run with the workflow's report")
    finish_command.add_argument("--run", required=True, help="the run id prepare printed")
    finish_command.add_argument(
        "--report",
        metavar="FILE",
        help="the workflow's report as JSON, '-' for standard input; used only if Claude Code's saved result of "
        "the workflow run is not found",
    )
    finish_command.add_argument("--no-report", action="store_true", help="the workflow returned no report")
    show_command = commands.add_parser("show", help="print one session's transcript")
    show_command.add_argument("session", help="the session id")
    arguments = parser.parse_args()
    # A title or a message can hold a lone surrogate, half an emoji cut off; it is printed escaped.
    sys.stdout.reconfigure(errors="backslashreplace")

    project = Path(arguments.project).expanduser().resolve()
    if arguments.command == "show":
        sys.stdout.write(show(project, arguments.session))
        return 0
    if arguments.command == "prepare":
        result = prepare(project, arguments.session, arguments.dry_run)
    else:
        report_text = None
        if arguments.report == "-":
            report_text = sys.stdin.read()
        elif arguments.report:
            report_text = Path(arguments.report).read_text(encoding="utf-8")
        result = finish(project, arguments.run, report_text, no_report=arguments.no_report)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["status"] in ("error", "incomplete") else 0


if __name__ == "__main__":
    sys.exit(main())
