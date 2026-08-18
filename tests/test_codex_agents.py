from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import codex_agents
from mcp_access import load_mcp_access_config
from project_context import ProjectContextError


class CodexAgentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.workspace = base / "workspace"
        self.workspace.mkdir()
        self.state = base / "state"
        self.config = load_mcp_access_config(
            environ={
                "ARVIS_MCP_PROFILE": "codex",
                "ARVIS_MCP_PROJECT_ROOT": str(self.workspace),
                "ARVIS_MCP_ALLOWED_ROOTS": str(self.workspace),
            },
            cwd=self.workspace,
        )
        self.environment = patch.dict(os.environ, {
            "ARVIS_CODEX_AGENT_CONTROL_ENABLED": "true",
            "ARVIS_CODEX_AGENT_STATE_ROOT": str(self.state),
            "ARVIS_CODEX_VISIBLE_TERMINAL_ENABLED": "true",
        })
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temp.cleanup()

    @patch("codex_agents.subprocess.Popen")
    def test_create_records_worker_pid_and_keeps_state_outside_workspace(self, popen: Mock) -> None:
        popen.return_value.pid = 12345
        created = codex_agents.create_agent("Inspect VERSION", str(self.workspace), access_config=self.config)
        status = codex_agents.get_status(created["agent_id"], workspace_hint=str(self.workspace), access_config=self.config)
        stored = json.loads((self.state / created["agent_id"] / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["pid"], 12345)
        self.assertEqual(status["status"], "initializing")
        self.assertFalse(self.state.is_relative_to(self.workspace))

    @patch("codex_agents.subprocess.Popen")
    def test_initial_handoff_is_bounded_and_recorded(self, popen: Mock) -> None:
        popen.return_value.pid = 12345
        created = codex_agents.create_agent("Continue", str(self.workspace), handoff_text="Preserved facts", access_config=self.config)
        request = json.loads((self.state / created["agent_id"] / "request.json").read_text(encoding="utf-8"))
        self.assertTrue(created["handoff_supplied"])
        self.assertTrue(request["handoff_supplied"])
        self.assertIn("Preserved facts", request["task"])

        with self.assertRaises(ProjectContextError):
            codex_agents.create_agent("Continue", str(self.workspace), handoff_text="x" * (codex_agents.MAX_HANDOFF_CHARS + 1), access_config=self.config)

    def test_state_root_inside_workspace_is_rejected(self) -> None:
        with patch.dict(os.environ, {"ARVIS_CODEX_AGENT_STATE_ROOT": str(self.workspace / "state")}):
            with self.assertRaises(ProjectContextError):
                codex_agents.create_agent("Task", str(self.workspace), access_config=self.config)

    def test_result_is_bounded_and_redacted(self) -> None:
        agent_id = "a" * 32
        directory = self.state / agent_id
        directory.mkdir(parents=True)
        (directory / "status.json").write_text(json.dumps({"agent_id": agent_id, "status": "completed"}), encoding="utf-8")
        (directory / "result.md").write_text("API_KEY=fake-secret\n" + "x" * 1000, encoding="utf-8")
        result = codex_agents.get_result(agent_id, max_chars=500, workspace_hint=str(self.workspace), access_config=self.config)
        self.assertNotIn("fake-secret", result["result"])
        self.assertTrue(result["truncated"])

    @patch("codex_agents._launch_terminal")
    @patch("codex_agents.subprocess.Popen")
    def test_visible_create_opens_same_agent_without_duplicate_worker(self, popen: Mock, launch: Mock) -> None:
        popen.return_value.pid = 12345
        launch.return_value = {"requested": True, "opened": True, "same_session": True, "mode": "read_only_then_interactive_resume"}

        created = codex_agents.create_agent("Inspect", str(self.workspace), visible=True, access_config=self.config)

        self.assertEqual(popen.call_count, 1)
        launch.assert_called_once()
        self.assertTrue(created["visibility"]["same_session"])

    @patch("codex_agents.subprocess.Popen")
    @patch("codex_agents._konsole_instance_registered", return_value=False)
    @patch("codex_agents._konsole_is_running", return_value=True)
    @patch("codex_agents.shutil.which", return_value="/usr/bin/konsole")
    def test_terminal_launcher_is_fixed_and_reuses_existing_konsole(self, _which: Mock, _running: Mock, _registered: Mock, popen: Mock) -> None:
        popen.return_value.pid = 22222
        agent_id = "b" * 32
        directory = self.state / agent_id
        directory.mkdir(parents=True)
        request = {"agent_id": agent_id, "workspace": str(self.workspace), "mode": "read_only", "task": "Inspect"}
        (directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
        (directory / "status.json").write_text(json.dumps({"agent_id": agent_id, "status": "completed"}), encoding="utf-8")

        result = codex_agents.show_agent(agent_id, workspace_hint=str(self.workspace), access_config=self.config)

        argv = popen.call_args.args[0]
        self.assertEqual(argv[0:3], ["/usr/bin/konsole", "--force-reuse", "--new-tab"])
        self.assertIn("codex_agent_terminal.py", " ".join(argv))
        self.assertNotIn("Inspect", argv)
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(result["terminal_target"], "existing_tab")
        self.assertTrue(result["same_session"])

    @patch("codex_agents.subprocess.Popen")
    @patch("codex_agents._konsole_is_running", return_value=False)
    @patch("codex_agents.shutil.which", return_value="/usr/bin/konsole")
    def test_terminal_launcher_falls_back_to_new_window_with_fixed_argv(self, _which: Mock, _reuse: Mock, popen: Mock) -> None:
        popen.return_value.pid = 22222
        agent_id = "c" * 32
        directory = self.state / agent_id
        directory.mkdir(parents=True)
        request = {"agent_id": agent_id, "workspace": str(self.workspace), "mode": "read_only", "task": "Inspect"}
        (directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
        (directory / "status.json").write_text(json.dumps({"agent_id": agent_id, "status": "completed"}), encoding="utf-8")

        result = codex_agents.show_agent(agent_id, workspace_hint=str(self.workspace), access_config=self.config)

        argv = popen.call_args.args[0]
        self.assertEqual(argv[0:2], ["/usr/bin/konsole", "--workdir"])
        self.assertNotIn("Inspect", argv)
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(result["terminal_target"], "new_window")

    @patch("codex_agents.subprocess.Popen")
    @patch("codex_agents._konsole_instance_registered", return_value=True)
    @patch("codex_agents._konsole_is_running", return_value=True)
    @patch("codex_agents.shutil.which", return_value="/usr/bin/konsole")
    def test_terminal_launcher_reports_new_window_when_konsole_refuses_reuse(self, _which: Mock, _running: Mock, _registered: Mock, popen: Mock) -> None:
        popen.return_value.pid = 33333
        agent_id = "d" * 32
        directory = self.state / agent_id
        directory.mkdir(parents=True)
        request = {"agent_id": agent_id, "workspace": str(self.workspace), "mode": "read_only", "task": "Inspect"}
        (directory / "request.json").write_text(json.dumps(request), encoding="utf-8")
        (directory / "status.json").write_text(json.dumps({"agent_id": agent_id, "status": "completed"}), encoding="utf-8")

        result = codex_agents.show_agent(agent_id, workspace_hint=str(self.workspace), access_config=self.config)
        status = json.loads((directory / "status.json").read_text(encoding="utf-8"))

        self.assertTrue(result["reuse_requested"])
        self.assertEqual(result["terminal_target"], "new_window")
        self.assertEqual(status["terminal_target"], "new_window")

    @patch("codex_agents.subprocess.Popen")
    def test_workspace_write_requires_explicit_allowed_root(self, popen: Mock) -> None:
        popen.return_value.pid = 12345
        denied = load_mcp_access_config(
            environ={
                "ARVIS_MCP_PROFILE": "chatgpt",
                "ARVIS_MCP_PROJECT_ROOT": str(self.workspace),
                "ARVIS_MCP_ALLOWED_ROOTS": str(self.workspace),
            },
            cwd=self.workspace,
        )
        allowed = load_mcp_access_config(
            environ={
                "ARVIS_MCP_PROFILE": "chatgpt",
                "ARVIS_MCP_PROJECT_ROOT": str(self.workspace),
                "ARVIS_MCP_ALLOWED_ROOTS": str(self.workspace),
                "ARVIS_MCP_WRITABLE_ROOTS": str(self.workspace),
            },
            cwd=self.workspace,
        )

        with self.assertRaises(ProjectContextError):
            codex_agents.create_agent("Write", str(self.workspace), mode="workspace_write", access_config=denied)
        created = codex_agents.create_agent("Write", str(self.workspace), mode="workspace_write", access_config=allowed)
        self.assertEqual(created["status"], "initializing")


if __name__ == "__main__":
    unittest.main()
