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
        result = self._result(
            action="volume_up",
            status="dry_run",
            reason_code="volume_dry_run",
            message="dry-run",
            params={"step_percent": 5},
        )

        rendered = render_final_response("", result)

        self.assertIn("Dry-run", rendered)
        self.assertIn("збільшив гучність на 5%", rendered)
        self.assertIn("реальна команда не виконувалась", rendered)

    def test_browser_task_dry_run_response(self) -> None:
        result = self._result(
            action="browser_task_run",
            status="dry_run",
            reason_code=None,
            message="dry-run",
            normalized_target="humanbenchmark_aim",
        )

        self.assertEqual(
            render_final_response("", result),
            "Dry-run, сер: я б запустив browser task HumanBenchmark Aim, але реальна дія не виконувалась.",
        )

    def test_browser_watch_poll_dry_run_response_does_not_spam_notice(self) -> None:
        result = self._result(
            action="browser_watch_poll_once",
            status="dry_run",
            reason_code=None,
            message="dry-run",
            normalized_target="viewport_change_full",
        )

        rendered = render_final_response("", result)

        self.assertIn("Dry-run", rendered)
        self.assertIn("viewport_change_full", rendered)
        self.assertNotIn("Generic кліки", rendered)

    def test_browser_watch_event_found_response(self) -> None:
        result = self._result(
            action="browser_watch_poll_once",
            status="executed",
            reason_code=None,
            message="Browser observer event found.",
            executed=True,
            details="event_type: text_appeared\nmessage: Text appeared: Example Domain",
        )

        rendered = render_final_response("", result)

        self.assertIn("Знайшов подію спостереження", rendered)
        self.assertIn("text_appeared", rendered)
        self.assertNotIn("Generic кліки", rendered)

    def test_browser_watch_started_response(self) -> None:
        result = self._result(
            action="browser_watch_start",
            status="executed",
            reason_code=None,
            message="Browser watch started.",
            executed=True,
            details="watch_id=text_appeared\nprofile=text_appeared",
            normalized_target="text_appeared",
        )

        rendered = render_final_response("", result)

        self.assertIn("Запустив фонове спостереження", rendered)
        self.assertIn("text_appeared", rendered)
        self.assertIn("Режим спостереження: без кліків і натискань.", rendered)

    def test_browser_watch_already_running_response(self) -> None:
        result = self._result(
            action="browser_watch_start",
            status="already_running",
            reason_code="browser_watch_already_running",
            message="Browser watch already running.",
            details="watch_id=text_appeared",
            normalized_target="text_appeared",
        )

        rendered = render_final_response("", result)

        self.assertIn("вже працює", rendered)
        self.assertIn("text_appeared", rendered)

    def test_browser_watch_stopped_response(self) -> None:
        result = self._result(
            action="browser_watch_stop",
            status="executed",
            reason_code=None,
            message="Browser watch stopped.",
            executed=True,
            details="watch_id=text_appeared\nprofile=text_appeared",
            normalized_target="text_appeared",
        )

        rendered = render_final_response("", result)

        self.assertIn("Зупинив фонове спостереження", rendered)
        self.assertIn("text_appeared", rendered)
        self.assertNotIn("Generic кліки", rendered)

    def test_browser_watch_not_found_response(self) -> None:
        result = self._result(
            action="browser_watch_stop",
            status="unknown_target",
            reason_code="browser_watch_not_found",
            message="Browser watch not found.",
            normalized_target="missing",
        )

        rendered = render_final_response("", result)

        self.assertIn("Не знаю такого профілю", rendered)
        self.assertIn("missing", rendered)

    def test_browser_watch_page_signal_blocked_response(self) -> None:
        result = self._result(
            action="browser_watch_start",
            status="blocked",
            reason_code="browser_observer_blocked",
            message="Browser observer blocked.",
            details="block_type=page_signal\nsignal=captcha\nlast_error=blocked_page_signal:captcha",
            normalized_target="text_appeared",
        )

        rendered = render_final_response("", result)

        self.assertIn("safety signal сторінки: captcha", rendered)
        self.assertNotIn("allowlist", rendered)

    def test_browser_watch_allowlist_blocked_response(self) -> None:
        result = self._result(
            action="browser_watch_start",
            status="blocked",
            reason_code="browser_observer_blocked",
            message="Browser observer blocked.",
            details="block_type=url_allowlist\ncurrent_url=https://evil.example/\nlast_error=url_outside_allowlist:https://evil.example/",
            normalized_target="text_appeared",
        )

        rendered = render_final_response("", result)

        self.assertIn("URL поза allowlist профілю", rendered)
        self.assertNotIn("safety signal", rendered)

    def test_browser_watch_not_running_response(self) -> None:
        result = self._result(
            action="browser_watch_stop",
            status="not_running",
            reason_code="browser_watch_not_running",
            message="Browser watch is not running.",
            details="watch_id=text_appeared\nprofile=text_appeared\nlast_status=error\nlast_error=poll_once RuntimeError: page closed",
            normalized_target="text_appeared",
        )

        rendered = render_final_response("", result)

        self.assertIn("Немає активного спостереження text_appeared", rendered)
        self.assertIn("Останній стан: error", rendered)
        self.assertIn("page closed", rendered)
        self.assertNotIn("Generic кліки", rendered)

    def test_browser_watch_last_error_credentials_are_redacted(self) -> None:
        for status in ("not_running", "command_failed"):
            with self.subTest(status=status):
                result = self._result(
                    action="browser_watch_start",
                    status=status,
                    reason_code="browser_watch_error",
                    message="Browser observer error.",
                    details="watch_id=text_appeared\nlast_status=error\nlast_error=Cookie: sid=secret-cookie",
                    normalized_target="text_appeared",
                )

                rendered = render_final_response("", result)

                self.assertIn("REDACTED", rendered)
                self.assertNotIn("secret-cookie", rendered)

    def test_browser_watch_status_response(self) -> None:
        result = self._result(
            action="browser_watch_status",
            status="executed",
            reason_code=None,
            message="Browser watch status.",
            executed=True,
            data={
                "profiles": ["text_appeared", "viewport_change_full"],
                "active_count": 1,
                "completed_count": 1,
                "valid_events_count": 2,
                "legacy_events_count": 0,
                "invalid_events_count": 0,
                "unsupported_events_count": 0,
                "active_watches": [
                    {
                        "profile": "text_appeared",
                        "status": "running",
                        "events_count": 2,
                        "last_error": None,
                        "stop_reason": None,
                    }
                ],
                "completed_watches": [
                    {
                        "profile": "viewport_change_full",
                        "status": "completed",
                        "events_count": 0,
                        "last_error": None,
                        "stop_reason": "timeout",
                    }
                ],
            },
        )

        rendered = render_final_response("", result)

        self.assertIn("Активних спостережень: 1", rendered)
        self.assertIn("валідних подій у журналі: 2", rendered)
        self.assertIn("text_appeared", rendered)
        self.assertIn("viewport_change_full", rendered)

    def test_browser_watch_status_zero_active_does_not_say_active(self) -> None:
        result = self._result(
            action="browser_watch_status",
            status="executed",
            reason_code=None,
            message="Browser watch status.",
            executed=True,
            data={
                "profiles": ["text_appeared"],
                "active_count": 0,
                "completed_count": 0,
                "valid_events_count": 2,
                "legacy_events_count": 0,
                "invalid_events_count": 0,
                "unsupported_events_count": 0,
                "active_watches": [],
                "completed_watches": [],
            },
        )

        rendered = render_final_response("", result)

        self.assertIn("зараз не запущено", rendered)
        self.assertIn("Активних спостережень немає", rendered)
        self.assertNotIn("Browser Observer працює", rendered)
        self.assertNotIn("Generic кліки", rendered)

    def test_browser_watch_status_renderer_redacts_last_error_credentials(self) -> None:
        result = self._result(
            action="browser_watch_status",
            status="executed",
            reason_code=None,
            message="Browser watch status.",
            executed=True,
            data={
                "active_count": 1,
                "completed_count": 0,
                "valid_events_count": 0,
                "legacy_events_count": 0,
                "invalid_events_count": 0,
                "unsupported_events_count": 0,
                "active_watches": [
                    {
                        "profile": "text_appeared",
                        "status": "error",
                        "events_count": 0,
                        "last_error": "Authorization: Bearer renderer-status-secret",
                        "stop_reason": None,
                    }
                ],
                "completed_watches": [],
            },
        )

        rendered = render_final_response("", result)

        self.assertIn("REDACTED", rendered)
        self.assertNotIn("renderer-status-secret", rendered)

    def test_browser_watch_no_event_response(self) -> None:
        result = self._result(
            action="browser_watch_poll_once",
            status="no_event",
            reason_code=None,
            message="Browser observer found no event.",
        )

        rendered = render_final_response("", result)

        self.assertIn("Події не знайшов", rendered)
        self.assertNotIn("Generic кліки", rendered)

    def test_browser_watch_events_response_does_not_spam_notice(self) -> None:
        result = self._result(
            action="browser_watch_events",
            status="executed",
            reason_code=None,
            message="Browser watch events.",
            executed=True,
            data={
                "events": [
                    {
                        "timestamp": "2026-07-23T10:00:00+00:00",
                        "profile": "text_appeared",
                        "event_type": "text_appeared",
                        "page": {
                            "url": "https://example.com/?token=REDACTED&view=ok",
                            "title": "Cookie: session=renderer-title-secret",
                        },
                        "message": "Authorization: Bearer renderer-message-secret",
                    }
                ],
                "returned_count": 1,
                "matched_count": 3,
                "events_count": 1,
                "matching_events_count": 1,
                "valid_events_count": 1,
                "invalid_events_count": 2,
                "unsupported_events_count": 1,
                "next_position": 4,
                "truncated": True,
                "filters": {
                    "profile": None,
                    "event_types": None,
                    "url_prefix": "https://example.com/?token=REDACTED",
                    "limit": 1,
                },
            },
        )

        rendered = render_final_response("", result)

        self.assertIn("Події Browser Observer", rendered)
        self.assertIn("text_appeared", rendered)
        self.assertIn("token=REDACTED", rendered)
        self.assertIn("Показано останні 1 з 3", rendered)
        self.assertIn("пошкоджених/невалідних: 2", rendered)
        self.assertIn("непідтримуваних версій: 1", rendered)
        self.assertNotIn("renderer-title-secret", rendered)
        self.assertNotIn("renderer-message-secret", rendered)
        self.assertNotIn("Generic кліки", rendered)

    def test_browser_watch_events_no_filter_matches_is_explicit(self) -> None:
        result = self._result(
            action="browser_watch_events",
            status="executed",
            reason_code=None,
            message="Browser watch events.",
            executed=True,
            data={
                "events": [],
                "returned_count": 0,
                "matched_count": 0,
                "events_count": 0,
                "matching_events_count": 0,
                "valid_events_count": 10,
                "skipped_records": 0,
                "next_position": 10,
                "truncated": False,
                "filters": {
                    "profile": "missing",
                    "event_types": None,
                    "since": None,
                    "until": None,
                    "site": None,
                    "url_prefix": None,
                    "limit": 5,
                    "after_event_id": None,
                    "after_position": None,
                },
            },
        )

        rendered = render_final_response("", result)

        self.assertEqual(rendered, "За заданими фільтрами подій Browser Observer не знайдено, сер.")

    def test_browser_watch_events_empty_log_reports_skipped_records_cleanly(self) -> None:
        result = self._result(
            action="browser_watch_events",
            status="executed",
            reason_code=None,
            message="Browser watch events.",
            executed=True,
            data={
                "events": [],
                "returned_count": 0,
                "matched_count": 0,
                "events_count": 0,
                "valid_events_count": 0,
                "invalid_events_count": 1,
                "unsupported_events_count": 1,
                "next_position": 2,
                "truncated": False,
                "filters": {
                    "profile": None,
                    "event_types": None,
                    "since": None,
                    "until": None,
                    "site": None,
                    "url_prefix": None,
                    "limit": 5,
                    "after_event_id": None,
                    "after_position": None,
                },
            },
        )

        rendered = render_final_response("", result)

        self.assertIn("Журнал Browser Observer поки порожній", rendered)
        self.assertIn("пошкоджених/невалідних: 1", rendered)
        self.assertIn("непідтримуваних версій: 1", rendered)

    def test_browser_watch_events_invalid_filter_error_is_ukrainian(self) -> None:
        result = self._result(
            action="browser_watch_events",
            status="invalid_params",
            reason_code="browser_watch_events_invalid_filters",
            message="Limit має бути від 1 до 100.",
        )

        rendered = render_final_response("", result)

        self.assertEqual(rendered, "Limit має бути від 1 до 100.")

    def test_browser_watch_events_filter_error_redacts_credentials(self) -> None:
        result = self._result(
            action="browser_watch_events",
            status="invalid_params",
            reason_code="browser_watch_events_invalid_filters",
            message="Authorization: Bearer renderer-filter-secret",
        )

        rendered = render_final_response("", result)

        self.assertIn("REDACTED", rendered)
        self.assertNotIn("renderer-filter-secret", rendered)

    def test_browser_watch_missing_profile_and_ambiguous_stop_are_clear(self) -> None:
        missing = self._result(
            action="browser_watch_start",
            status="invalid_params",
            reason_code="browser_watch_target_required",
            message="Укажи налаштований profile для Browser Observer, сер.",
            data={"candidates": []},
        )
        ambiguous = self._result(
            action="browser_watch_stop",
            status="ambiguous",
            reason_code="browser_watch_stop_ambiguous",
            message="Уточни watch_id.",
            data={
                "candidates": [
                    {"watch_id": "one", "profile": "one"},
                    {"watch_id": "Authorization: Bearer renderer-candidate-secret", "profile": "two"},
                ]
            },
        )

        missing_rendered = render_final_response("", missing)
        ambiguous_rendered = render_final_response("", ambiguous)

        self.assertIn("Укажи", missing_rendered)
        self.assertIn("яке спостереження зупинити", ambiguous_rendered)
        self.assertIn("one", ambiguous_rendered)
        self.assertIn("REDACTED", ambiguous_rendered)
        self.assertNotIn("renderer-candidate-secret", ambiguous_rendered)

    def test_browser_watch_missing_start_url_response(self) -> None:
        result = self._result(
            action="browser_watch_poll_once",
            status="not_configured",
            reason_code="browser_observer_not_configured",
            message="missing start_url",
            details="reason_code: profile_start_url_missing",
        )

        rendered = render_final_response("", result)

        self.assertIn("start_url", rendered)
        self.assertNotIn("Generic кліки", rendered)

    def test_browser_watch_missing_playwright_response(self) -> None:
        result = self._result(
            action="browser_watch_poll_once",
            status="not_configured",
            reason_code="browser_observer_not_configured",
            message="missing dependency",
            details="reason_code: playwright_missing",
        )

        rendered = render_final_response("", result)

        self.assertIn("Playwright", rendered)
        self.assertNotIn("Generic кліки", rendered)

    def test_browser_task_executed_response(self) -> None:
        result = self._result(
            action="browser_task_run",
            status="executed",
            reason_code=None,
            message="done",
            executed=True,
            normalized_target="humanbenchmark_aim",
            details="attempted_clicks: 30\nconfirmed_hits: 0\nmax_targets: 30\nelapsed_seconds: 12.40",
        )

        rendered = render_final_response("", result)

        self.assertIn("не можу підтвердити 30 попадань", rendered)
        self.assertIn("Спроб: 30", rendered)
        self.assertIn("підтверджено: 0", rendered)
        self.assertNotIn("Поцілив 30", rendered)

    def test_browser_task_partial_response_includes_stop_reason(self) -> None:
        result = self._result(
            action="browser_task_run",
            status="executed",
            reason_code=None,
            message="done",
            executed=True,
            normalized_target="humanbenchmark_aim",
            details=(
                "attempted_clicks: 60\n"
                "confirmed_hits: 21\n"
                "max_targets: 30\n"
                "elapsed_seconds: 39.87\n"
                "stop_reason: max_attempts; user_text=відкрий тренування аіма"
            ),
        )

        rendered = render_final_response("", result)

        self.assertIn("Зупинився, сер: max_attempts", rendered)
        self.assertNotIn("user_text=", rendered)

    def test_browser_task_confirmed_response(self) -> None:
        result = self._result(
            action="browser_task_run",
            status="executed",
            reason_code=None,
            message="done",
            executed=True,
            normalized_target="humanbenchmark_aim",
            details=(
                "attempted_clicks: 30\n"
                "confirmed_hits: 30\n"
                "max_targets: 30\n"
                "elapsed_seconds: 12.40\n"
                "final_site_result_ms: 422 ms"
            ),
        )

        rendered = render_final_response("", result)

        self.assertIn("Підтверджено 30/30", rendered)
        self.assertIn("Результат сайту: 422 ms", rendered)
        self.assertIn("Середній цикл: 413 ms", rendered)

    def test_browser_task_confirmed_without_site_result_hides_user_text(self) -> None:
        result = self._result(
            action="browser_task_run",
            status="executed",
            reason_code=None,
            message="done",
            executed=True,
            normalized_target="humanbenchmark_aim",
            details=(
                "attempted_clicks: 30\n"
                "confirmed_hits: 30\n"
                "max_targets: 30\n"
                "elapsed_seconds: 16.72\n"
                "final_site_result_ms=; user_text=відкрий тренування аіма і порази 30 цілей"
            ),
        )

        rendered = render_final_response("", result)

        self.assertEqual(
            rendered,
            "Готово, сер. Підтверджено 30/30 цілей за 16.72 секунд. Середній цикл: 557 ms.",
        )
        self.assertNotIn("Результат сайту:", rendered)
        self.assertNotIn("user_text=", rendered)

    def test_browser_task_confirmed_average_cycle_uses_confirmed_hits(self) -> None:
        result = self._result(
            action="browser_task_run",
            status="executed",
            reason_code=None,
            message="done",
            executed=True,
            normalized_target="humanbenchmark_aim",
            details=(
                "attempted_clicks: 30\n"
                "confirmed_hits: 30\n"
                "max_targets: 30\n"
                "elapsed_seconds: 22.27\n"
                "final_site_result_ms: "
            ),
        )

        rendered = render_final_response("", result)

        self.assertEqual(
            rendered,
            "Готово, сер. Підтверджено 30/30 цілей за 22.27 секунд. Середній цикл: 742 ms.",
        )

    def test_browser_task_blocked_response(self) -> None:
        result = self._result(
            action="browser_task_run",
            status="blocked",
            reason_code="browser_task_blocked",
            message="blocked",
            normalized_target="humanbenchmark_aim",
        )

        rendered = render_final_response("", result)

        self.assertIn("Browser task зупинено", rendered)

    def test_browser_task_not_configured_response(self) -> None:
        result = self._result(
            action="browser_task_run",
            status="not_configured",
            reason_code="browser_agent_not_configured",
            message="missing deps",
            normalized_target="humanbenchmark_aim",
        )

        rendered = render_final_response("", result)

        self.assertIn("Browser Agent треба спершу налаштувати", rendered)
        self.assertIn("playwright/opencv-python/numpy", rendered)

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
            "Гучність збільшив, сер.",
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
            "Запустив Spotify, сер.",
        )
        self.assertEqual(
            render_final_response(
                "",
                self._result("open_app", "executed", None, "done", executed=True, normalized_target="youtube"),
            ),
            "Запустив YouTube, сер.",
        )

    def test_status_and_set_responses(self) -> None:
        self.assertEqual(
            render_final_response(
                "",
                self._result(
                    "media_status",
                    "executed",
                    None,
                    "done",
                    executed=True,
                    details="status: Playing\nartist: Ren\ntitle: Depression",
                ),
            ),
            "Зараз грає: Ren — Depression, сер.",
        )
        self.assertEqual(
            render_final_response(
                "",
                self._result(
                    "volume_status",
                    "executed",
                    None,
                    "done",
                    executed=True,
                    details="volume_percent: 42\nmuted: True",
                ),
            ),
            "Гучність зараз 42%, але звук вимкнений, сер.",
        )
        self.assertEqual(
            render_final_response(
                "",
                self._result(
                    "volume_set",
                    "executed",
                    None,
                    "done",
                    executed=True,
                    details="level_percent: 30",
                    params={"level_percent": 30},
                ),
            ),
            "Поставив гучність на 30%, сер.",
        )

    def test_unknown_app_target_is_helpful(self) -> None:
        result = self._result(
            "open_app",
            "unknown_target",
            "app_target_not_whitelisted",
            "not in whitelist",
            normalized_target="obs",
        )

        rendered = render_final_response("", result)

        self.assertIn("Ціль не в whitelist", rendered)
        self.assertIn("obs", rendered)
        self.assertIn("steam", rendered)
        self.assertIn("OBS_COMMAND", rendered)

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
        params: dict[str, object] | None = None,
        data: dict[str, object] | None = None,
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
            params=params,
            data=data,
        )


if __name__ == "__main__":
    unittest.main()
