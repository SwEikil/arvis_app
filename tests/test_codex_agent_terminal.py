from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import codex_agent_terminal
import codex_agent_worker


class CodexAgentTerminalTests(unittest.TestCase):
    def test_worker_extracts_session_id_from_bounded_event_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            events = Path(tmpdir) / "events.jsonl"
            events.write_text('{"type":"turn.started"}\n{"type":"thread.started","thread_id":"session-123"}\n', encoding="utf-8")
            self.assertEqual(codex_agent_worker.session_id_from_events(events), "session-123")
            self.assertEqual(codex_agent_terminal.session_id_from_events(events), "session-123")

    def test_latest_final_answer_uses_exact_session_and_final_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / "codex"
            session = codex_home / "sessions" / "2026" / "08" / "18" / "rollout-session-123.jsonl"
            session.parent.mkdir(parents=True)
            rows = [
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": "progress"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": "final result"}]}},
            ]
            session.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                self.assertEqual(codex_agent_terminal.latest_final_answer("session-123"), "final result")

    def test_safe_child_env_does_not_forward_arbitrary_or_desktop_launcher_values(self) -> None:
        with patch.dict(os.environ, {"HOME": "/tmp/home", "PATH": "/usr/bin", "SECRET_TOKEN": "secret", "BASH_ENV": "/tmp/unsafe"}, clear=True):
            result = codex_agent_terminal.safe_child_env()
        self.assertEqual(result, {"HOME": "/tmp/home", "PATH": "/usr/bin"})


if __name__ == "__main__":
    unittest.main()
