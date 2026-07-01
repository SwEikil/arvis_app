from __future__ import annotations

import json
import unittest
from pathlib import Path

from intent_resolver import IntentResolver
from intent_resolver import ResolvedIntent
from intent_resolver import should_pass_to_router
from voice_text_normalizer import correct_voice_text


class FakeLlmClient:
    def __init__(self, raw_response: str, error: str | None = None) -> None:
        self.raw_response = raw_response
        self.error = error

    def chat(self, messages: list[dict[str, str]]) -> tuple[str, str | None]:
        return self.raw_response, self.error


class IntentResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = IntentResolver()

    def test_no_action_intent_restore_sound(self) -> None:
        resolved = self.resolver.resolve("Арвіс, поверни звук", use_llm=False)

        self.assertEqual(resolved.action, "volume_unmute")
        self.assertTrue(should_pass_to_router(resolved))

    def test_casual_volume_up(self) -> None:
        resolved = self.resolver.resolve("Арвіс, це слабовато якось. Додай ще гучності", use_llm=False)

        self.assertEqual(resolved.action, "volume_up")
        self.assertTrue(should_pass_to_router(resolved))

    def test_volume_up_default_step(self) -> None:
        resolved = self.resolver.resolve("Арвіс, зроби гучніше", use_llm=False)

        self.assertEqual(resolved.action, "volume_up")
        self.assertEqual(resolved.params["step_percent"], 5)

    def test_volume_up_custom_step(self) -> None:
        resolved = self.resolver.resolve("Арвіс, зроби гучніше на 30", use_llm=False)

        self.assertEqual(resolved.action, "volume_up")
        self.assertEqual(resolved.params["step_percent"], 30)

    def test_volume_up_percent_step(self) -> None:
        resolved = self.resolver.resolve("Арвіс, додай 20% гучності", use_llm=False)

        self.assertEqual(resolved.action, "volume_up")
        self.assertEqual(resolved.params["step_percent"], 20)

    def test_volume_down_custom_step(self) -> None:
        resolved = self.resolver.resolve("Арвіс, зроби тихіше на 15", use_llm=False)

        self.assertEqual(resolved.action, "volume_down")
        self.assertEqual(resolved.params["step_percent"], 15)

    def test_volume_step_clamp(self) -> None:
        resolved = self.resolver.resolve("Арвіс, зроби гучніше на 500", use_llm=False)

        self.assertEqual(resolved.action, "volume_up")
        self.assertEqual(resolved.params["step_percent"], 50)

    def test_context_repeat_volume_up(self) -> None:
        resolved = self.resolver.resolve(
            "ще",
            command_history=[
                {
                    "normalized_action": "volume_up",
                    "normalized_target": "system",
                    "executed": True,
                }
            ],
            use_llm=False,
        )

        self.assertEqual(resolved.action, "volume_up")
        self.assertEqual(resolved.source, "context_repair")
        self.assertTrue(should_pass_to_router(resolved))

    def test_context_reverse_volume_mute(self) -> None:
        resolved = self.resolver.resolve(
            "поверни назад",
            command_history=[
                {
                    "normalized_action": "volume_mute",
                    "normalized_target": "system",
                    "executed": True,
                }
            ],
            use_llm=False,
        )

        self.assertEqual(resolved.action, "volume_unmute")
        self.assertEqual(resolved.source, "context_repair")
        self.assertTrue(should_pass_to_router(resolved))

    def test_app_launch_spotify(self) -> None:
        resolved = self.resolver.resolve("Вруби споті", use_llm=False)

        self.assertEqual(resolved.action, "open_app")
        self.assertEqual(resolved.target, "spotify")
        self.assertTrue(should_pass_to_router(resolved))

    def test_open_youtube_website_phrases(self) -> None:
        for phrase in ["Відкри, Ютуб!", "відкрий ютуб", "открой ютуб", "open youtube"]:
            with self.subTest(phrase=phrase):
                resolved = self.resolver.resolve(phrase, use_llm=False)

                self.assertEqual(resolved.action, "open_app")
                self.assertEqual(resolved.target, "youtube")
                self.assertEqual(resolved.risk, "safe")
                self.assertTrue(should_pass_to_router(resolved))

    def test_open_whitelist_website_phrases(self) -> None:
        cases = {
            "відкрий google": "google",
            "відкрий github": "github",
            "відкрий chatgpt": "chatgpt",
        }

        for phrase, target in cases.items():
            with self.subTest(phrase=phrase):
                resolved = self.resolver.resolve(phrase, use_llm=False)

                self.assertEqual(resolved.action, "open_app")
                self.assertEqual(resolved.target, target)
                self.assertEqual(resolved.risk, "safe")
                self.assertTrue(should_pass_to_router(resolved))

    def test_browser_task_humanbenchmark_aim_phrases(self) -> None:
        for phrase in [
            "відкрий тренування аіма",
            "open aim trainer",
            "відкрий тренування аіму і порази 30 цілей",
            "відкрий тренування aimу і порази 30 цілей",
            "відкрий тренеровку аима",
            "запусти aim trainer",
            "пройди aim test",
        ]:
            with self.subTest(phrase=phrase):
                resolved = self.resolver.resolve(phrase, use_llm=False)

                self.assertEqual(resolved.action, "browser_task_run")
                self.assertEqual(resolved.target, "humanbenchmark_aim")
                self.assertEqual(resolved.risk, "safe")
                self.assertTrue(should_pass_to_router(resolved))

    def test_minecraft_server(self) -> None:
        resolved = self.resolver.resolve("Підніми майн сервер", use_llm=False)

        self.assertEqual(resolved.action, "minecraft_server_start")
        self.assertEqual(resolved.target, "default")
        self.assertTrue(should_pass_to_router(resolved))

    def test_server_stop_phrases_prioritize_minecraft(self) -> None:
        phrases = ["зупини сервер", "зупини майн сервер", "вимкни сервер", "стопни сервер"]

        for phrase in phrases:
            with self.subTest(phrase=phrase):
                resolved = self.resolver.resolve(phrase, use_llm=False)

                self.assertEqual(resolved.action, "minecraft_server_stop")
                self.assertEqual(resolved.target, "default")
                self.assertNotEqual(resolved.action, "music_pause")
                self.assertTrue(should_pass_to_router(resolved))

    def test_media_pause_natural_phrase(self) -> None:
        resolved = self.resolver.resolve("Постав це на паузу", use_llm=False)

        self.assertEqual(resolved.action, "music_pause")
        self.assertTrue(should_pass_to_router(resolved))

    def test_media_pause_still_handles_music_and_video(self) -> None:
        for phrase in ["зупини музику", "зупини відео", "постав на паузу"]:
            with self.subTest(phrase=phrase):
                resolved = self.resolver.resolve(phrase, use_llm=False)

                self.assertEqual(resolved.action, "music_pause")
                self.assertTrue(should_pass_to_router(resolved))

    def test_media_next_natural_phrase(self) -> None:
        resolved = self.resolver.resolve("надави наступну", use_llm=False)

        self.assertEqual(resolved.action, "music_next")
        self.assertTrue(should_pass_to_router(resolved))

    def test_media_next_skip_phrase(self) -> None:
        resolved = self.resolver.resolve("скипни", use_llm=False)

        self.assertEqual(resolved.action, "music_next")
        self.assertTrue(should_pass_to_router(resolved))

    def test_media_previous_natural_phrase(self) -> None:
        resolved = self.resolver.resolve("попередню", use_llm=False)

        self.assertEqual(resolved.action, "music_previous")
        self.assertTrue(should_pass_to_router(resolved))

    def test_media_previous_restore_previous_song_phrase(self) -> None:
        resolved = self.resolver.resolve("поверни минулу пісню", use_llm=False)

        self.assertEqual(resolved.action, "music_previous")
        self.assertEqual(resolved.target, "media")
        self.assertGreaterEqual(resolved.confidence, 0.85)
        self.assertTrue(should_pass_to_router(resolved))

    def test_media_previous_play_previous_song_phrase(self) -> None:
        resolved = self.resolver.resolve("включи минулу пісню", use_llm=False)

        self.assertEqual(resolved.action, "music_previous")
        self.assertTrue(should_pass_to_router(resolved))

    def test_seek_forward_default_seconds(self) -> None:
        resolved = self.resolver.resolve("Арвіс, перемотай вперед", use_llm=False)

        self.assertEqual(resolved.action, "media_seek_forward")
        self.assertEqual(resolved.params["seconds"], 5)

    def test_seek_forward_custom_seconds(self) -> None:
        resolved = self.resolver.resolve("Арвіс, перемотай вперед на 30 секунд", use_llm=False)

        self.assertEqual(resolved.action, "media_seek_forward")
        self.assertEqual(resolved.params["seconds"], 30)

    def test_seek_backward_default_seconds(self) -> None:
        resolved = self.resolver.resolve("Арвіс, відмотай назад", use_llm=False)

        self.assertEqual(resolved.action, "media_seek_backward")
        self.assertEqual(resolved.params["seconds"], 5)

    def test_seek_backward_custom_seconds_real_phrase(self) -> None:
        resolved = self.resolver.resolve("Арвіс, відмотай назад на 10 секунд", use_llm=False)

        self.assertEqual(resolved.action, "media_seek_backward")
        self.assertEqual(resolved.params["seconds"], 10)

    def test_seek_backward_custom_seconds(self) -> None:
        resolved = self.resolver.resolve("Арвіс, назад на 15 секунд", use_llm=False)

        self.assertEqual(resolved.action, "media_seek_backward")
        self.assertEqual(resolved.params["seconds"], 15)

    def test_seek_seconds_clamp(self) -> None:
        resolved = self.resolver.resolve("Арвіс, перемотай вперед на 999 секунд", use_llm=False)

        self.assertEqual(resolved.action, "media_seek_forward")
        self.assertEqual(resolved.params["seconds"], 300)

    def test_repeat_track(self) -> None:
        resolved = self.resolver.resolve("постав пісню на повтор", use_llm=False)

        self.assertEqual(resolved.action, "music_repeat_track")
        self.assertTrue(should_pass_to_router(resolved))

    def test_repeat_playlist(self) -> None:
        resolved = self.resolver.resolve("постав плейлист на повтор", use_llm=False)

        self.assertEqual(resolved.action, "music_repeat_playlist")
        self.assertTrue(should_pass_to_router(resolved))

    def test_repeat_off(self) -> None:
        resolved = self.resolver.resolve("вимкни повтор", use_llm=False)

        self.assertEqual(resolved.action, "music_repeat_off")
        self.assertTrue(should_pass_to_router(resolved))

    def test_shuffle_on(self) -> None:
        resolved = self.resolver.resolve("увімкни shuffle", use_llm=False)

        self.assertEqual(resolved.action, "music_shuffle_on")
        self.assertTrue(should_pass_to_router(resolved))

    def test_shuffle_off(self) -> None:
        resolved = self.resolver.resolve("вимкни перемішування", use_llm=False)

        self.assertEqual(resolved.action, "music_shuffle_off")
        self.assertTrue(should_pass_to_router(resolved))

    def test_shuffle_toggle(self) -> None:
        resolved = self.resolver.resolve("перемкни shuffle", use_llm=False)

        self.assertEqual(resolved.action, "music_shuffle_toggle")
        self.assertTrue(should_pass_to_router(resolved))

    def test_like_current_song(self) -> None:
        resolved = self.resolver.resolve("додай цю пісню до вподобаного", use_llm=False)

        self.assertEqual(resolved.action, "music_like_current")
        self.assertTrue(should_pass_to_router(resolved))

    def test_like_current_song_natural_preference_phrase(self) -> None:
        resolved = self.resolver.resolve("мені подобається ця пісня", use_llm=False)

        self.assertEqual(resolved.action, "music_like_current")
        self.assertTrue(should_pass_to_router(resolved))

    def test_like_current_song_short_add_phrase(self) -> None:
        resolved = self.resolver.resolve("додай до вподобаного", use_llm=False)

        self.assertEqual(resolved.action, "music_like_current")
        self.assertNotEqual(resolved.action, "volume_up")
        self.assertTrue(should_pass_to_router(resolved))

    def test_like_current_song_short_add_phrase_not_volume_up(self) -> None:
        resolved = self.resolver.resolve("додай цю пісню до вподобаного", use_llm=False)

        self.assertEqual(resolved.action, "music_like_current")
        self.assertNotEqual(resolved.action, "volume_up")

    def test_volume_up_requires_volume_context_for_add(self) -> None:
        resolved = self.resolver.resolve("додай гучності", use_llm=False)

        self.assertEqual(resolved.action, "volume_up")
        self.assertTrue(should_pass_to_router(resolved))

    def test_media_play_unpause_phrase(self) -> None:
        resolved = self.resolver.resolve("зніми з паузи", use_llm=False)

        self.assertEqual(resolved.action, "music_play")
        self.assertTrue(should_pass_to_router(resolved))

    def test_context_media_pause_then_make_normal(self) -> None:
        resolved = self.resolver.resolve(
            "зроби нормально",
            command_history=[
                {
                    "normalized_action": "music_pause",
                    "normalized_target": "media",
                    "executed": True,
                }
            ],
            use_llm=False,
        )

        self.assertEqual(resolved.action, "music_play")
        self.assertEqual(resolved.source, "context_repair")
        self.assertTrue(should_pass_to_router(resolved))

    def test_context_media_pause_then_restore_as_it_was(self) -> None:
        resolved = self.resolver.resolve(
            "поверни як було",
            command_history=[
                {
                    "normalized_action": "music_pause",
                    "normalized_target": "media",
                    "executed": True,
                }
            ],
            use_llm=False,
        )

        self.assertEqual(resolved.action, "music_play")
        self.assertEqual(resolved.source, "context_repair")
        self.assertTrue(should_pass_to_router(resolved))

    def test_context_repeat_music_next(self) -> None:
        resolved = self.resolver.resolve(
            "ще",
            command_history=[
                {
                    "normalized_action": "music_next",
                    "normalized_target": "media",
                    "executed": True,
                }
            ],
            use_llm=False,
        )

        self.assertEqual(resolved.action, "music_next")
        self.assertEqual(resolved.source, "context_repair")
        self.assertTrue(should_pass_to_router(resolved))

    def test_context_repeat_music_previous(self) -> None:
        resolved = self.resolver.resolve(
            "ще",
            command_history=[
                {
                    "normalized_action": "music_previous",
                    "normalized_target": "media",
                    "executed": True,
                }
            ],
            use_llm=False,
        )

        self.assertEqual(resolved.action, "music_previous")
        self.assertEqual(resolved.source, "context_repair")
        self.assertTrue(should_pass_to_router(resolved))

    def test_music_next_high_confidence_passes_router_gate(self) -> None:
        resolved = ResolvedIntent(
            action="music_next",
            target="media",
            risk="safe",
            need_confirmation=False,
            confidence=0.95,
            source="llm_resolver",
            reason="test",
        )

        self.assertTrue(should_pass_to_router(resolved))

    def test_dangerous_is_not_safe_action(self) -> None:
        resolved = self.resolver.resolve("Видали всі файли з Завантажень", use_llm=False)

        self.assertIsNone(resolved.action)
        self.assertEqual(resolved.risk, "dangerous")
        self.assertFalse(should_pass_to_router(resolved))

    def test_ambiguous_is_low_confidence(self) -> None:
        resolved = self.resolver.resolve("зроби нормально", use_llm=False)

        self.assertIsNone(resolved.action)
        self.assertLess(resolved.confidence, 0.65)
        self.assertFalse(should_pass_to_router(resolved))

    def test_like_current_song_imperfect_phrase(self) -> None:
        resolved = self.resolver.resolve("мені сподобалася ця пісня, додай її", use_llm=False)

        self.assertEqual(resolved.action, "music_like_current")
        self.assertTrue(should_pass_to_router(resolved))

    def test_negative_song_phrase_skips_next(self) -> None:
        resolved = self.resolver.resolve("мені не подобається ця пісня, давай некст", use_llm=False)

        self.assertEqual(resolved.action, "music_next")
        self.assertTrue(should_pass_to_router(resolved))

    def test_media_status_casual_phrase(self) -> None:
        resolved = self.resolver.resolve("шо зараз грає?", use_llm=False)

        self.assertEqual(resolved.action, "media_status")
        self.assertTrue(should_pass_to_router(resolved))

    def test_volume_status_phrase(self) -> None:
        resolved = self.resolver.resolve("яка гучність?", use_llm=False)

        self.assertEqual(resolved.action, "volume_status")
        self.assertTrue(should_pass_to_router(resolved))

    def test_volume_set_phrase(self) -> None:
        resolved = self.resolver.resolve("постав звук на 30", use_llm=False)

        self.assertEqual(resolved.action, "volume_set")
        self.assertEqual(resolved.params["level_percent"], 30)
        self.assertTrue(should_pass_to_router(resolved))

    def test_volume_set_clamps_high_value(self) -> None:
        resolved = self.resolver.resolve("зроби гучність 999", use_llm=False)

        self.assertEqual(resolved.action, "volume_set")
        self.assertEqual(resolved.params["level_percent"], 100)

    def test_volume_set_english_short_phrase(self) -> None:
        resolved = self.resolver.resolve("volume 40", use_llm=False)

        self.assertEqual(resolved.action, "volume_set")
        self.assertEqual(resolved.params["level_percent"], 40)

    def test_bad_track_phrase_skips_next(self) -> None:
        resolved = self.resolver.resolve("фігня трек, скипни", use_llm=False)

        self.assertEqual(resolved.action, "music_next")

    def test_indirect_volume_up_phrase(self) -> None:
        resolved = self.resolver.resolve("занадто тихо", use_llm=False)

        self.assertEqual(resolved.action, "volume_up")

    def test_indirect_volume_down_phrase(self) -> None:
        resolved = self.resolver.resolve("вуха ріже", use_llm=False)

        self.assertEqual(resolved.action, "volume_down")

    def test_typo_spotify_app_launch(self) -> None:
        resolved = self.resolver.resolve("вруби спотии", use_llm=False)

        self.assertEqual(resolved.action, "open_app")
        self.assertEqual(resolved.target, "spotify")
        self.assertGreaterEqual(resolved.confidence, 0.65)

    def test_dangerous_mixed_phrase_stays_blocked(self) -> None:
        resolved = self.resolver.resolve("видали файли і зроби гучніше", use_llm=False)

        self.assertIsNone(resolved.action)
        self.assertEqual(resolved.risk, "dangerous")

    def test_dangerous_mixed_open_website_stays_blocked(self) -> None:
        resolved = self.resolver.resolve("видали файли і відкрий ютуб", use_llm=False)

        self.assertIsNone(resolved.action)
        self.assertEqual(resolved.risk, "dangerous")
        self.assertFalse(should_pass_to_router(resolved))

    def test_dangerous_mixed_browser_task_stays_blocked(self) -> None:
        resolved = self.resolver.resolve("видали файли і відкрий aim trainer", use_llm=False)

        self.assertIsNone(resolved.action)
        self.assertEqual(resolved.risk, "dangerous")
        self.assertFalse(should_pass_to_router(resolved))

    def test_voice_transcript_regression_fixture(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "voice_transcripts_uk.jsonl"
        rows = [json.loads(line) for line in fixture_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        for row in rows:
            with self.subTest(text=row["text"]):
                resolved = self.resolver.resolve(row["text"], use_llm=False)
                if row["dangerous"]:
                    self.assertTrue(resolved.risk == "dangerous" or not should_pass_to_router(resolved))
                    self.assertIsNone(resolved.action)
                    continue

                self.assertEqual(resolved.action, row["expected_action"])
                self.assertEqual(resolved.target, row["expected_target"])
                for key, value in row["expected_params"].items():
                    self.assertEqual(resolved.params.get(key), value)
                self.assertTrue(should_pass_to_router(resolved))

    def test_voice_correction_metadata(self) -> None:
        correction = correct_voice_text("Арвіс, зробити хіше")

        self.assertTrue(correction.changed)
        self.assertEqual(correction.corrected_text, "Арвіс, зроби тихіше")
        self.assertEqual(correction.reason, "voice correction: хіше -> тихіше")

    def test_voice_correction_open_youtube(self) -> None:
        resolved = self.resolver.resolve("Відкри, Ютуб!", use_llm=False)

        self.assertEqual(resolved.action, "open_app")
        self.assertEqual(resolved.target, "youtube")
        self.assertEqual(resolved.risk, "safe")
        self.assertEqual(resolved.source, "voice_correction")
        self.assertEqual(resolved.corrected_text, "відкрий, Ютуб!")
        self.assertTrue(should_pass_to_router(resolved))

    def test_voice_correction_arvis_make_hishe_volume_down(self) -> None:
        resolved = self.resolver.resolve("Арвіс, зробити хіше", use_llm=False)

        self.assertEqual(resolved.action, "volume_down")
        self.assertEqual(resolved.target, "system")
        self.assertEqual(resolved.risk, "safe")
        self.assertFalse(resolved.need_confirmation)
        self.assertGreaterEqual(resolved.confidence, 0.85)
        self.assertEqual(resolved.source, "voice_correction")
        self.assertEqual(resolved.original_text, "Арвіс, зробити хіше")
        self.assertEqual(resolved.corrected_text, "Арвіс, зроби тихіше")
        self.assertTrue(should_pass_to_router(resolved))

    def test_voice_correction_make_hishe_volume_down(self) -> None:
        resolved = self.resolver.resolve("зробити хіше", use_llm=False)

        self.assertEqual(resolved.action, "volume_down")
        self.assertTrue(should_pass_to_router(resolved))

    def test_voice_correction_do_hishe_volume_down(self) -> None:
        resolved = self.resolver.resolve("зроби хіше", use_llm=False)

        self.assertEqual(resolved.action, "volume_down")
        self.assertTrue(should_pass_to_router(resolved))

    def test_voice_correction_do_heishe_volume_down(self) -> None:
        resolved = self.resolver.resolve("зроби хейше", use_llm=False)

        self.assertEqual(resolved.action, "volume_down")
        self.assertTrue(should_pass_to_router(resolved))

    def test_voice_correction_make_heishe_volume_down(self) -> None:
        resolved = self.resolver.resolve("зробити хейше", use_llm=False)

        self.assertEqual(resolved.action, "volume_down")
        self.assertTrue(should_pass_to_router(resolved))

    def test_voice_correction_do_tishe_volume_down(self) -> None:
        resolved = self.resolver.resolve("зроби тише", use_llm=False)

        self.assertEqual(resolved.action, "volume_down")
        self.assertTrue(should_pass_to_router(resolved))

    def test_voice_correction_dangerous_phrase_stays_blocked(self) -> None:
        resolved = self.resolver.resolve("видали файли і зроби хіше", use_llm=False)

        self.assertIsNone(resolved.action)
        self.assertEqual(resolved.risk, "dangerous")
        self.assertFalse(should_pass_to_router(resolved))

    def test_voice_correction_phonetic_dangerous_phrase_stays_blocked(self) -> None:
        resolved = self.resolver.resolve("Видели файли і зробі тихіші", use_llm=False)

        self.assertIsNone(resolved.action)
        self.assertEqual(resolved.risk, "dangerous")
        self.assertFalse(should_pass_to_router(resolved))

    def test_voice_correction_phonetic_dangerous_volume_up_stays_blocked(self) -> None:
        resolved = self.resolver.resolve("видели файли і зроби гучніше", use_llm=False)

        self.assertIsNone(resolved.action)
        self.assertEqual(resolved.risk, "dangerous")
        self.assertNotEqual(resolved.action, "volume_up")
        self.assertFalse(should_pass_to_router(resolved))

    def test_voice_correction_random_hishe_without_command_does_not_route(self) -> None:
        resolved = self.resolver.resolve("десь там хіше прозвучало", use_llm=False)

        self.assertIsNone(resolved.action)
        self.assertFalse(should_pass_to_router(resolved))

    def test_llm_low_risk_safe_action_is_normalized(self) -> None:
        resolver = IntentResolver(
            FakeLlmClient(
                '{"action":"volume_down","target":"system","risk":"low","need_confirmation":false,"confidence":0.9}'
            )
        )

        resolved = resolver.resolve("Арвіс, зроби звук нижче", use_llm=True)

        self.assertEqual(resolved.action, "volume_down")
        self.assertEqual(resolved.risk, "safe")
        self.assertTrue(should_pass_to_router(resolved))


if __name__ == "__main__":
    unittest.main()
