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
        self.assertEqual(state["last_run"], "2026-11-10T12:00:00Z")  # the first window, kept until a complete finish
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
        self.assertEqual(state["last_run"], "2026-11-24T12:00:00Z")

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
        self.assertEqual(state["last_run"], "2026-11-24T12:00:00Z")
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
        self.assertEqual(state["last_run"], "2026-11-10T12:00:00Z")  # the first window is kept

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
        self.assertEqual(state["last_run"], "2026-11-10T12:00:00Z")  # unchanged

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
        self.assertEqual(claude_logs.created_at(claude_logs.read_records(self.logs / f"{sid}.jsonl")), new)
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
        self.assertNotIn(sid, [s["id"] for s in later.get("sessions", [])])

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

    def test_saved_outputs_are_read_wherever_claude_code_saved_them(self):
        parent = self.logs / new_id() / "tool-results"
        parent.mkdir(parents=True)
        (parent / "out.txt").write_text("PARENT'S SAVED OUTPUT")
        forked = self.write_tool_result(new_id(), (
            "<persisted-output>\nOutput too large (40KB). Full output saved to: "
            f"{parent}/out.txt\n\nPreview (first 2KB):\nPREVIEW\n</persisted-output>"))
        self.assertIn("PARENT'S SAVED OUTPUT", forked)
        self.assertNotIn("NOTE ABOUT THIS FILE", forked)

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


if __name__ == "__main__":
    unittest.main()
