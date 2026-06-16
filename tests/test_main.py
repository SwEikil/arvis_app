from __future__ import annotations

import unittest
from unittest.mock import Mock
from unittest.mock import patch

import main


class MainReloadCommandTests(unittest.TestCase):
    def test_reload_command_is_recognized(self) -> None:
        router = Mock()
        router.dry_run = False

        with patch("main.save_reload_state", return_value=True) as save_state, patch(
            "main.restart_current_process",
        ) as restart:
            result = main.handle_command(
                "/reload",
                [{"role": "user", "content": "hello"}],
                "summary",
                True,
                router,
                [{"counter": 1}],
                1,
            )

        self.assertTrue(result.handled)
        self.assertFalse(result.exit_requested)
        save_state.assert_called_once()
        restart.assert_called_once()

    def test_restart_command_is_recognized(self) -> None:
        router = Mock()
        router.dry_run = True

        with patch("main.save_reload_state", return_value=True), patch("main.restart_current_process") as restart:
            result = main.handle_command("/restart", [], "", False, router, [], 0)

        self.assertTrue(result.handled)
        restart.assert_called_once()

    def test_reload_does_not_call_router(self) -> None:
        router = Mock()
        router.dry_run = True

        with patch("main.save_reload_state", return_value=True), patch("main.restart_current_process"):
            main.handle_command("/reload", [], "", False, router, [], 0)

        router.route.assert_not_called()

    def test_reload_does_not_touch_minecraft_server(self) -> None:
        router = Mock()
        router.dry_run = True

        with patch("main.save_reload_state", return_value=True), patch("main.restart_current_process"), patch(
            "command_router.execute_minecraft_server_action",
        ) as minecraft_action:
            main.handle_command("/reload", [], "", False, router, [], 0)

        minecraft_action.assert_not_called()

    def test_reload_restart_error_is_reported_without_crashing(self) -> None:
        router = Mock()
        router.dry_run = True

        with patch("main.save_reload_state", return_value=True), patch(
            "main.restart_current_process",
            side_effect=OSError("exec failed"),
        ):
            result = main.handle_command("/reload", [], "summary", True, router, [], 0)

        self.assertTrue(result.handled)
        self.assertFalse(result.exit_requested)
        self.assertEqual(result.session_summary, "summary")
        self.assertTrue(result.debug)

    def test_restore_runtime_state_restores_minimal_fields(self) -> None:
        router = Mock()
        router.dry_run = True
        active_history: list[dict[str, str]] = []
        command_history: list[dict[str, object]] = []

        session_summary, debug, command_counter, restored = main.restore_runtime_state(
            {
                "dry_run": False,
                "debug": True,
                "session_summary": "summary",
                "active_history": [{"role": "user", "content": "hello"}],
                "command_history": [{"counter": 3, "normalized_action": "volume_up"}],
            },
            active_history,
            command_history,
            router,
        )

        self.assertTrue(restored)
        self.assertFalse(router.dry_run)
        self.assertTrue(debug)
        self.assertEqual(session_summary, "summary")
        self.assertEqual(active_history, [{"role": "user", "content": "hello"}])
        self.assertEqual(command_history, [{"counter": 3, "normalized_action": "volume_up"}])
        self.assertEqual(command_counter, 3)


if __name__ == "__main__":
    unittest.main()
