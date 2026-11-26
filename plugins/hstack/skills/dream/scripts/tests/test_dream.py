"""Offline tests for /hstack:dream's scripts. They build small Claude Code logs by hand in a temporary folder,
so they read none of your sessions and call no model. From the plugin's root folder:

    python3 -m unittest discover -s skills/dream/scripts/tests -v
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parents[2] / "hooks"))

import claude_logs  # noqa: E402
import dream  # noqa: E402
import dream_guard  # noqa: E402
from render import render_text  # noqa: E402

NOW = datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc)
COMMAND = "<command-message>hstack:dream</command-message>\n<command-name>/hstack:dream</command-name>"


def at(**ago) -> str:
    return (NOW - timedelta(**ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(sid, text, when, **extra):
    return {"type": "user", "sessionId": sid, "timestamp": when, "message": {"role": "user", "content": text}, **extra}


def assistant(sid, blocks, when, **extra):
    return {"type": "assistant", "sessionId": sid, "timestamp": when, "message": {"role": "assistant", "content": blocks}, **extra}


def results(sid, blocks, when):
    return {"type": "user", "sessionId": sid, "timestamp": when, "message": {"role": "user", "content": blocks}}


def new_id() -> str:
    return str(uuid.uuid4())


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        previous = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude")
        self.addCleanup(self._restore, previous)
        self.project = self.tmp / "project"
        self.project.mkdir()
        name = "".join(c if c.isalnum() and c.isascii() else "-" for c in str(self.project))
        self.logs = claude_logs.claude_home() / "projects" / name
        self.logs.mkdir(parents=True)

    @staticmethod
    def _restore(previous):
        if previous is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = previous

    def write_log(self, sid, records, folder=None) -> Path:
        path = (folder or self.logs) / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return path

    def normal_session(self, text="Run the tests with FOO=1.", **ago) -> str:
        sid = new_id()
        when = at(**(ago or {"hours": 2}))
        self.write_log(sid, [user(sid, text, when), assistant(sid, [{"type": "text", "text": "Noted."}], when)])
        return sid

    def dream_session(self, **ago) -> str:
        sid = new_id()
        when = at(**(ago or {"minutes": 1}))
        self.write_log(sid, [user(sid, COMMAND, when), assistant(sid, [{"type": "text", "text": "Preparing."}], when)])
        return sid



class ConverterTest(Sandbox):
    def test_keeps_the_conversation_and_leaves_out_the_rest(self):
        sid = new_id()
        session_dir = self.logs / sid
        (session_dir / "tool-results").mkdir(parents=True)
        (session_dir / "tool-results" / "out1.txt").write_text("THE WHOLE SAVED OUTPUT", encoding="utf-8")
        preview = (
            "<persisted-output>\nOutput too large (40KB). Full output saved to: "
            f"{session_dir}/tool-results/out1.txt\n\nPreview (first 2KB):\nTHE PREVIEW\n</persisted-output>"
        )
        when = at(hours=1)
        self.write_log(
            sid,
            [
                {"type": "custom-title", "customTitle": "Fix the build"},
                {"type": "file-history-snapshot", "snapshot": {"files": ["x"]}},
                user(sid, "Run the tests with FOO=1.", when, origin={"kind": "human"}),
                user(sid, "SKILL BODY THE USER DID NOT WRITE", when, isMeta=True),
                user(sid, "<task-notification>AGENT FINISHED</task-notification>", when, origin={"kind": "task-notification"}),
                assistant(
                    sid,
                    [
                        {"type": "thinking", "thinking": "SECRET THOUGHTS", "signature": "x"},
                        {"type": "text", "text": "Running them."},
                        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "FOO=1 make test"}},
                    ],
                    when,
                ),
                results(
                    sid,
                    [
                        {"type": "tool_result", "tool_use_id": "t1", "content": preview},
                        {"type": "tool_result", "tool_use_id": "t2", "content": [
                            {"type": "text", "text": "a screenshot"},
                            {"type": "image", "source": {"type": "base64", "data": "BASE64IMAGEDATA"}},
                        ]},
                    ],
                    when,
                ),
                user(sid, "This session is being continued... SUMMARY TEXT", when, isCompactSummary=True),
                {"type": "api-request-blob", "message": {"role": "user", "content": "COPY SENT TO THE API"}},
            ],
        )
        explore = session_dir / "subagents" / "agent-abc.jsonl"
        self.write_log("agent-abc", [user(sid, "Find the config", when, isSidechain=True)], explore.parent)
        explore.with_suffix(".meta.json").write_text(json.dumps({"agentType": "Explore"}))
        analyzer = session_dir / "subagents" / "workflows" / "agent-def.jsonl"
        self.write_log("agent-def", [user(sid, "ANALYZER TASK", when, isSidechain=True)], analyzer.parent)
        analyzer.with_suffix(".meta.json").write_text(json.dumps({"agentType": "hstack:transcript-analyzer"}))

        text = render_text(claude_logs.export(self.logs / f"{sid}.jsonl"))

        self.assertIn(f"SESSION {sid}: Fix the build (agent Claude Code;", text)
        self.assertIn("USER:\nRun the tests with FOO=1.", text)
        self.assertIn("AGENT:\nRunning them.", text)
        self.assertIn("TOOL CALL Bash:\ncommand: FOO=1 make test", text)
        self.assertIn("TOOL RESULT (Bash):\nTHE WHOLE SAVED OUTPUT", text)
        self.assertIn("a screenshot", text)
        self.assertIn("=== sub-thread abc: Explore ===", text)
        self.assertIn("Find the config", text)
        for left_out in (
            "SKILL BODY", "AGENT FINISHED", "SECRET THOUGHTS", "THE PREVIEW", "BASE64IMAGEDATA", "SUMMARY TEXT",
            "COPY SENT TO THE API", "ANALYZER TASK", "file-history",
        ):
            self.assertNotIn(left_out, text)

    def test_tells_dream_sessions_from_others(self):
        sid = new_id()
        dreamed = [user(sid, COMMAND, at(minutes=5)), assistant(sid, [{"type": "text", "text": "ok"}], at(minutes=5))]
        mixed = [
            user(sid, "Fix the bug", at(minutes=9)),
            assistant(sid, [{"type": "text", "text": "Fixed."}], at(minutes=8)),
            user(sid, COMMAND, at(minutes=5)),
        ]
        plain = [user(sid, "Fix the bug", at(minutes=9))]
        self.assertTrue(claude_logs.is_dream_session(dreamed))
        self.assertFalse(claude_logs.talked_before_dream(dreamed))
        self.assertFalse(claude_logs.is_dream_session(mixed))
        self.assertTrue(claude_logs.talked_before_dream(mixed))
        self.assertFalse(claude_logs.runs_dream(plain))


class PrepareTest(Sandbox):
    def test_takes_the_window_and_leaves_out_dream_sessions(self):
        old = self.normal_session(days=20)
        recent = self.normal_session(hours=3)
        earlier_dream = self.dream_session(days=2)
        current = self.dream_session(minutes=1)
        stray = new_id()  # a log that carries the current dream session's id, wherever Claude Code put it
        self.write_log(stray, [user(current, "an agent of the dream", at(minutes=1))])
        (self.project / ".dreaming" / "memory").mkdir(parents=True)
        (self.project / ".dreaming" / "memory" / "notes.md").write_text("earlier memory")

        result = dream.prepare(self.project, current, started=NOW)

        self.assertEqual(result["status"], "ready", result)
        transcripts = result["workflow_args"]["transcripts"]
        self.assertEqual([Path(t).stem for t in transcripts], [recent])
        self.assertIn("Run the tests with FOO=1.", Path(transcripts[0]).read_text())
        self.assertEqual(result["workflow_args"]["group_size"], 20)
        self.assertEqual(result["workflow_args"]["memory_dir"], str(self.project / ".dreaming" / "memory"))
        skipped = {s["id"] for s in result["skipped"]}
        self.assertEqual(skipped, {earlier_dream, current, stray})
        self.assertNotIn(old, skipped)
        state = json.loads((self.project / ".dreaming" / "state" / "state.json").read_text())
        self.assertEqual(set(state["dream_sessions"]), {current, earlier_dream})
        self.assertEqual(state["last_run"], "2026-11-10T12:00:00.000Z")  # the first window, kept until a complete finish
        self.assertEqual(state["current_run"], result["run_id"])
        run_dir = self.project / ".dreaming" / "state" / "runs" / result["run_id"]
        self.assertEqual((run_dir / "memory_before" / "notes.md").read_text(), "earlier memory")
        self.assertEqual((self.project / ".dreaming" / "state" / ".gitignore").read_text().splitlines()[-1], "*")
        self.assertFalse((self.project / ".dreaming" / ".gitignore").exists())  # memory is the project's to commit

    def test_a_log_unchanged_since_the_window_began_is_not_read(self):
        sid = self.normal_session(hours=1)
        log = self.logs / f"{sid}.jsonl"
        old = (NOW - timedelta(days=30)).timestamp()
        os.utime(log, (old, old))
        result = dream.prepare(self.project, None, dry_run=True, started=NOW)
        self.assertEqual(result["sessions"], [])

    def test_refuses_in_a_session_that_already_has_a_conversation(self):
        sid = new_id()
        self.write_log(sid, [
            user(sid, "Fix the bug", at(minutes=9)),
            assistant(sid, [{"type": "text", "text": "Fixed."}], at(minutes=8)),
            user(sid, COMMAND, at(minutes=1)),
        ])
        result = dream.prepare(self.project, sid, started=NOW)
        self.assertEqual(result["status"], "refused")
        self.assertFalse((self.project / ".dreaming" / "state" / "state.json").exists())

    def test_finds_the_current_session_without_its_id(self):
        current = self.dream_session(minutes=1)
        self.normal_session(hours=1)
        result = dream.prepare(self.project, "${CLAUDE_SESSION_ID}")
        self.assertEqual(result["status"], "ready")
        self.assertIn(current, {s["id"] for s in result["skipped"]})

    def test_with_nothing_to_take_moves_the_window_on(self):
        current = self.dream_session(minutes=1)
        result = dream.prepare(self.project, current, started=NOW)
        self.assertEqual(result["status"], "nothing")
        state = json.loads((self.project / ".dreaming" / "state" / "state.json").read_text())
        self.assertEqual(state["last_run"], "2026-11-24T12:00:00.000Z")

    def test_a_dry_run_writes_nothing(self):
        self.normal_session(hours=1)
        result = dream.prepare(self.project, None, dry_run=True, started=NOW)
        self.assertEqual(result["status"], "dry run")
        self.assertEqual(len(result["sessions"]), 1)
        self.assertFalse((self.project / ".dreaming").exists())


class FinishTest(Sandbox):
    def prepared(self):
        self.normal_session(hours=1)
        current = self.dream_session(minutes=1)
        memory = self.project / ".dreaming" / "memory"
        memory.mkdir(parents=True)
        (memory / "kept-stub.md").write_text("MOVED TO [[topic]]\n")  # from an earlier run: not this run's
        result = dream.prepare(self.project, current, started=NOW)
        self.assertEqual(result["status"], "ready")
        return result, memory

    def organizer_wrote(self, *files, agent="hstack:memory-organizer", when="2026-11-24T12:00:30+00:00"):
        """What the hook logs for each Write the organizer makes."""
        log = self.project / ".dreaming" / "state" / "guard.log"
        with log.open("a") as out:
            for file in files:
                out.write(json.dumps({"time": when, "agent_type": agent, "tool": "Write", "target": str(file),
                                      "decision": "allow"}) + "\n")

    @staticmethod
    def report(inputs, failures=(), organized=True):
        failures = [{"input": f, "reason": "it broke"} for f in failures]
        return json.dumps({
            "inputs_expected": len(inputs),
            "inputs_processed": len(inputs) - len(failures),
            "failures": failures,
            "organized": organized,
            "index_entries": 2,
            "changes": ["merged old-name into topic"],
        })

    def test_a_complete_run_sweeps_its_placeholders_and_moves_the_window_on(self):
        result, memory = self.prepared()
        (memory / "_index.md").write_text("## Project\n[[topic]] - the topic\n- [[other]] - another\n")
        (memory / "topic.md").write_text("---\nname: topic\n---\nfacts\n")
        (memory / "old-name.md").write_text("MOVED TO [[topic]]\n")
        (memory / "user-stub.md").write_text("MOVED TO [[topic]]\n")  # added by someone else during the run
        self.organizer_wrote(memory / "old-name.md", memory / "topic.md")
        self.organizer_wrote(memory / "user-stub.md", agent="hstack:transcript-analyzer")

        done = dream.finish(self.project, result["run_id"], self.report(result["workflow_args"]["transcripts"]))

        self.assertEqual(done["status"], "complete", done)
        self.assertEqual(done["placeholders_deleted"], 1)
        self.assertEqual(done["index_entries"], 2)
        self.assertFalse((memory / "old-name.md").exists())
        self.assertTrue((memory / "kept-stub.md").exists())
        self.assertTrue((memory / "user-stub.md").exists())  # not the organizer's: not this run's to delete
        state = json.loads((self.project / ".dreaming" / "state" / "state.json").read_text())
        self.assertEqual(state["last_run"], "2026-11-24T12:00:00.000Z")
        report = Path(done["report"]).read_text()
        self.assertIn("merged old-name into topic", report)

        again = dream.prepare(self.project, self.dream_session(minutes=0), started=NOW + timedelta(hours=1))
        self.assertEqual(again["status"], "nothing")  # the session taken before is not taken again

    def test_a_failure_keeps_the_window(self):
        result, _ = self.prepared()
        inputs = result["workflow_args"]["transcripts"]
        done = dream.finish(self.project, result["run_id"], self.report(inputs, failures=inputs))
        self.assertEqual(done["status"], "incomplete")
        self.assertTrue(any("not analyzed" in p for p in done["problems"]))
        state = json.loads((self.project / ".dreaming" / "state" / "state.json").read_text())
        self.assertEqual(state["last_run"], "2026-11-10T12:00:00.000Z")  # the first window is kept

    def test_a_placeholder_whose_target_is_missing_is_left_and_reported(self):
        result, memory = self.prepared()
        (memory / "ghost.md").write_text("MOVED TO [[nowhere]]")
        self.organizer_wrote(memory / "ghost.md")
        done = dream.finish(self.project, result["run_id"], self.report(result["workflow_args"]["transcripts"]))
        self.assertEqual(done["status"], "incomplete")
        self.assertTrue((memory / "ghost.md").exists())

    def save_workflow_result(self, inputs, report_json, session="5bcf1aae-526e-4811-a1b3-959f060f158a"):
        """What Claude Code writes for a workflow run, as far as finish reads it."""
        saved = self.logs / session / "workflows" / "wf_test-1.json"
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(json.dumps({
            "args": {"memory_dir": "x", "transcripts": inputs, "group_size": 20, "instructions": ""},
            "result": {"report": json.loads(report_json), "summaries": []},
            "status": "completed",
        }))

    def test_without_a_saved_result_or_a_report_it_asks_for_the_report(self):
        result, _ = self.prepared()
        done = dream.finish(self.project, result["run_id"])
        self.assertEqual(done["status"], "report needed")
        state = json.loads((self.project / ".dreaming" / "state" / "state.json").read_text())
        self.assertEqual(state["last_run"], "2026-11-10T12:00:00.000Z")  # unchanged

    def test_the_saved_result_is_read_without_a_report_passed_in(self):
        result, _ = self.prepared()
        inputs = result["workflow_args"]["transcripts"]
        self.save_workflow_result(inputs, self.report(inputs))
        done = dream.finish(self.project, result["run_id"])
        self.assertEqual(done["status"], "complete", done)
        run = json.loads((self.project / ".dreaming" / "state" / "runs" / result["run_id"] / "run.json").read_text())
        self.assertEqual(run["report_source"], "Claude Code's saved result of the workflow run")

    def test_the_saved_result_wins_over_a_different_report_passed_in(self):
        result, _ = self.prepared()
        inputs = result["workflow_args"]["transcripts"]
        self.save_workflow_result(inputs, self.report(inputs))
        done = dream.finish(self.project, result["run_id"], self.report(inputs, failures=inputs))
        self.assertEqual(done["status"], "complete")
        self.assertIn("differed", Path(done["report"]).read_text())

    def test_a_saved_result_of_another_run_is_not_used(self):
        result, _ = self.prepared()
        self.save_workflow_result(["/elsewhere/t.txt"], self.report(["/elsewhere/t.txt"]))
        self.assertEqual(dream.finish(self.project, result["run_id"])["status"], "report needed")

    def test_no_report_means_not_complete(self):
        result, _ = self.prepared()
        done = dream.finish(self.project, result["run_id"], None, no_report=True)
        self.assertEqual(done["status"], "incomplete")
        self.assertIn("no report", done["problems"][0])

    def test_the_whole_workflow_result_is_accepted(self):
        result, _ = self.prepared()
        whole = json.dumps({"report": json.loads(self.report(result["workflow_args"]["transcripts"])), "summaries": []})
        self.assertEqual(dream.finish(self.project, result["run_id"], whole)["status"], "complete")

    def test_only_the_latest_runs_keep_their_transcripts_and_memory_copy(self):
        runs = self.project / ".dreaming" / "state" / "runs"
        for day in range(1, 8):
            run = runs / f"202611{day:02}-120000"
            (run / "transcripts").mkdir(parents=True)
            (run / "memory_before").mkdir()
            (run / "report.md").write_text("kept")
        dream._prune_runs(dream.Paths(self.project))
        whole = [r.name for r in sorted(runs.iterdir()) if (r / "transcripts").exists()]
        self.assertEqual(whole, [f"202611{day:02}-120000" for day in range(3, 8)])
        self.assertFalse((runs / "20261101-120000" / "memory_before").exists())
        self.assertEqual((runs / "20261101-120000" / "report.md").read_text(), "kept")


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.project = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.project)
        self.memory = self.project / ".dreaming" / "memory"
        self.transcripts = self.project / ".dreaming" / "state" / "runs" / "20261124-120000" / "transcripts"
        self.set_current_run("20261124-120000")

    def set_current_run(self, run):
        state = self.project / ".dreaming" / "state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "state.json").write_text(json.dumps({"last_run": None, "dream_sessions": [], "current_run": run}))

    def decide(self, tool, agent="hstack:transcript-analyzer", **given):
        decision = dream_guard.decide({"agent_type": agent, "tool_name": tool, "tool_input": given}, self.project)
        return decision[0] if decision else None

    def test_only_the_current_runs_transcripts_can_be_read(self):
        earlier = self.project / ".dreaming" / "state" / "runs" / "20261101-120000" / "transcripts" / "t.txt"
        self.assertEqual(self.decide("Read", file_path=str(earlier)), "deny")
        self.set_current_run(None)  # finish has closed the run
        self.assertEqual(self.decide("Read", file_path=str(self.transcripts / "t.txt")), "deny")
        self.assertEqual(self.decide("Read", file_path=str(self.memory / "_index.md")), "allow")
        self.set_current_run("../../..")
        self.assertEqual(self.decide("Read", file_path=str(self.project / "secrets.env")), "deny")

    def test_a_linked_memory_folder_is_refused(self):
        docs = self.project / "docs"
        docs.mkdir()
        self.memory.parent.mkdir(parents=True, exist_ok=True)
        self.memory.symlink_to(docs)
        self.assertEqual(self.decide("Edit", file_path=str(self.memory / "guide.md")), "deny")
        self.assertEqual(self.decide("Edit", file_path=str(docs / "guide.md")), "deny")

    def test_other_agents_are_left_alone(self):
        self.assertIsNone(self.decide("Write", agent="Explore", file_path="/etc/passwd"))
        self.assertIsNone(self.decide("Write", agent=None, file_path="/etc/passwd"))

    def test_writes_stay_in_memory_as_markdown(self):
        self.assertEqual(self.decide("Write", file_path=str(self.memory / "topic.md")), "allow")
        self.assertEqual(self.decide("Edit", agent="hstack:memory-organizer", file_path=str(self.memory / "a" / "b.md")), "allow")
        self.assertEqual(self.decide("Write", file_path=str(self.memory / "topic.txt")), "deny")
        self.assertEqual(self.decide("Write", file_path=str(self.memory / "CLAUDE.md")), "deny")
        self.assertEqual(self.decide("Write", file_path=str(self.memory / ".." / "x.md")), "deny")
        self.assertEqual(self.decide("Write", file_path=str(self.transcripts / "t.md")), "deny")
        self.assertEqual(self.decide("Write", file_path=str(self.project / "README.md")), "deny")

    def test_reads_stay_in_memory_and_transcripts(self):
        self.assertEqual(self.decide("Read", file_path=str(self.transcripts / "t.txt")), "allow")
        self.assertEqual(self.decide("Read", file_path=str(self.memory / "_index.md")), "allow")
        self.assertEqual(self.decide("Read", file_path=str(self.project / "secrets.env")), "deny")
        self.assertEqual(self.decide("Read", file_path=str(self.project / ".dreaming" / "state" / "state.json")), "deny")
        self.assertEqual(self.decide("Grep", pattern="FOO", path=str(self.memory)), "allow")
        self.assertEqual(self.decide("Grep", pattern="FOO"), "deny")  # no path: the whole project
        self.assertEqual(self.decide("Glob", pattern="**/*.md", path=str(self.memory)), "allow")
        self.assertEqual(self.decide("Glob", pattern="../../*", path=str(self.memory)), "deny")
        self.assertEqual(self.decide("Grep", pattern="x", glob="../*", path=str(self.memory)), "deny")

    def test_other_tools_are_refused(self):
        self.assertEqual(self.decide("Bash", command="ls"), "deny")

    def test_a_check_that_breaks_refuses_the_call(self):
        hook = SCRIPTS.parents[2] / "hooks" / "dream_guard.py"
        call = {
            "agent_type": "hstack:transcript-analyzer",
            "tool_name": "Write",
            "tool_input": {"file_path": str(self.memory / "bad\u0000name.md")},  # a NUL byte breaks the path check
            "cwd": str(self.project),
        }
        done = subprocess.run(
            [sys.executable, "-B", str(hook)], input=json.dumps(call), capture_output=True, text=True,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(self.project)},
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


class ReviewFixesTest(Sandbox):
    """One test for each problem the pre-commit review found."""

    def run_finished(self, result, **report):
        inputs = result["workflow_args"]["transcripts"]
        return dream.finish(self.project, result["run_id"], json.dumps({
            "inputs_expected": len(inputs), "inputs_processed": len(inputs), "failures": [],
            "organized": True, "index_entries": 0, "changes": [], **report,
        }))

    def test_transcripts_are_private(self):
        self.normal_session(hours=1)
        result = dream.prepare(self.project, self.dream_session(), started=NOW)
        transcript = Path(result["workflow_args"]["transcripts"][0])
        self.assertEqual(transcript.stat().st_mode & 0o777, 0o600)
        for folder in (transcript.parent, transcript.parent.parent, self.project / ".dreaming" / "state"):
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700, folder)

    def test_a_session_started_in_another_folder_with_the_same_log_folder_is_not_taken(self):
        sid = new_id()
        other = Path(f"{self.project.parent}-project")  # <tmp>-project and <tmp>/project share a log folder
        self.assertEqual(claude_logs.log_dirs(other), claude_logs.log_dirs(self.project))
        self.write_log(sid, [user(sid, "Elsewhere", at(hours=1), cwd=str(other))])
        mine = new_id()
        self.write_log(mine, [user(mine, "Here", at(hours=1), cwd=str(self.project))])
        result = dream.prepare(self.project, None, dry_run=True, started=NOW)
        self.assertEqual([s["id"] for s in result["sessions"]], [mine])

    def test_a_failed_first_run_keeps_its_window(self):
        sid = self.normal_session(days=13, hours=23)
        result = dream.prepare(self.project, self.dream_session(), started=NOW)
        dream.finish(self.project, result["run_id"], None, no_report=True)  # the run did not complete
        retry = dream.prepare(self.project, self.dream_session(), started=NOW + timedelta(hours=2))
        self.assertEqual(retry["status"], "ready")
        self.assertIn(sid, [s["id"] for s in retry["sessions"]])

    def test_a_fork_starts_when_it_was_forked(self):
        old, new = at(days=16), at(hours=1)
        sid = new_id()
        source = {"sessionId": new_id(), "messageUuid": "m1"}
        self.write_log(sid, [
            user(sid, "Old question", old, forkedFrom=source),
            assistant(sid, [{"type": "text", "text": "Old answer"}], old, forkedFrom=source),
            user(sid, "New work after the fork", new),
        ])
        result = dream.prepare(self.project, None, dry_run=True, started=NOW)
        self.assertEqual([s["id"] for s in result["sessions"]], [sid])

    def test_quoting_the_command_tags_is_not_running_the_command(self):
        sid = new_id()
        self.write_log(sid, [
            user(sid, f"Why does my log hold {COMMAND}?", at(hours=1)),
            assistant(sid, [{"type": "text", "text": "Because..."}], at(hours=1)),
        ])
        records = claude_logs.read_records(self.logs / f"{sid}.jsonl")
        self.assertFalse(claude_logs.runs_dream(records))
        result = dream.prepare(self.project, self.dream_session(), started=NOW)
        self.assertEqual([s["id"] for s in result["sessions"]], [sid])

    def test_a_rediscovered_dream_lets_none_of_its_logs_through(self):
        old_dream = self.dream_session(days=1)
        companion = new_id()  # a log carrying the old dream's session id, sorted before it or after it
        self.write_log(companion, [user(old_dream, "an agent of the old dream", at(days=1))])
        result = dream.prepare(self.project, self.dream_session(), started=NOW)
        self.assertEqual(result["status"], "nothing")
        self.assertIn(companion, {s["id"] for s in result["skipped"]})

    def test_a_missing_saved_output_is_noted_in_the_transcript(self):
        sid = new_id()
        preview = (
            "<persisted-output>\nOutput too large (40KB). Full output saved to: "
            f"{self.logs / sid}/tool-results/gone.txt\n\nPreview (first 2KB):\nTHE PREVIEW\n</persisted-output>"
        )
        self.write_log(sid, [
            user(sid, "Run it", at(hours=1)),
            assistant(sid, [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "x"}}], at(hours=1)),
            results(sid, [{"type": "tool_result", "tool_use_id": "t1", "content": preview}], at(hours=1)),
        ])
        text = render_text(claude_logs.export(self.logs / f"{sid}.jsonl"))
        self.assertIn("NOTE ABOUT THIS FILE: 1 tool output(s) of this session were not kept in full", text)
        self.assertIn("THE PREVIEW", text)
        result = dream.prepare(self.project, None, dry_run=True, started=NOW)
        self.assertIn("note", result["sessions"][0])

    def test_text_documents_are_kept_and_other_documents_dropped(self):
        sid = new_id()
        self.write_log(sid, [results(sid, [
            {"type": "text", "text": "Use the port in the spec."},
            {"type": "document", "title": "spec.txt", "source": {"type": "text", "media_type": "text/plain",
                                                                 "data": "Production port: 7443"}},
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "PDFBASE64"}},
        ], at(hours=1))])
        text = render_text(claude_logs.export(self.logs / f"{sid}.jsonl"))
        self.assertIn("[document: spec.txt]\nProduction port: 7443", text)
        self.assertNotIn("PDFBASE64", text)

    def test_a_long_project_path_finds_its_hashed_log_folder(self):
        project = self.tmp / ("p" * 120) / ("q" * 120)
        project.mkdir(parents=True)
        name = "".join(c if c.isalnum() else "-" for c in str(project))
        folder = claude_logs.claude_home() / "projects" / f"{name[:200]}-1x2y3z"
        sid = new_id()
        self.write_log(sid, [user(sid, "Hello", at(hours=1), cwd=str(project))], folder)
        result = dream.prepare(project, None, dry_run=True, started=NOW)
        self.assertEqual([s["id"] for s in result["sessions"]], [sid])

    def test_a_lone_surrogate_does_not_stop_the_run(self):
        sid = new_id()
        path = self.logs / f"{sid}.jsonl"
        record = json.dumps(user(sid, "cut emoji: \ud83d end", at(hours=1)))  # json escapes it as \\ud83d
        path.write_text(record + "\n", encoding="utf-8")
        result = dream.prepare(self.project, self.dream_session(), started=NOW)
        self.assertEqual(result["status"], "ready")
        self.assertIn("cut emoji", Path(result["workflow_args"]["transcripts"][0]).read_text(encoding="utf-8"))

    def test_a_broken_link_in_memory_does_not_stop_the_run(self):
        memory = self.project / ".dreaming" / "memory"
        memory.mkdir(parents=True)
        (memory / "renamed.md").symlink_to(memory / "gone.md")
        self.normal_session(hours=1)
        self.assertEqual(dream.prepare(self.project, self.dream_session(), started=NOW)["status"], "ready")

    def test_finish_closes_the_run_to_the_agents(self):
        self.normal_session(hours=1)
        result = dream.prepare(self.project, self.dream_session(), started=NOW)
        self.run_finished(result)
        state = json.loads((self.project / ".dreaming" / "state" / "state.json").read_text())
        self.assertIsNone(state["current_run"])

    def test_a_placeholder_restored_meanwhile_is_not_deleted(self):
        stub = self.project / "note.md"
        stub.write_text("restored text")
        self.assertFalse(dream._unlink_if_unchanged(stub, "MOVED TO [[topic]]"))
        self.assertEqual(stub.read_text(), "restored text")


class SecondReviewTest(Sandbox):
    """One test for each problem the second pre-commit review found."""

    def test_a_note_saved_right_after_the_check_survives(self):
        memory = self.project / ".dreaming" / "memory"
        memory.mkdir(parents=True)
        stub = memory / "old.md"
        stub.write_text("MOVED TO [[topic]]")
        real_rename = os.rename

        def rename_then_editor_saves(source, target):
            real_rename(source, target)
            Path(source).write_text("restored by an editor")  # saved the moment the placeholder moved aside

        with unittest.mock.patch.object(dream.os, "rename", rename_then_editor_saves):
            self.assertTrue(dream._unlink_if_unchanged(stub, "MOVED TO [[topic]]"))
        self.assertEqual(stub.read_text(), "restored by an editor")

    def test_linked_folders_are_refused_and_left_untouched(self):
        shared = self.tmp / "shared-cache"
        shared.mkdir()
        shared.chmod(0o755)
        (self.project / ".dreaming").mkdir()
        (self.project / ".dreaming" / "state").symlink_to(shared)
        self.normal_session(hours=1)
        result = dream.prepare(self.project, self.dream_session(), started=NOW)
        self.assertEqual(result["status"], "error")
        self.assertEqual(shared.stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(shared.iterdir()), [])

    def test_a_run_with_nothing_closes_an_abandoned_run(self):
        sid = self.normal_session(hours=1)
        first = dream.prepare(self.project, self.dream_session(), started=NOW)
        self.assertEqual(first["status"], "ready")  # and then never finished
        (self.logs / f"{sid}.jsonl").unlink()
        again = dream.prepare(self.project, self.dream_session(), started=NOW + timedelta(hours=1))
        self.assertEqual(again["status"], "nothing")
        state = json.loads((self.project / ".dreaming" / "state" / "state.json").read_text())
        self.assertIsNone(state["current_run"])

    def test_only_the_whole_typed_command_marks_a_dream(self):
        sid = new_id()
        self.write_log(sid, [user(sid, "<command-name>/hstack:dream</command-name>", at(hours=1))])
        self.assertFalse(claude_logs.runs_dream(claude_logs.read_records(self.logs / f"{sid}.jsonl")))

    def test_a_recent_log_carrying_an_old_dreams_id_is_not_taken(self):
        old_dream = self.dream_session(days=18)
        old_log = self.logs / f"{old_dream}.jsonl"
        stale = (NOW - timedelta(days=18)).timestamp()
        os.utime(old_log, (stale, stale))
        companion = new_id()
        self.write_log(companion, [user(old_dream, "an agent of the old dream", at(hours=1))])
        result = dream.prepare(self.project, self.dream_session(), started=NOW)
        self.assertEqual(result["status"], "nothing")
        self.assertIn(companion, {s["id"] for s in result["skipped"]})

    def test_a_fork_is_taken_once_from_its_first_own_message(self):
        sid, source = new_id(), {"sessionId": new_id(), "messageUuid": "m1"}
        copies = [user(sid, "Old question", at(days=17), forkedFrom=source)]
        self.write_log(sid, copies)
        self.assertEqual(dream.prepare(self.project, None, dry_run=True, started=NOW)["sessions"], [])
        self.write_log(sid, [*copies, user(sid, "New work", at(hours=1))])
        first = dream.prepare(self.project, self.dream_session(), started=NOW)
        self.assertEqual([s["id"] for s in first["sessions"]], [sid])
        dream.finish(self.project, first["run_id"], json.dumps({
            "inputs_expected": 1, "inputs_processed": 1, "failures": [], "organized": True,
            "index_entries": 0, "changes": [],
        }))
        self.write_log(sid, [*copies, user(sid, "New work", at(hours=1)), user(sid, "More", at(minutes=-30))])
        later = dream.prepare(self.project, self.dream_session(minutes=-40), started=NOW + timedelta(hours=1))
        self.assertEqual([s["id"] for s in later["sessions"]], [sid])  # only its part said since the first run
        text = Path(later["workflow_args"]["transcripts"][0]).read_text()
        self.assertIn("More", text)
        self.assertNotIn("New work", text)
        self.assertNotIn("Old question", text)

    def test_folder_names_follow_claude_codes_rules(self):
        project = self.tmp / "rocket\U0001F680"
        project.mkdir()
        name = "".join(c if c.isascii() and c.isalnum() else "-" for c in str(self.tmp)) + "-rocket--"
        sid = new_id()
        self.write_log(sid, [user(sid, "Hello", at(hours=1), cwd=str(project))],
                       claude_logs.claude_home() / "projects" / name)
        self.assertEqual(len(dream.prepare(project, None, dry_run=True, started=NOW)["sessions"]), 1)

        named = new_id()
        self.write_log(named, [user(named, "Hi", at(hours=1), cwd=str(self.project))],
                       claude_logs.claude_home() / "projects" / "my-project")
        with unittest.mock.patch.dict(os.environ, {"CLAUDE_CODE_PROJECT_DIR_NAME": "my-project"}):
            found = dream.prepare(self.project, None, dry_run=True, started=NOW)["sessions"]
        self.assertIn(named, [s["id"] for s in found])

    def test_a_session_in_two_log_folders_is_taken_once(self):
        project = self.tmp / ("p" * 120) / ("q" * 120)
        project.mkdir(parents=True)
        prefix = "".join(c if c.isascii() and c.isalnum() else "-" for c in str(project))[:200]
        sid = new_id()
        for suffix, text in (("aaa", "older copy"), ("bbb", "newer copy")):
            folder = claude_logs.claude_home() / "projects" / f"{prefix}-{suffix}"
            self.write_log(sid, [user(sid, text, at(hours=1), cwd=str(project))], folder)
        older = claude_logs.claude_home() / "projects" / f"{prefix}-aaa" / f"{sid}.jsonl"
        newer = claude_logs.claude_home() / "projects" / f"{prefix}-bbb" / f"{sid}.jsonl"
        os.utime(older, (newer.stat().st_mtime - 60, newer.stat().st_mtime - 60))
        result = dream.prepare(project, self.dream_session(), started=NOW)
        self.assertEqual(len(result["workflow_args"]["transcripts"]), 1)
        self.assertIn("newer copy", Path(result["workflow_args"]["transcripts"][0]).read_text())

    def write_tool_result(self, sid, preview):
        self.write_log(sid, [
            user(sid, "Run it", at(hours=1)),
            assistant(sid, [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "x"}}], at(hours=1)),
            results(sid, [{"type": "tool_result", "tool_use_id": "t1", "content": preview}], at(hours=1)),
        ])
        return render_text(claude_logs.export(self.logs / f"{sid}.jsonl"))

    def test_saved_outputs_are_read_from_the_sessions_own_folder_only(self):
        sid = new_id()
        (self.logs / sid / "tool-results").mkdir(parents=True)
        (self.logs / sid / "tool-results" / "out.txt").write_text("ITS OWN SAVED OUTPUT")
        own = self.write_tool_result(sid, (
            "<persisted-output>\nOutput too large (40KB). Full output saved to: "
            f"{self.logs / sid}/tool-results/out.txt\n\nPreview (first 2KB):\nPREVIEW\n</persisted-output>"))
        self.assertIn("ITS OWN SAVED OUTPUT", own)
        self.assertNotIn("NOTE ABOUT THIS FILE", own)

        # Another project's saved output, named by a preview a tool printed (a session reading another's log).
        elsewhere = claude_logs.claude_home() / "projects" / "-other-project" / new_id() / "tool-results"
        elsewhere.mkdir(parents=True)
        (elsewhere / "out.txt").write_text("ANOTHER PROJECT'S OUTPUT")
        quoted = self.write_tool_result(new_id(), (
            "<persisted-output>\nOutput too large (40KB). Full output saved to: "
            f"{elsewhere}/out.txt\n\nPreview (first 2KB):\nPREVIEW\n</persisted-output>"))
        self.assertNotIn("ANOTHER PROJECT'S OUTPUT", quoted)
        self.assertIn("NOTE ABOUT THIS FILE", quoted)

        sid = new_id()
        (self.logs / sid / "tool-results").mkdir(parents=True)
        (self.logs / sid / "tool-results" / "part.txt").write_text("THE SAVED PART")
        partial = self.write_tool_result(sid, (
            "<persisted-output>\nOutput exceeded the 64MB persist limit; only the first 64MB were saved to: "
            f"{self.logs / sid}/tool-results/part.txt\n\nPreview (start and end):\nPREVIEW\n</persisted-output>"))
        self.assertIn("THE SAVED PART", partial)
        self.assertIn("were saved by Claude Code only in part", partial)

    def test_a_preview_naming_a_file_outside_claude_codes_logs_is_not_read(self):
        secret = self.tmp / "tool-results" / "id_rsa"
        secret.parent.mkdir()
        secret.write_text("PRIVATE KEY")
        text = self.write_tool_result(new_id(), (
            f"<persisted-output>\nOutput too large (1KB). Full output saved to: {secret}\n\nPREVIEW\n</persisted-output>"))
        self.assertNotIn("PRIVATE KEY", text)
        self.assertIn("NOTE ABOUT THIS FILE", text)


class HandsOnRunTest(Sandbox):
    """What a hands-on run with Claude Code 2.1.332, and its review, turned up."""

    def parent(self, **ago):
        sid, when = new_id(), at(**ago)
        records = [
            user(sid, "PARENT FACT", when, uuid=new_id()),
            assistant(sid, [{"type": "text", "text": "Noted."}], when, uuid=new_id()),
        ]
        self.write_log(sid, records)
        return sid, records

    def fork(self, parent, records, text, when):
        """A fork as Claude Code 2.1.332 logs it: a derived-from record, then copies of the parent's messages
        that keep their uuids and times (a copied reply marked as made for the parent), then its own."""
        sid = new_id()
        copies = [
            {**r, "sessionId": sid, **({"requestSessionId": parent} if r["type"] == "assistant" else {})}
            for r in records
        ]
        derived = {"type": "derived-from", "v": 1, "sessionId": sid,
                   "sessions": [{"type": "claude_code_session", "id": parent, "via": "fork"}]}
        self.write_log(sid, [derived, *copies, user(sid, text, when, uuid=new_id())])
        return sid

    def transcript(self, sid):
        return render_text(claude_logs.export(self.logs / f"{sid}.jsonl"))

    def complete(self, prepared):
        taken = len(prepared["sessions"])
        return dream.finish(self.project, prepared["run_id"], json.dumps({
            "inputs_expected": taken, "inputs_processed": taken, "failures": [], "organized": True,
            "index_entries": 0, "changes": [],
        }))

    def state(self):
        return json.loads((self.project / ".dreaming" / "state" / "state.json").read_text())

    def test_a_fork_holds_and_starts_with_its_own_messages_only(self):
        parent, records = self.parent(days=3)
        fork = self.fork(parent, records, "FORK FACT", at(hours=1))
        text = self.transcript(fork)
        self.assertIn("FORK FACT", text)
        self.assertNotIn("PARENT FACT", text)
        self.assertNotIn("NOTE ABOUT THIS FILE", text)
        result = dream.prepare(self.project, None, dry_run=True, started=NOW)
        self.assertIn((fork, at(hours=1)), [(s["id"], s["created"]) for s in result["sessions"]])

    def test_a_fork_of_a_session_an_earlier_run_took_is_taken(self):
        parent, records = self.parent(days=3)
        first = dream.prepare(self.project, self.dream_session(days=2), started=NOW - timedelta(days=2))
        self.assertEqual([s["id"] for s in first["sessions"]], [parent])
        self.complete(first)
        fork = self.fork(parent, records, "FORK FACT", at(hours=1))
        later = dream.prepare(self.project, self.dream_session(), started=NOW)
        self.assertEqual([s["id"] for s in later["sessions"]], [fork])

    def test_a_fork_made_in_vs_code_holds_only_its_own_messages(self):
        parent, records = self.parent(days=3)
        sid = new_id()
        copies = [  # as VS Code copies them: new uuids, each naming its original
            {**r, "sessionId": sid, "uuid": new_id(), "forkedFrom": {"sessionId": parent, "messageUuid": r["uuid"]}}
            for r in records
        ]
        self.write_log(sid, [*copies, user(sid, "FORK FACT", at(hours=1), uuid=new_id())])
        text = self.transcript(sid)
        self.assertIn("FORK FACT", text)
        self.assertNotIn("PARENT FACT", text)

    def test_a_fork_whose_original_is_gone_keeps_what_it_copied(self):
        parent, records = self.parent(days=3)
        fork = self.fork(parent, records, "FORK FACT", at(hours=1))
        (self.logs / f"{parent}.jsonl").unlink()
        text = self.transcript(fork)
        self.assertIn("PARENT FACT", text)  # nothing else holds it any more
        self.assertIn("FORK FACT", text)

    def test_a_compacted_session_keeps_everything(self):
        sid = new_id()
        compacted = {"type": "derived-from", "v": 1, "sessionId": sid,
                     "sessions": [{"type": "claude_code_session", "id": sid, "via": "compact"}]}
        self.write_log(sid, [
            user(sid, "BEFORE COMPACTING", at(hours=2), uuid=new_id()),
            compacted,
            user(sid, "AFTER COMPACTING", at(hours=1), uuid=new_id()),
        ])
        text = self.transcript(sid)
        self.assertIn("BEFORE COMPACTING", text)
        self.assertIn("AFTER COMPACTING", text)

    def test_a_fork_of_a_dream_session_is_a_dream_session(self):
        dream_sid, when = new_id(), at(hours=3)
        records = [user(dream_sid, COMMAND, when, uuid=new_id()),
                   assistant(dream_sid, [{"type": "text", "text": "Preparing."}], when, uuid=new_id())]
        self.write_log(dream_sid, records)
        fork = self.fork(dream_sid, records, "Something else", at(hours=1))
        result = dream.prepare(self.project, None, dry_run=True, started=NOW)
        self.assertNotIn(fork, [s["id"] for s in result["sessions"]])

    def test_a_message_that_got_no_reply_is_a_conversation(self):
        sid = new_id()
        self.write_log(sid, [
            user(sid, "A FACT, sent and then interrupted", at(minutes=3)),
            user(sid, "[Request interrupted by user]", at(minutes=3)),
            user(sid, COMMAND, at(minutes=1)),
        ])
        result = dream.prepare(self.project, sid, started=NOW)
        self.assertEqual(result["status"], "refused")

    def test_slash_commands_before_the_dream_are_not_a_conversation(self):
        self.normal_session(hours=1)
        sid = new_id()
        self.write_log(sid, [
            user(sid, "<command-name>/model</command-name>\n<command-message>model</command-message>", at(minutes=3)),
            user(sid, "<local-command-stdout>Set model to opus</local-command-stdout>", at(minutes=3)),
            user(sid, COMMAND, at(minutes=1)),
        ])
        result = dream.prepare(self.project, sid, started=NOW)
        self.assertEqual(result["status"], "ready")
        self.assertIn(sid, {s["id"] for s in result["skipped"]})

    def test_what_claude_code_prints_for_a_command_is_not_the_users(self):
        sid = new_id()
        self.write_log(sid, [
            user(sid, "BEFORE", at(hours=2)),
            user(sid, "<local-command-stdout>Compacted (ctrl+o to see full summary)</local-command-stdout>", at(hours=2)),
            user(sid, "AFTER", at(hours=1)),
        ])
        text = self.transcript(sid)
        self.assertNotIn("Compacted", text)
        self.assertIn("BEFORE", text)
        self.assertIn("AFTER", text)

    def test_command_output_is_left_out_but_the_rest_of_its_message_kept(self):
        sid = new_id()
        self.write_log(sid, [results(sid, [
            {"type": "text", "text": "<local-command-stdout>Set model to opus</local-command-stdout>"},
            {"type": "document", "title": "spec.txt", "source": {"type": "text", "media_type": "text/plain",
                                                                 "data": "Production port: 7443"}},
        ], at(hours=1))])
        text = self.transcript(sid)
        self.assertNotIn("Set model to opus", text)
        self.assertIn("Production port: 7443", text)

    def test_a_session_created_as_a_run_starts_is_the_next_runs(self):
        at_start = new_id()
        self.write_log(at_start, [user(at_start, "At the start", "2026-11-24T12:00:00.074Z")])
        after = new_id()
        self.write_log(after, [user(after, "Just after", "2026-11-24T12:00:01.500Z")])
        first = dream.prepare(self.project, self.dream_session(), started=NOW + timedelta(microseconds=74900))
        self.assertEqual(first["status"], "nothing")
        later = dream.prepare(self.project, self.dream_session(), started=NOW + timedelta(hours=1))
        self.assertEqual(sorted(s["id"] for s in later["sessions"]), sorted([at_start, after]))

    @staticmethod
    def linked(sid, *steps):
        """Records linked as Claude Code links them: each (name, parent name or None, record) gets a uuid and
        its parent's uuid."""
        ids, out = {}, []
        for name, parent, record in steps:
            ids[name] = new_id()
            out.append({**record, "uuid": ids[name], "parentUuid": ids[parent] if parent else None})
        return out

    def test_what_the_user_rewound_away_is_left_out(self):
        sid, when = new_id(), at(hours=1)
        self.write_log(sid, self.linked(
            sid,
            ("q", None, user(sid, "How many stations are there?", when)),
            ("a", "q", assistant(sid, [{"type": "text", "text": "Three."}], when)),
            ("gone", "a", user(sid, "Decision: remove the inlet station.", when)),
            ("gone-reply", "gone", assistant(sid, [{"type": "text", "text": "Noted, inlet goes."}], when)),
            ("new", "a", user(sid, "How many tests are there?", when)),
            ("reply", "new", assistant(sid, [{"type": "text", "text": "Two."}], when)),
        ))
        text = self.transcript(sid)
        for kept in ("How many stations", "Three.", "How many tests", "Two."):
            self.assertIn(kept, text)
        self.assertNotIn("remove the inlet", text)
        self.assertNotIn("inlet goes", text)

    def test_a_rewind_to_before_the_first_message_leaves_that_message_out(self):
        sid, when = new_id(), at(hours=1)
        self.write_log(sid, self.linked(
            sid,
            ("first", None, user(sid, "FIRST, taken back", when)),
            ("first-reply", "first", assistant(sid, [{"type": "text", "text": "Ok."}], when)),
            ("second", None, user(sid, "SECOND", when)),
        ))
        text = self.transcript(sid)
        self.assertIn("SECOND", text)
        self.assertNotIn("FIRST", text)

    def test_parallel_tool_calls_keep_every_result(self):
        sid, when = new_id(), at(hours=1)
        self.write_log(sid, self.linked(
            sid,
            ("q", None, user(sid, "Read both files.", when)),
            ("call-1", "q", assistant(sid, [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}], when)),
            ("call-2", "call-1", assistant(sid, [{"type": "tool_use", "id": "t2", "name": "Read", "input": {}}], when)),
            ("result-1", "call-1", results(sid, [{"type": "tool_result", "tool_use_id": "t1", "content": "RESULT ONE"}], when)),
            ("skill", "result-1", user(sid, "A skill's body", when, isMeta=True)),
            ("result-2", "call-2", results(sid, [{"type": "tool_result", "tool_use_id": "t2", "content": "RESULT TWO"}], when)),
            ("done", "result-2", assistant(sid, [{"type": "text", "text": "Read both."}], when)),
        ))
        text = self.transcript(sid)
        for kept in ("Read both files.", "RESULT ONE", "RESULT TWO", "Read both."):
            self.assertIn(kept, text)

    def test_a_compaction_keeps_what_came_before_it(self):
        sid, when = new_id(), at(hours=1)
        records = self.linked(
            sid,
            ("before", None, user(sid, "BEFORE COMPACTING", when)),
            ("before-reply", "before", assistant(sid, [{"type": "text", "text": "Ok."}], when)),
        )
        boundary = {"type": "system", "subtype": "compact_boundary", "uuid": new_id(), "parentUuid": None,
                    "logicalParentUuid": records[-1]["uuid"]}
        summary = user(sid, "Summary of the conversation so far", when, isCompactSummary=True, uuid=new_id(),
                       parentUuid=boundary["uuid"])
        after = user(sid, "AFTER COMPACTING", when, uuid=new_id(), parentUuid=summary["uuid"])
        self.write_log(sid, [*records, boundary, summary, after])
        text = self.transcript(sid)
        self.assertIn("BEFORE COMPACTING", text)
        self.assertIn("AFTER COMPACTING", text)

    def test_a_continued_session_gives_the_next_run_only_its_new_part(self):
        sid = new_id()
        earlier = [user(sid, "EARLIER FACT", at(hours=2)), assistant(sid, [{"type": "text", "text": "Ok."}], at(hours=2))]
        self.write_log(sid, earlier)
        self.complete(dream.prepare(self.project, self.dream_session(), started=NOW))
        self.write_log(sid, [*earlier, user(sid, "CONTINUED FACT", at(minutes=-30))])  # reopened after that run
        later = dream.prepare(self.project, self.dream_session(), started=NOW + timedelta(hours=1))
        self.assertEqual([s["id"] for s in later["sessions"]], [sid])
        text = Path(later["workflow_args"]["transcripts"][0]).read_text()
        self.assertIn("CONTINUED FACT", text)
        self.assertNotIn("EARLIER FACT", text)
        self.assertIn("began before 2026-11-24T12:00:00.000Z; only what was said in it from then on is here", text)
        self.complete(later)
        again = dream.prepare(self.project, self.dream_session(), started=NOW + timedelta(hours=2))
        self.assertEqual(again["status"], "nothing")

    def test_what_a_session_says_while_a_run_runs_is_the_next_runs(self):
        sid = new_id()
        self.write_log(sid, [user(sid, "SAID BEFORE THE RUN", at(hours=1)), user(sid, "SAID DURING IT", at(minutes=-10))])
        first = dream.prepare(self.project, self.dream_session(), started=NOW)
        first_text = Path(first["workflow_args"]["transcripts"][0]).read_text()
        self.assertIn("SAID BEFORE THE RUN", first_text)
        self.assertNotIn("SAID DURING IT", first_text)
        self.complete(first)
        later = dream.prepare(self.project, self.dream_session(), started=NOW + timedelta(hours=1))
        later_text = Path(later["workflow_args"]["transcripts"][0]).read_text()
        self.assertIn("SAID DURING IT", later_text)
        self.assertNotIn("SAID BEFORE THE RUN", later_text)

    def test_a_session_of_bare_commands_is_not_a_conversation(self):
        cleared = new_id()
        self.write_log(cleared, [user(cleared, "<command-name>/clear</command-name>\n<command-message>clear</command-message>\n"
                                               "<command-args></command-args>", at(hours=1))])
        asked = new_id()
        self.write_log(asked, [user(asked, "<command-name>/hstack:align</command-name>\n<command-message>hstack:align"
                                           "</command-message>\n<command-args>add a metres flag</command-args>", at(hours=1))])
        result = dream.prepare(self.project, None, dry_run=True, started=NOW)
        self.assertEqual([s["id"] for s in result["sessions"]], [asked])
        self.assertIn({"id": cleared, "reason": "no conversation"}, result["skipped"])

    def test_a_message_typed_while_claude_works_is_kept(self):
        sid, when = new_id(), at(hours=1)
        queued = {"type": "attachment", "sessionId": sid, "timestamp": when, "attachment": {
            "type": "queued_command", "prompt": [{"type": "text", "text": "TYPED WHILE IT WORKED"}],
            "origin": {"kind": "human"}, "humanTurn": True, "commandMode": "prompt"}}
        hand_back = {"type": "attachment", "sessionId": sid, "timestamp": when, "attachment": {
            "type": "queued_command", "prompt": "AN AGENT'S HAND-BACK", "origin": {"kind": "peer"}, "isMeta": True}}
        self.write_log(sid, [user(sid, "Start", when), queued, hand_back,
                             assistant(sid, [{"type": "text", "text": "Yes."}], when)])
        text = self.transcript(sid)
        self.assertIn("TYPED WHILE IT WORKED", text)
        self.assertNotIn("AN AGENT'S HAND-BACK", text)

    def test_what_the_original_took_back_stays_in_a_fork_that_kept_it(self):
        parent, when = new_id(), at(hours=3)
        records = self.linked(
            parent,
            ("q", None, user(parent, "Question", when)),
            ("a", "q", assistant(parent, [{"type": "text", "text": "Answer."}], when)),
            ("decision", "a", user(parent, "DECISION THE FORK KEPT", when)),
            ("ok", "decision", assistant(parent, [{"type": "text", "text": "Noted."}], when)),
        )
        fork = self.fork(parent, records, "The fork goes on", at(hours=2))
        instead = user(parent, "Something else instead", at(hours=1), uuid=new_id(), parentUuid=records[1]["uuid"])
        self.write_log(parent, [*records, instead])  # then the original rewinds past the decision
        self.assertNotIn("DECISION THE FORK KEPT", self.transcript(parent))
        self.assertIn("DECISION THE FORK KEPT", self.transcript(fork))

    def test_a_subagent_working_after_its_session_went_quiet_is_taken(self):
        sid = new_id()
        log = self.write_log(sid, [user(sid, "Start a background agent.", at(hours=5))])
        self.complete(dream.prepare(self.project, self.dream_session(), started=NOW))
        agent = self.logs / sid / "subagents" / "agent-a1.jsonl"
        agent.parent.mkdir(parents=True)
        agent.write_text(json.dumps(user(sid, "THE AGENT'S LATER WORK", at(minutes=-30), isSidechain=True)) + "\n")
        quiet = (NOW - timedelta(hours=5)).timestamp()
        os.utime(log, (quiet, quiet))  # the session itself wrote nothing since the first run
        later = dream.prepare(self.project, self.dream_session(), started=NOW + timedelta(hours=1))
        self.assertEqual([s["id"] for s in later["sessions"]], [sid])
        self.assertIn("THE AGENT'S LATER WORK", Path(later["workflow_args"]["transcripts"][0]).read_text())

    def test_an_agent_launched_on_a_rewound_branch_is_left_out(self):
        sid, when = new_id(), at(hours=1)
        call = lambda tool_id: assistant(sid, [{"type": "tool_use", "id": tool_id, "name": "Agent", "input": {}}], when)
        self.write_log(sid, self.linked(
            sid,
            ("first", None, user(sid, "Explore the parser.", when)),
            ("gone-call", "first", call("toolu_gone")),
            ("instead", None, user(sid, "Never mind. Explore the tests.", when)),  # rewound to before the first
            ("kept-call", "instead", call("toolu_kept")),
        ))
        agents = self.logs / sid / "subagents"
        agents.mkdir(parents=True)
        for name, tool_id, text in (("a", "toolu_gone", "ABANDONED AGENT'S WORK"), ("b", "toolu_kept", "KEPT AGENT'S WORK")):
            (agents / f"agent-{name}.jsonl").write_text(json.dumps(user(sid, text, when, isSidechain=True)) + "\n")
            (agents / f"agent-{name}.meta.json").write_text(json.dumps({"agentType": "Explore", "toolUseId": tool_id}))
        text = self.transcript(sid)
        self.assertNotIn("ABANDONED AGENT'S WORK", text)
        self.assertIn("KEPT AGENT'S WORK", text)

    def test_a_rewind_followed_by_a_bare_command_still_leaves_the_branch_out(self):
        sid, when = new_id(), at(hours=1)
        bare = ("<command-name>/hstack:align</command-name>\n<command-message>hstack:align</command-message>\n"
                "<command-args></command-args>")
        self.write_log(sid, self.linked(
            sid,
            ("q", None, user(sid, "How many stations?", when)),
            ("a", "q", assistant(sid, [{"type": "text", "text": "Three."}], when)),
            ("gone", "a", user(sid, "Decision: remove the inlet station.", when)),
            ("command", "a", user(sid, bare, when)),
            ("reply", "command", assistant(sid, [{"type": "text", "text": "What should I check?"}], when)),
        ))
        text = self.transcript(sid)
        self.assertNotIn("remove the inlet", text)
        self.assertIn("What should I check?", text)

    def test_a_rewind_followed_by_a_command_with_its_note_still_leaves_the_branch_out(self):
        sid, when = new_id(), at(hours=1)
        caveat = "<local-command-caveat>The command below was run directly in Claude Code.</local-command-caveat>"
        self.write_log(sid, self.linked(
            sid,
            ("q", None, user(sid, "How many stations?", when)),
            ("a", "q", assistant(sid, [{"type": "text", "text": "Three."}], when)),
            ("gone", "a", user(sid, "Decision: remove the inlet station.", when)),
            ("note", "a", user(sid, caveat, when, isMeta=True)),
            ("command", "note", user(sid, "<command-name>/reload-plugins</command-name>", when)),
            ("after", "command", user(sid, "How many tests?", when)),
        ))
        text = self.transcript(sid)
        self.assertNotIn("remove the inlet", text)
        self.assertIn("How many tests?", text)

    def test_a_workflow_launched_on_a_rewound_branch_is_left_out(self):
        sid, when = new_id(), at(hours=1)
        launch = assistant(sid, [{"type": "tool_use", "id": "toolu_wf", "name": "Workflow", "input": {}}], when)
        result = {**results(sid, [{"type": "tool_result", "tool_use_id": "toolu_wf", "content": "started"}], when),
                  "toolUseResult": {"status": "async_launched", "runId": "wf_gone"}}
        self.write_log(sid, self.linked(
            sid,
            ("first", None, user(sid, "Run the workflow.", when)),
            ("launch", "first", launch),
            ("result", "launch", result),
            ("instead", None, user(sid, "Never mind.", when)),  # rewound to before the first message
        ))
        agents = self.logs / sid / "subagents" / "workflows" / "wf_gone"
        agents.mkdir(parents=True)
        (agents / "agent-w1.jsonl").write_text(json.dumps(user(sid, "THE WORKFLOW AGENT'S WORK", when, isSidechain=True)) + "\n")
        (agents / "agent-w1.meta.json").write_text(json.dumps({"agentType": "general-purpose"}))
        text = self.transcript(sid)
        self.assertNotIn("THE WORKFLOW AGENT'S WORK", text)
        self.assertIn("Never mind.", text)

    def test_a_message_delivered_after_a_run_started_goes_to_the_next_run(self):
        sid = new_id()
        start = [user(sid, "Start", at(minutes=10)), assistant(sid, [{"type": "text", "text": "Working."}], at(minutes=10))]
        self.write_log(sid, start)
        self.complete(dream.prepare(self.project, self.dream_session(), started=NOW))
        meanwhile = [  # what Claude wrote while the message waited, after the run started
            assistant(sid, [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}], at(minutes=-1)),
            results(sid, [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}], at(minutes=-1)),
        ]
        queued = {"type": "attachment", "sessionId": sid, "timestamp": at(minutes=5), "attachment": {  # typed before
            "type": "queued_command", "prompt": "TYPED BEFORE THE RUN, DELIVERED AFTER", "origin": {"kind": "human"}}}
        self.write_log(sid, [*start, *meanwhile, queued])
        later = dream.prepare(self.project, self.dream_session(), started=NOW + timedelta(hours=1))
        self.assertEqual([s["id"] for s in later["sessions"]], [sid])
        self.assertIn("TYPED BEFORE THE RUN, DELIVERED AFTER", Path(later["workflow_args"]["transcripts"][0]).read_text())

    def test_a_rewind_followed_by_a_compaction_still_leaves_the_branch_out(self):
        sid, when = new_id(), at(hours=1)
        records = self.linked(
            sid,
            ("q", None, user(sid, "How many stations?", when)),
            ("a", "q", assistant(sid, [{"type": "text", "text": "Three."}], when)),
            ("gone", "a", user(sid, "Decision: remove the inlet station.", when)),
        )
        boundary = {"type": "system", "subtype": "compact_boundary", "uuid": new_id(), "parentUuid": None,
                    "logicalParentUuid": records[1]["uuid"]}  # compacted right after rewinding to the answer
        after = user(sid, "How many tests?", when, uuid=new_id(), parentUuid=boundary["uuid"])
        self.write_log(sid, [*records, boundary, after])
        text = self.transcript(sid)
        self.assertNotIn("remove the inlet", text)
        self.assertIn("How many tests?", text)

    def test_a_forks_copies_keep_their_old_times_when_its_original_is_gone(self):
        parent, records = self.parent(days=20)  # older than the first run's 14 days
        sid = new_id()
        bookkeeping = {"type": "queue-operation", "operation": "enqueue", "timestamp": at(hours=1), "sessionId": sid}
        derived = {"type": "derived-from", "v": 1, "sessionId": sid,
                   "sessions": [{"type": "claude_code_session", "id": parent, "via": "fork"}]}
        copies = [{**r, "sessionId": sid} for r in records]
        self.write_log(sid, [bookkeeping, derived, *copies, user(sid, "FORK FACT", at(hours=1), uuid=new_id())])
        (self.logs / f"{parent}.jsonl").unlink()
        prepared = dream.prepare(self.project, self.dream_session(), started=NOW)
        text = Path(prepared["workflow_args"]["transcripts"][0]).read_text()
        self.assertIn("FORK FACT", text)
        self.assertNotIn("PARENT FACT", text)

    def test_a_log_the_guard_cannot_write_never_keeps_its_decision_back(self):
        dreaming = self.project / ".dreaming"
        (dreaming / "state").mkdir(parents=True)
        dreaming.chmod(0)
        self.addCleanup(dreaming.chmod, 0o755)
        hook = SCRIPTS.parents[2] / "hooks" / "dream_guard.py"
        call = {"agent_type": "hstack:memory-organizer", "tool_name": "Bash", "cwd": str(self.project),
                "tool_input": {"command": "true"}}
        done = subprocess.run([sys.executable, str(hook)], input=json.dumps(call), capture_output=True, text=True,
                              env={**os.environ, "CLAUDE_PROJECT_DIR": str(self.project)})
        self.assertEqual(json.loads(done.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_a_session_started_in_the_same_second_as_a_run_is_taken_once(self):
        sid, when = new_id(), "2026-11-24T12:00:00.400Z"
        self.write_log(sid, [user(sid, "Same second", when), assistant(sid, [{"type": "text", "text": "Ok."}], when)])
        first = dream.prepare(self.project, self.dream_session(), started=NOW + timedelta(milliseconds=900))
        self.assertIn(sid, [s["id"] for s in first["sessions"]])
        self.complete(first)
        later = dream.prepare(self.project, self.dream_session(), started=NOW + timedelta(hours=1))
        self.assertNotIn(sid, [s["id"] for s in later.get("sessions", [])])


if __name__ == "__main__":
    unittest.main()
