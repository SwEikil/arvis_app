from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_state
from conversation_summary import new_session_id


VALID_SUMMARY = """Goal: test
Confirmed facts: None
Constraints: None
Decisions: None
Open questions: None
Next actions: None
Names/identifiers: None"""


class RuntimeStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_cwd = os.getcwd()
        self._tmpdir = tempfile.TemporaryDirectory()
        os.chdir(self._tmpdir.name)

    def tearDown(self) -> None:
        os.chdir(self._original_cwd)
        self._tmpdir.cleanup()

    def test_runtime_path_is_dot_runtime(self) -> None:
        self.assertEqual(runtime_state.RUNTIME_DIR, Path(".runtime"))
        self.assertEqual(runtime_state.RELOAD_STATE_FILE, Path(".runtime") / "reload_state.json")

    def test_save_reload_state_writes_json(self) -> None:
        session_id = new_session_id()
        saved = runtime_state.save_reload_state(
            dry_run=False,
            debug=True,
            session_id=session_id,
            session_summary=VALID_SUMMARY,
            active_history=[{"role": "user", "content": "hello"}],
            command_history=[{"counter": 1, "normalized_action": "volume_up"}],
            command_counter=1,
        )

        self.assertTrue(saved)
        self.assertTrue((Path(".runtime") / "reload_state.json").exists())

        state = runtime_state.load_reload_state()
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["dry_run"], False)
        self.assertEqual(state["debug"], True)
        self.assertEqual(state["session_id"], session_id)
        self.assertEqual(state["session_summary"], VALID_SUMMARY)
        self.assertEqual(state["active_history"], [{"role": "user", "content": "hello"}])
        self.assertEqual(state["command_history"], [{"counter": 1, "normalized_action": "volume_up"}])
        self.assertEqual(state["command_counter"], 1)

    def test_load_reload_state_restores_minimal_state(self) -> None:
        runtime_state.save_reload_state(
            dry_run=True,
            debug=False,
            session_id=new_session_id(),
            session_summary="",
        )

        state = runtime_state.load_reload_state()

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["dry_run"], True)
        self.assertEqual(state["debug"], False)
        self.assertEqual(state["session_summary"], "")
        self.assertFalse((Path(".runtime") / "reload_state.json").exists())

    def test_snapshot_uses_private_permissions(self) -> None:
        saved = runtime_state.save_reload_state(
            dry_run=True,
            debug=False,
            session_id=new_session_id(),
            session_summary="",
        )

        self.assertTrue(saved)
        self.assertEqual(Path(".runtime").stat().st_mode & 0o777, 0o700)
        self.assertEqual((Path(".runtime") / "reload_state.json").stat().st_mode & 0o777, 0o600)

    def test_invalid_uuid_is_rejected_and_snapshot_is_one_shot(self) -> None:
        Path(".runtime").mkdir()
        (Path(".runtime") / "reload_state.json").write_text(
            '{"dry_run":true,"debug":false,"session_id":"bad","session_summary":"","active_history":[]}',
            encoding="utf-8",
        )

        self.assertIsNone(runtime_state.load_reload_state())
        self.assertFalse((Path(".runtime") / "reload_state.json").exists())

    def test_corrupted_history_is_rejected(self) -> None:
        session_id = new_session_id()
        Path(".runtime").mkdir()
        payload = (
            '{"dry_run":true,"debug":false,"session_id":"'
            + session_id
            + '","session_summary":"","active_history":[{"role":"assistant","content":"orphan"}]}'
        )
        (Path(".runtime") / "reload_state.json").write_text(payload, encoding="utf-8")

        self.assertIsNone(runtime_state.load_reload_state())

    def test_corrupt_reload_state_does_not_crash(self) -> None:
        Path(".runtime").mkdir()
        (Path(".runtime") / "reload_state.json").write_text("{not json", encoding="utf-8")

        state = runtime_state.load_reload_state()

        self.assertIsNone(state)
        self.assertFalse((Path(".runtime") / "reload_state.json").exists())

    def test_atomic_replace_uses_temporary_file_in_runtime_directory(self) -> None:
        with patch("runtime_state.os.replace", wraps=os.replace) as replace:
            saved = runtime_state.save_reload_state(
                dry_run=True,
                debug=False,
                session_id=new_session_id(),
                session_summary="",
            )

        self.assertTrue(saved)
        source, destination = replace.call_args.args
        self.assertEqual(Path(source).parent, runtime_state.RUNTIME_DIR)
        self.assertEqual(Path(destination).parent, runtime_state.RUNTIME_DIR)
        self.assertEqual(Path(destination), runtime_state.RELOAD_STATE_FILE)

    def test_replace_failure_removes_temporary_file_and_returns_false(self) -> None:
        with patch("runtime_state.os.replace", side_effect=RuntimeError("replace failed")):
            saved = runtime_state.save_reload_state(
                dry_run=True,
                debug=False,
                session_id=new_session_id(),
                session_summary="",
            )

        self.assertFalse(saved)
        self.assertEqual(list(Path(".runtime").glob(".reload_state.*.tmp")), [])
        self.assertFalse(runtime_state.RELOAD_STATE_FILE.exists())

    def test_chmod_exceptions_do_not_fail_snapshot_write(self) -> None:
        with patch("runtime_state.Path.chmod", side_effect=RuntimeError("chmod unavailable")):
            saved = runtime_state.save_reload_state(
                dry_run=True,
                debug=False,
                session_id=new_session_id(),
                session_summary="",
            )

        self.assertTrue(saved)
        self.assertTrue(runtime_state.RELOAD_STATE_FILE.exists())

    def test_snapshot_validation_exception_returns_false_without_content(self) -> None:
        with patch("runtime_state.validate_reload_summary", side_effect=RuntimeError("raw conversation")):
            saved = runtime_state.save_reload_state(
                dry_run=True,
                debug=False,
                session_id=new_session_id(),
                session_summary="",
            )

        self.assertFalse(saved)
        self.assertFalse(runtime_state.RELOAD_STATE_FILE.exists())

    def test_reload_accepts_unknown_additional_fields(self) -> None:
        session_id = new_session_id()
        payload = {
            "dry_run": True,
            "debug": False,
            "session_id": session_id,
            "session_summary": "",
            "active_history": [],
            "future_optional_field": {"version": 2},
        }
        Path(".runtime").mkdir()
        runtime_state.RELOAD_STATE_FILE.write_text(json.dumps(payload), encoding="utf-8")

        state = runtime_state.load_reload_state()

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["session_id"], session_id)
        self.assertNotIn("future_optional_field", state)

    def test_snapshot_rejects_summary_and_history_over_hard_bounds(self) -> None:
        oversized_summary = VALID_SUMMARY.replace("Goal: test", "Goal: " + "x" * 4_000)
        oversized_history = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": "x"}
            for index in range(41)
        ]
        oversized_char_history = [
            {"role": "user", "content": "x" * 32_001},
            {"role": "assistant", "content": "answer"},
        ]

        for candidate_summary, candidate_history in (
            (oversized_summary, []),
            ("", oversized_history),
            ("", oversized_char_history),
        ):
            with self.subTest(messages=len(candidate_history)):
                self.assertFalse(
                    runtime_state.save_reload_state(
                        dry_run=True,
                        debug=False,
                        session_id=new_session_id(),
                        session_summary=candidate_summary,
                        active_history=candidate_history,
                    )
                )

    def test_restart_current_process_calls_execv_with_current_python_and_argv(self) -> None:
        with patch.object(sys, "executable", "/tmp/python"), patch.object(
            sys,
            "argv",
            ["main.py", "--flag"],
        ), patch("runtime_state.os.execv") as execv:
            runtime_state.restart_current_process()

        execv.assert_called_once_with("/tmp/python", ["/tmp/python", "main.py", "--flag"])


if __name__ == "__main__":
    unittest.main()
