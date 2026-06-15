from __future__ import annotations

import unittest

from command_router import CommandResult
from response_renderer import render_final_response


class ResponseRendererTests(unittest.TestCase):
    def test_no_command_result_returns_assistant_message(self) -> None:
        self.assertEqual(render_final_response("Привіт.", None), "Привіт.")

    def test_minecraft_start_already_running_unmanaged(self) -> None:
        result = self._result(
            action="minecraft_server_start",
            status="already_running",
            reason_code="minecraft_server_already_running_unmanaged",
            message="router message",
        )

        rendered = render_final_response("Мені потрібна адреса сервера.", result)

        self.assertIn("Minecraft server уже працює", rendered)
        self.assertIn("не через Арвіса/tmux", rendered)
        self.assertIn("не запускав другий екземпляр", rendered)
        self.assertNotIn("адреса", rendered)

    def test_minecraft_status_unmanaged(self) -> None:
        result = self._result(
            action="minecraft_server_status",
            status="executed",
            reason_code="minecraft_server_running",
            message="router message",
            details="\n".join(
                [
                    "running: True",
                    "managed_by_tmux: False",
                    "unmanaged_java_process_found: True",
                    "control_available: False",
                ]
            ),
        )

        rendered = render_final_response("Потрібен IP.", result)

        self.assertIn("Minecraft server працює", rendered)
        self.assertIn("не через Арвіса/tmux", rendered)
        self.assertIn("читати логи", rendered)
        self.assertIn("stop/restart", rendered)
        self.assertNotIn("IP", rendered)

    def test_minecraft_start_executed(self) -> None:
        result = self._result(
            action="minecraft_server_start",
            status="executed",
            reason_code="minecraft_server_started",
            message="started",
            executed=True,
        )

        self.assertEqual(render_final_response("", result), "Запустив Minecraft server, сер.")

    def test_minecraft_stop_unmanaged(self) -> None:
        result = self._result(
            action="minecraft_server_stop",
            status="unsupported",
            reason_code="minecraft_server_unmanaged",
            message="unsupported",
        )

        rendered = render_final_response("", result)

        self.assertIn("не під керуванням Арвіса/tmux", rendered)
        self.assertIn("не можу безпечно", rendered)
        self.assertIn("Зупиніть його вручну", rendered)

    def test_minecraft_stop_not_running(self) -> None:
        result = self._result(
            action="minecraft_server_stop",
            status="not_running",
            reason_code="minecraft_server_not_running",
            message="not running",
        )

        self.assertEqual(render_final_response("", result), "Minecraft server уже не працює, сер.")

    def test_minecraft_metrics(self) -> None:
        result = self._result(
            action="minecraft_server_metrics",
            status="executed",
            reason_code="minecraft_server_metrics",
            message="metrics",
        )

        self.assertEqual(render_final_response("", result), "Показую навантаження Minecraft server, сер.")

    def test_dry_run(self) -> None:
        result = self._result(action="volume_up", status="dry_run", reason_code="volume_dry_run", message="dry-run")

        rendered = render_final_response("", result)

        self.assertIn("Dry-run", rendered)
        self.assertIn("volume_up", rendered)
        self.assertIn("реальна команда не запускалась", rendered)

    def test_unsupported_like_current_song(self) -> None:
        result = self._result(
            action="music_like_current",
            status="unsupported",
            reason_code="spotify_api_required",
            message="Spotify API required",
        )

        rendered = render_final_response("", result)

        self.assertIn("Spotify API", rendered)
        self.assertIn("ще не налаштований", rendered)

    def test_dangerous_blocked(self) -> None:
        result = self._result(
            action="delete_all_files",
            status="blocked_dangerous",
            reason_code="dangerous_action",
            message="blocked",
            is_safety_block=True,
        )

        self.assertEqual(render_final_response("", result), "Ні, сер. Це небезпечна дія, я її не виконуватиму.")

    def test_generic_volume_media_app_executed(self) -> None:
        self.assertEqual(
            render_final_response("", self._result("volume_up", "executed", None, "done", executed=True)),
            "Гучність збільшено, сер.",
        )
        self.assertEqual(
            render_final_response("", self._result("music_next", "executed", None, "done", executed=True)),
            "Перемкнув на наступний трек, сер.",
        )
        self.assertEqual(
            render_final_response(
                "",
                self._result("open_app", "executed", None, "done", executed=True, normalized_target="spotify"),
            ),
            "Запустив spotify, сер.",
        )

    def _result(
        self,
        action: str,
        status: str,
        reason_code: str | None,
        message: str,
        executed: bool = False,
        details: str | None = None,
        is_safety_block: bool = False,
        normalized_target: str | None = None,
    ) -> CommandResult:
        return CommandResult(
            executed=executed,
            action=action,
            status=status,
            message=message,
            details=details,
            reason_code=reason_code,
            is_safety_block=is_safety_block,
            normalized_action=action,
            normalized_target=normalized_target,
        )


if __name__ == "__main__":
    unittest.main()
