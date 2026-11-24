"""Offline tests for hooks/premise_cue.py. Each test feeds the hook the events Claude Code would send, one process
per event as Claude Code runs it, with the plugin's data folder in a temporary folder. From the plugin's root folder:

    python3 -m unittest discover -s hooks/tests -v
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "premise_cue.py"
PRINCIPLE = HOOK.parents[1] / "skills" / "principle-attack-the-premise" / "SKILL.md"


class PremiseCueTest(unittest.TestCase):
    def setUp(self):
        self._data = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._data.name)
        self.session = "session-1"

    def tearDown(self):
        self._data.cleanup()

    def run_hook(self, payload, env_data=True) -> str | None:
        """The note the hook adds, or None when it prints nothing. Fails the test on anything else."""
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_DATA"}
        if env_data:
            env["CLAUDE_PLUGIN_DATA"] = str(self.data_dir)
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        done = subprocess.run([sys.executable, "-I", str(HOOK)], input=stdin, capture_output=True, text=True,
                              env=env, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr, "")
        if not done.stdout.strip():
            return None
        out = json.loads(done.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUseFailure")
        return out["hookSpecificOutput"]["additionalContext"]

    def base(self, agent=None, cwd="/repo"):
        data = {"session_id": self.session, "cwd": cwd}
        if agent:
            data |= {"agent_id": agent, "agent_type": "general-purpose"}
        return data

    def edit(self, agent=None):
        return self.run_hook(self.base(agent) | {"hook_event_name": "PostToolUse", "tool_name": "Edit",
                                                 "tool_input": {"file_path": "/repo/a.py"}})

    def bash_ok(self, command, agent=None, background=False, cwd="/repo", **response):
        given = {"command": command} | ({"run_in_background": True} if background else {})
        result = {"stdout": "", "stderr": "", "interrupted": False} | response
        return self.run_hook(self.base(agent, cwd) | {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                                      "tool_input": given, "tool_response": result})

    def bash_fail(self, command, agent=None, error="Exit code 1\nFAILED tests/test_a.py::test_x", interrupt=False,
                  cwd="/repo"):
        return self.run_hook(self.base(agent, cwd) | {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                                                      "tool_input": {"command": command}, "error": error,
                                                      "is_interrupt": interrupt})

    def two_failed_fixes(self, command="pytest tests/test_a.py"):
        """Reproduce, then fix and fail twice. Returns the note from the last failure."""
        self.assertIsNone(self.bash_fail(command))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail(command))
        self.assertIsNone(self.edit())
        return self.bash_fail(command)

    def test_second_failed_fix_gets_a_note_naming_the_command_and_principle(self):
        note = self.two_failed_fixes()
        self.assertIsNotNone(note)
        self.assertIn("`pytest tests/test_a.py`", note)
        self.assertIn(str(PRINCIPLE), note)
        self.assertTrue(PRINCIPLE.is_file())

    def test_reruns_without_an_edit_between_are_not_fixes(self):
        self.assertIsNone(self.bash_fail("make check"))
        self.assertIsNone(self.bash_fail("make check"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("make check"))
        self.assertIsNone(self.bash_fail("make check"))

    def test_the_first_failure_only_reproduces_the_problem(self):
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.edit())
        self.assertIsNotNone(self.bash_fail("npm test"))

    def test_a_success_resets_the_count(self):
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_ok("npm test"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test"))

    def test_each_command_counts_on_its_own(self):
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test"))  # npm test: 1 failed fix
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm run lint"))  # lint: first failure, only reproduces
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm run lint"))  # lint: 1 failed fix
        self.assertIsNotNone(self.bash_fail("npm test"))  # npm test: 2, with edits since its last run

    def test_only_whitespace_around_the_command_is_ignored(self):
        self.assertIsNone(self.bash_fail("pytest 'a b.py'"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail(" pytest 'a b.py'\n"))  # fix 1 of the same check
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("pytest 'a  b.py'"))  # a different file: a different check
        self.assertIsNotNone(self.bash_fail("pytest 'a b.py'"))

    def test_the_same_command_in_another_directory_is_another_check(self):
        self.assertIsNone(self.bash_fail("npm test", cwd="/repo/a"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test", cwd="/repo/a"))  # a: 1 failed fix
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test", cwd="/repo/b"))  # b: first failure, only reproduces
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test", cwd="/repo/b"))  # b: 1 failed fix
        self.assertIsNotNone(self.bash_fail("npm test", cwd="/repo/a"))  # a: 2

    def test_a_note_resets_the_count(self):
        self.assertIsNotNone(self.two_failed_fixes("cargo test"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("cargo test"))
        self.assertIsNone(self.edit())
        self.assertIsNotNone(self.bash_fail("cargo test"))

    def test_interrupts_and_failures_without_an_exit_code_are_ignored(self):
        self.assertIsNone(self.bash_fail("go test ./..."))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("go test ./..."))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("go test ./...", error="Exit code 130", interrupt=True))
        self.assertIsNone(self.bash_fail("go test ./...", error="Command timed out after 2m 0s"))
        self.assertIsNone(self.bash_fail("go test ./...", error="Exit code 143\nCommand timed out after 2m 0s"))
        self.assertIsNone(self.bash_fail("go test ./...", error="Exit code 255\nCommand timed out after 60s\n\n"
                                                                "Command timed out after 60s"))
        self.assertIsNotNone(self.bash_fail("go test ./..."))

    def test_a_test_that_times_out_is_still_a_failed_check(self):
        for n, own_timeout in enumerate(("Exit code 1\npanic: test timed out after 10m0s",
                                         "Exit code 1\nCommand timed out after 5000 milliseconds: worker")):
            with self.subTest(own_timeout=own_timeout):
                self.session = f"own-timeout-{n}"
                self.assertIsNone(self.bash_fail("npm test", error=own_timeout))
                self.assertIsNone(self.edit())
                self.assertIsNone(self.bash_fail("npm test", error=own_timeout))
                self.assertIsNone(self.edit())
                self.assertIsNotNone(self.bash_fail("npm test", error=own_timeout))

    def test_a_result_that_is_not_a_plain_pass_does_not_reset_the_count(self):
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.bash_ok("npm test", background=True, backgroundTaskId="b1"))
        self.assertIsNone(self.bash_ok("npm test", backgroundTaskId="b2", timedOutAfterMs=120000))
        self.assertIsNone(self.bash_ok("npm test", interrupted=True))
        self.assertIsNone(self.edit())
        self.assertIsNotNone(self.bash_fail("npm test"))

    def test_an_exit_1_read_as_a_normal_result_neither_resets_nor_counts(self):
        check = "diff expected.txt actual.txt"
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.bash_ok("npm test", returnCodeInterpretation="Files differ"))
        for _ in range(3):
            self.assertIsNone(self.edit())
            self.assertIsNone(self.bash_ok(check, returnCodeInterpretation="Files differ"))
        self.assertIsNone(self.edit())
        self.assertIsNotNone(self.bash_fail("npm test"))

    def test_each_agent_counts_on_its_own(self):
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.edit(agent="sub1"))
        self.assertIsNone(self.bash_fail("npm test"))  # the subagent's edit is not the main agent's fix
        self.assertIsNone(self.edit())
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertIsNone(self.bash_fail("npm test", agent="sub1"))
        self.assertIsNone(self.edit(agent="sub1"))
        self.assertIsNone(self.bash_fail("npm test", agent="sub1"))
        self.assertIsNone(self.edit())
        self.assertIsNotNone(self.bash_fail("npm test"))

    def test_input_it_cannot_use_prints_nothing(self):
        self.assertIsNone(self.run_hook("not json"))
        self.assertIsNone(self.run_hook("[]"))
        self.assertIsNone(self.run_hook({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash"}))
        self.assertIsNone(self.run_hook(self.base() | {"hook_event_name": "PostToolUse", "tool_name": "Read",
                                                       "tool_input": {"file_path": "/repo/a.py"}}))
        self.session = "../escape"
        self.assertIsNone(self.bash_fail("npm test"))
        self.assertEqual(list(self.data_dir.rglob("*")), [])

    def test_without_a_data_folder_it_does_nothing(self):
        payload = self.base() | {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                                 "tool_input": {"command": "npm test"}, "error": "Exit code 1"}
        for _ in range(5):
            self.assertIsNone(self.run_hook(payload, env_data=False))

    def test_a_new_log_deletes_logs_untouched_for_a_week(self):
        folder = self.data_dir / "premise-cue"
        folder.mkdir()
        old, recent = folder / "old.main.jsonl", folder / "recent.main.jsonl"
        old.write_text('{"kind": "edit"}\n')
        recent.write_text('{"kind": "edit"}\n')
        week_ago = time.time() - 8 * 24 * 3600
        os.utime(old, (week_ago, week_ago))
        self.edit()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue((folder / f"{self.session}.main.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
