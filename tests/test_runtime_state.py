from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_state


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
        saved = runtime_state.save_reload_state(
            dry_run=False,
            debug=True,
            session_summary="summary",
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
        self.assertEqual(state["session_summary"], "summary")
        self.assertEqual(state["active_history"], [{"role": "user", "content": "hello"}])
        self.assertEqual(state["command_history"], [{"counter": 1, "normalized_action": "volume_up"}])
        self.assertEqual(state["command_counter"], 1)

    def test_load_reload_state_restores_minimal_state(self) -> None:
        runtime_state.save_reload_state(
            dry_run=True,
            debug=False,
            session_summary="restored",
        )

        state = runtime_state.load_reload_state()

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["dry_run"], True)
        self.assertEqual(state["debug"], False)
        self.assertEqual(state["session_summary"], "restored")
        self.assertFalse((Path(".runtime") / "reload_state.json").exists())

    def test_corrupt_reload_state_does_not_crash(self) -> None:
        Path(".runtime").mkdir()
        (Path(".runtime") / "reload_state.json").write_text("{not json", encoding="utf-8")

        state = runtime_state.load_reload_state()

        self.assertIsNone(state)
        self.assertFalse((Path(".runtime") / "reload_state.json").exists())

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
