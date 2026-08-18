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


if __name__ == "__main__":
    unittest.main()
