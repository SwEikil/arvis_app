from __future__ import annotations

import unittest
from unittest.mock import patch

from command_router import CommandRouter
from intent_resolver import IntentResolver
from intent_resolver import should_pass_to_router
from main import should_try_resolver_for_result
from schemas import ActionIntent


class CommandRouterVolumeNormalizationTests(unittest.TestCase):
    def test_volume_up_dry_run_uses_step_percent(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(
                action="volume_up",
                target="system",
                risk="safe",
                need_confirmation=False,
                params={"step_percent": 30},
            ),
            user_text="Арвіс, зроби гучніше на 30",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.params, {"step_percent": 30})
        self.assertIn("30%+", result.details or "")

    def test_volume_down_dry_run_uses_step_percent(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(
                action="volume_down",
                target="system",
                risk="safe",
                need_confirmation=False,
                params={"step_percent": 15},
            ),
            user_text="Арвіс, зроби тихіше на 15",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.params, {"step_percent": 15})
        self.assertIn("15%-", result.details or "")

    def test_seek_forward_dry_run_uses_seconds(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(
                action="media_seek_forward",
                target="media",
                risk="safe",
                need_confirmation=False,
                params={"seconds": 30},
            ),
            user_text="Арвіс, перемотай вперед на 30 секунд",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.params, {"seconds": 30})
        self.assertIn("position 30+", result.details or "")

    def test_seek_backward_dry_run_uses_seconds(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(
                action="media_seek_backward",
                target="media",
                risk="safe",
                need_confirmation=False,
                params={"seconds": 15},
            ),
            user_text="Арвіс, назад на 15 секунд",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.params, {"seconds": 15})
        self.assertIn("position 15-", result.details or "")

    def test_seek_media_backward_alias_uses_default_seconds(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(
                action="seek_media",
                target="backward",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, відмотай назад",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.normalized_action, "media_seek_backward")
        self.assertEqual(result.params, {"seconds": 5})
        self.assertIn("position 5-", result.details or "")

    def test_seek_media_forward_alias_uses_default_seconds(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(
                action="seek_media",
                target="forward",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, перемотай вперед",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.normalized_action, "media_seek_forward")
        self.assertEqual(result.params, {"seconds": 5})
        self.assertIn("position 5+", result.details or "")

    def test_repeat_and_shuffle_dry_run_commands(self) -> None:
        repeat_result = CommandRouter(dry_run=True).route(
            ActionIntent(action="music_repeat_track", target="media", risk="safe", need_confirmation=False),
            user_text="постав пісню на повтор",
        )
        shuffle_result = CommandRouter(dry_run=True).route(
            ActionIntent(action="music_shuffle_toggle", target="media", risk="safe", need_confirmation=False),
            user_text="перемкни shuffle",
        )

        self.assertIn("loop Track", repeat_result.details or "")
        self.assertIn("shuffle Toggle", shuffle_result.details or "")

    def test_like_current_song_is_unsupported(self) -> None:
        result = CommandRouter(dry_run=False).route(
            ActionIntent(action="music_like_current", target="media", risk="safe", need_confirmation=False),
            user_text="додай цю пісню до вподобаного",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.reason_code, "spotify_api_required")
        self.assertFalse(result.is_safety_block)
        self.assertIn("Spotify API", result.message)

    def test_like_current_song_short_phrase_is_unsupported_not_dangerous(self) -> None:
        result = CommandRouter(dry_run=False).route(
            ActionIntent(action="music_like_current", target="media", risk="safe", need_confirmation=False),
            user_text="додай до вподобаного",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.reason_code, "spotify_api_required")
        self.assertFalse(result.is_safety_block)
        self.assertEqual(result.normalized_action, "music_like_current")
        self.assertIn("Spotify API", result.message)
        self.assertNotIn("risk=", result.details or "")

    def test_like_current_song_preference_phrase_is_unsupported(self) -> None:
        result = CommandRouter(dry_run=False).route(
            ActionIntent(action="music_like_current", target="media", risk="safe", need_confirmation=False),
            user_text="мені подобається ця пісня",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.reason_code, "spotify_api_required")
        self.assertFalse(result.is_safety_block)
        self.assertIn("Spotify API", result.message)

    def test_play_next_track_alias_routes_to_music_next(self) -> None:
        result, calls = self._route_media(
            ActionIntent(
                action="play_next_track",
                target="spotify",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="надави наступну",
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.normalized_action, "music_next")
        self.assertEqual(result.normalized_target, "spotify")
        self.assertEqual(calls, ["music_next"])

    def test_next_track_alias_routes_to_music_next(self) -> None:
        result, calls = self._route_media(
            ActionIntent(
                action="next_track",
                target="spotify",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="next track",
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.normalized_action, "music_next")
        self.assertEqual(result.normalized_target, "spotify")
        self.assertEqual(calls, ["music_next"])

    def test_set_volume_louder_uses_natural_user_text_for_volume_up(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="set_volume",
                target="louder",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, це слабовато якось. Додай ще гучності",
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.normalized_action, "volume_up")
        self.assertEqual(calls, ["volume_up"])
        self.assertIn("volume direction/action detected from user_text", result.details or "")

    def test_adjust_volume_music_uses_user_text_for_volume_down(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="adjust_volume",
                target="music",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, зроби тихіше",
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.normalized_action, "volume_down")
        self.assertEqual(calls, ["volume_down"])
        self.assertIn("volume direction/action detected from user_text", result.details or "")
        self.assertIn("default audio sink", result.details or "")

    def test_adjust_volume_music_uses_user_text_for_volume_up(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="adjust_volume",
                target="music",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, зроби голосніше",
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.normalized_action, "volume_up")
        self.assertEqual(calls, ["volume_up"])

    def test_adjust_volume_music_uses_user_text_for_mute(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="adjust_volume",
                target="music",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, вимкни звук",
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.normalized_action, "volume_mute")
        self.assertEqual(calls, ["volume_mute"])

    def test_restore_audio_uses_user_text_for_volume_unmute(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="restore_audio",
                target="system_sound",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, поверни звук",
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.normalized_action, "volume_unmute")
        self.assertEqual(calls, ["volume_unmute"])
        self.assertIn("volume direction/action detected from user_text", result.details or "")

    def test_set_volume_mute_uses_explicit_volume_mute(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="set_volume",
                target="mute",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, вимкни звук",
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.normalized_action, "volume_mute")
        self.assertEqual(calls, ["volume_mute"])

    def test_unknown_safe_action_uses_natural_user_text_for_volume_up(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="make_it_better",
                target="music",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, додай ще гучності",
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.normalized_action, "volume_up")
        self.assertEqual(calls, ["volume_up"])

    def test_browser_volume_falls_back_to_default_sink(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="adjust_volume",
                target="browser",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, зроби браузер тихіше",
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.normalized_action, "volume_down")
        self.assertEqual(result.normalized_target, "brave")
        self.assertEqual(calls, ["volume_down"])
        self.assertIn("per-app volume is not supported", result.details or "")
        self.assertIn("default audio sink", result.details or "")

    def test_volume_scope_without_direction_is_not_guessed(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="adjust_volume",
                target="music",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, налаштуй музику",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.reason_code, "volume_direction_unknown")
        self.assertEqual(result.normalized_action, "adjust_volume")
        self.assertEqual(calls, [])
        self.assertIn("Volume direction is not clear", result.message)

    def test_dangerous_action_is_still_blocked(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="delete_all_files",
                target="Downloads",
                risk="dangerous",
                need_confirmation=True,
            ),
            user_text="Арвіс, видали всі файли і додай ще гучності",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "blocked_dangerous")
        self.assertTrue(result.is_safety_block)
        self.assertEqual(calls, [])
        self.assertIn("risk=dangerous", result.details or "")

    def test_unknown_action_is_still_blocked(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="paint_the_wall",
                target="blue",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Арвіс, пофарбуй стіну",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "unknown_action")
        self.assertEqual(result.reason_code, "action_not_whitelisted")
        self.assertEqual(calls, [])
        self.assertIn("unknown", result.message.lower())

    def test_need_confirmation_is_blocked_with_specific_status(self) -> None:
        result, calls = self._route_volume(
            ActionIntent(
                action="volume_up",
                target="system",
                risk="safe",
                need_confirmation=True,
            ),
            user_text="Арвіс, додай гучності",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "blocked_confirmation_required")
        self.assertEqual(result.reason_code, "confirmation_required")
        self.assertTrue(result.is_safety_block)
        self.assertEqual(calls, [])
        self.assertIn("confirmation", result.message.lower())

    def test_minecraft_server_without_config_is_not_configured(self) -> None:
        result = CommandRouter(dry_run=False).route(
            ActionIntent(
                action="start_minecraft_server",
                target="minecraft_server",
                risk="safe",
                need_confirmation=False,
            ),
            user_text="Підніми майн сервер",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.reason_code, "minecraft_server_not_configured")
        self.assertFalse(result.is_safety_block)
        self.assertIn("not configured", result.message.lower())

    def test_model_risk_dangerous_safe_volume_text_can_be_repaired(self) -> None:
        router = CommandRouter(dry_run=False)
        initial = router.route(
            ActionIntent(
                action="volume_up",
                target="system",
                risk="dangerous",
                need_confirmation=False,
            ),
            user_text="Арвіс, додай гучності",
        )

        self.assertEqual(initial.status, "blocked_dangerous")
        self.assertTrue(should_try_resolver_for_result(initial, "Арвіс, додай гучності"))

        resolved = IntentResolver().resolve("Арвіс, додай гучності", use_llm=False)
        self.assertEqual(resolved.action, "volume_up")
        self.assertTrue(should_pass_to_router(resolved))

        calls: list[str] = []

        def fake_execute(action: str, params: dict[str, object] | None = None) -> tuple[bool, str, str | None]:
            calls.append(action)
            return True, f"fake {action}", "fake wpctl"

        with patch("command_router.execute_volume_action", fake_execute):
            repaired = router.route(resolved.to_action_intent(), user_text="Арвіс, додай гучності")  # type: ignore[arg-type]

        self.assertTrue(repaired.executed)
        self.assertEqual(repaired.status, "executed")
        self.assertEqual(calls, ["volume_up"])

    def test_model_risk_dangerous_delete_text_is_not_repaired(self) -> None:
        result = CommandRouter(dry_run=False).route(
            ActionIntent(
                action="volume_up",
                target="system",
                risk="dangerous",
                need_confirmation=False,
            ),
            user_text="видали всі файли",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "blocked_dangerous")
        self.assertTrue(result.is_safety_block)
        self.assertFalse(should_try_resolver_for_result(result, "видали всі файли"))

    def _route_volume(
        self,
        intent: ActionIntent,
        user_text: str,
    ) -> tuple[object, list[str]]:
        calls: list[str] = []

        def fake_execute(action: str, params: dict[str, object] | None = None) -> tuple[bool, str, str | None]:
            calls.append(action)
            return True, f"fake {action}", "fake wpctl"

        with patch("command_router.execute_volume_action", fake_execute):
            result = CommandRouter(dry_run=False).route(intent, user_text=user_text)

        return result, calls

    def _route_media(
        self,
        intent: ActionIntent,
        user_text: str,
    ) -> tuple[object, list[str]]:
        calls: list[str] = []

        def fake_execute(action: str, params: dict[str, object] | None = None) -> tuple[bool, str, str | None]:
            calls.append(action)
            return True, f"fake {action}", "fake playerctl"

        with patch("command_router.execute_media_action", fake_execute):
            result = CommandRouter(dry_run=False).route(intent, user_text=user_text)

        return result, calls


if __name__ == "__main__":
    unittest.main()
