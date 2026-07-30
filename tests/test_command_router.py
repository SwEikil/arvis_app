from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from actions.browser_watch_manager import BrowserWatchManager
from actions.browser_watch_manager import WatchManagerResult
from actions.media import execute_media_action
from actions.volume import execute_volume_action
from command_router import CommandRouter
from intent_resolver import IntentResolver
from intent_resolver import should_pass_to_router
from main import should_try_resolver_for_result
from response_renderer import render_final_response
from schemas import ActionIntent


class CommandRouterVolumeNormalizationTests(unittest.TestCase):
    def test_open_youtube_dry_run_is_whitelisted(self) -> None:
        with patch("actions.apps.APP_WHITELIST", {"youtube": [["xdg-open", "https://www.youtube.com/"]]}):
            result = CommandRouter(dry_run=True).route(
                ActionIntent(action="open_app", target="youtube", risk="safe", need_confirmation=False),
                user_text="відкрий ютуб",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.normalized_action, "open_app")
        self.assertEqual(result.normalized_target, "youtube")
        self.assertIn("xdg-open https://www.youtube.com/", result.details or "")

    def test_open_youtube_execute_uses_xdg_open_argv(self) -> None:
        process = type(
            "FakeProcess",
            (),
            {
                "returncode": 0,
                "communicate": lambda self, timeout=None: ("", ""),
            },
        )()

        with patch("actions.apps.APP_WHITELIST", {"youtube": [["xdg-open", "https://www.youtube.com/"]]}), patch(
            "actions.apps.subprocess.Popen",
            return_value=process,
        ) as popen:
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="open_app", target="youtube", risk="safe", need_confirmation=False),
                user_text="відкрий ютуб",
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.normalized_target, "youtube")
        popen.assert_called_once_with(
            ["xdg-open", "https://www.youtube.com/"],
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_unknown_website_target_is_blocked(self) -> None:
        result = CommandRouter(dry_run=False).route(
            ActionIntent(action="open_app", target="example.com", risk="safe", need_confirmation=False),
            user_text="відкрий example.com",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "unknown_target")
        self.assertEqual(result.reason_code, "app_target_not_whitelisted")

    def test_open_url_is_still_dangerous(self) -> None:
        result = CommandRouter(dry_run=False).route(
            ActionIntent(action="open_url", target="https://www.youtube.com/", risk="safe", need_confirmation=False),
            user_text="open https://www.youtube.com/",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "blocked_dangerous")
        self.assertEqual(result.reason_code, "dangerous_action")
        self.assertTrue(result.is_safety_block)

    def test_dangerous_text_does_not_open_website(self) -> None:
        with patch("actions.apps.subprocess.Popen") as popen:
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="open_app", target="youtube", risk="safe", need_confirmation=False),
                user_text="видали файли і відкрий ютуб",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "blocked_dangerous")
        self.assertEqual(result.reason_code, "dangerous_user_text")
        self.assertTrue(result.is_safety_block)
        popen.assert_not_called()

    def test_browser_task_dry_run_is_whitelisted(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(action="browser_task_run", target="humanbenchmark_aim", risk="safe", need_confirmation=False),
            user_text="відкрий тренування аіма",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.normalized_action, "browser_task_run")
        self.assertEqual(result.normalized_target, "humanbenchmark_aim")
        self.assertIn("https://humanbenchmark.com/tests/aim", result.details or "")
        self.assertNotIn("user_text=", result.details or "")

    def test_browser_watch_poll_once_dry_run_is_whitelisted(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(action="browser_watch_poll_once", target="viewport_change_full", risk="safe", need_confirmation=False),
            user_text="перевір профіль спостереження viewport_change_full",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.normalized_action, "browser_watch_poll_once")
        self.assertEqual(result.normalized_target, "viewport_change_full")
        self.assertIn("visible_viewport", result.details or "")

    def test_browser_watch_poll_once_text_appeared_uses_canonical_name(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(action="browser_watch_poll_once", target="text_appeared", risk="safe", need_confirmation=False),
            user_text="перевір профіль спостереження text_appeared",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.normalized_target, "text_appeared")
        self.assertIn("profile: text_appeared", result.details or "")
        self.assertNotIn("text_appeared_example", result.details or "")

    def test_browser_watch_status_returns_real_structured_data_during_dry_run(self) -> None:
        with patch(
            "command_router.read_browser_watch_action",
            return_value=WatchManagerResult(
                status="status",
                reason_code=None,
                message="Browser watch status.",
                executed=True,
                data={
                    "profiles": ["text_appeared"],
                    "active_count": 0,
                    "completed_count": 0,
                    "valid_events_count": 0,
                    "legacy_events_count": 0,
                    "invalid_events_count": 0,
                    "unsupported_events_count": 0,
                    "active_watches": [],
                    "completed_watches": [],
                },
            ),
        ) as read_only:
            result = CommandRouter(dry_run=True).route(
                ActionIntent(action="browser_watch_status", target="observer", risk="safe", need_confirmation=False),
                user_text="статус спостереження",
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.data["profiles"], ["text_appeared"])
        self.assertEqual(result.data["active_watches"], [])
        self.assertIsNone(result.details)
        read_only.assert_called_once_with("browser_watch_status", "observer", {})

    def test_browser_watch_status_read_only_exception_does_not_disable_other_dry_runs(self) -> None:
        status_result = WatchManagerResult(
            status="status",
            reason_code=None,
            message="Browser watch status.",
            executed=True,
            data={
                "active_count": 0,
                "completed_count": 0,
                "valid_events_count": 0,
                "legacy_events_count": 0,
                "invalid_events_count": 0,
                "unsupported_events_count": 0,
                "active_watches": [],
                "completed_watches": [],
            },
        )
        router = CommandRouter(dry_run=True)
        with (
            patch("command_router.read_browser_watch_action", return_value=status_result) as read_only,
            patch("command_router.execute_browser_watch_action") as execute,
        ):
            status = router.route(
                ActionIntent(action="browser_watch_status", target="observer", risk="safe", need_confirmation=False),
                user_text="статус спостереження",
            )
            start = router.route(
                ActionIntent(action="browser_watch_start", target="text_appeared", risk="safe", need_confirmation=False),
                user_text="стеж за профілем text_appeared",
            )

        self.assertTrue(status.executed)
        self.assertEqual(status.status, "executed")
        self.assertFalse(start.executed)
        self.assertEqual(start.status, "dry_run")
        self.assertTrue(router.dry_run)
        read_only.assert_called_once()
        execute.assert_not_called()

    def test_browser_watch_start_dry_run(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(action="browser_watch_start", target="text_appeared", risk="safe", need_confirmation=False),
            user_text="стеж за профілем text_appeared",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.normalized_action, "browser_watch_start")
        self.assertIn("profile: text_appeared", result.details or "")

    def test_browser_watch_stop_dry_run_does_not_execute(self) -> None:
        with patch("command_router.execute_browser_watch_action") as execute:
            result = CommandRouter(dry_run=True).route(
                ActionIntent(
                    action="browser_watch_stop",
                    target="text_appeared",
                    risk="safe",
                    need_confirmation=False,
                ),
                user_text="зупини спостереження text_appeared",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        execute.assert_not_called()

    def test_browser_watch_action_without_required_profile_is_structured_error(self) -> None:
        for action in ("browser_watch_start", "browser_watch_poll_once"):
            with self.subTest(action=action):
                result = CommandRouter(dry_run=True).route(
                    ActionIntent(
                        action=action,
                        target="",
                        risk="safe",
                        need_confirmation=False,
                    ),
                    user_text="проверь страницу один раз",
                )

                self.assertFalse(result.executed)
                self.assertEqual(result.status, "invalid_params")
                self.assertEqual(result.reason_code, "browser_watch_target_required")
                self.assertEqual(result.data, {"candidates": []})

    def test_browser_watch_stop_without_target_selects_only_active_watch(self) -> None:
        status = WatchManagerResult(
            status="status",
            reason_code=None,
            message="Browser watch status.",
            executed=True,
            data={
                "active_watches": [
                    {"watch_id": "text_appeared", "profile": "text_appeared"},
                ]
            },
        )
        with patch("command_router.read_browser_watch_action", return_value=status) as read_only:
            result = CommandRouter(dry_run=True).route(
                ActionIntent(
                    action="browser_watch_stop",
                    target="",
                    risk="safe",
                    need_confirmation=False,
                ),
                user_text="останови наблюдение",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.normalized_target, "text_appeared")
        read_only.assert_called_once_with("browser_watch_status", "observer", {})

    def test_browser_watch_stop_without_target_is_ambiguous_for_multiple_watches(self) -> None:
        status = WatchManagerResult(
            status="status",
            reason_code=None,
            message="Browser watch status.",
            executed=True,
            data={
                "active_watches": [
                    {"watch_id": "one", "profile": "one"},
                    {
                        "watch_id": "Authorization: Bearer candidate-secret",
                        "profile": "two",
                    },
                ]
            },
        )
        with (
            patch("command_router.read_browser_watch_action", return_value=status),
            patch("command_router.execute_browser_watch_action") as execute,
        ):
            result = CommandRouter(dry_run=True).route(
                ActionIntent(
                    action="browser_watch_stop",
                    target="",
                    risk="safe",
                    need_confirmation=False,
                ),
                user_text="останови наблюдение",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.reason_code, "browser_watch_stop_ambiguous")
        self.assertEqual(len(result.data["candidates"]), 2)
        self.assertNotIn("candidate-secret", str(result.data))
        execute.assert_not_called()

    def test_browser_watch_stop_without_target_and_no_active_watch_is_invalid(self) -> None:
        status = WatchManagerResult(
            status="status",
            reason_code=None,
            message="Browser watch status.",
            executed=True,
            data={"active_watches": []},
        )
        with patch("command_router.read_browser_watch_action", return_value=status):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(
                    action="browser_watch_stop",
                    target="",
                    risk="safe",
                    need_confirmation=False,
                ),
                user_text="останови наблюдение",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "invalid_params")
        self.assertEqual(result.reason_code, "browser_watch_target_required")

    def test_browser_watch_start_execution_uses_manager(self) -> None:
        with patch(
            "command_router.execute_browser_watch_action",
            return_value=(True, "Browser watch started.", "watch_id=text_appeared\nprofile=text_appeared"),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_start", target="text_appeared", risk="safe", need_confirmation=False),
                user_text="стеж за профілем text_appeared",
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")

    def test_browser_watch_stop_execution_uses_manager(self) -> None:
        with patch(
            "command_router.execute_browser_watch_action",
            return_value=(True, "Browser watch stopped.", "watch_id=text_appeared\nprofile=text_appeared"),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_stop", target="text_appeared", risk="safe", need_confirmation=False),
                user_text="зупини спостереження text_appeared",
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")

    def test_browser_watch_stop_unknown_is_unknown_target(self) -> None:
        with patch(
            "command_router.execute_browser_watch_action",
            return_value=(False, "Browser watch not found.", "watch_id=missing"),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_stop", target="missing", risk="safe", need_confirmation=False),
                user_text="зупини спостереження missing",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "unknown_target")
        self.assertEqual(result.reason_code, "browser_watch_not_found")

    def test_browser_watch_stop_known_not_running_is_not_unknown(self) -> None:
        with patch(
            "command_router.execute_browser_watch_action",
            return_value=(
                False,
                "Browser watch is not running.",
                "watch_id=text_appeared\nprofile=text_appeared\nlast_status=error\nlast_error=poll_once RuntimeError: page closed",
            ),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_stop", target="text_appeared", risk="safe", need_confirmation=False),
                user_text="зупини спостереження text_appeared",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "not_running")
        self.assertEqual(result.reason_code, "browser_watch_not_running")

    def test_browser_watch_status_active_watchers(self) -> None:
        with patch(
            "command_router.read_browser_watch_action",
            return_value=WatchManagerResult(
                status="status",
                reason_code=None,
                message="Browser watch status.",
                executed=True,
                data={
                    "profiles": ["text_appeared"],
                    "active_count": 1,
                    "completed_count": 0,
                    "valid_events_count": 2,
                    "legacy_events_count": 0,
                    "invalid_events_count": 0,
                    "unsupported_events_count": 0,
                    "active_watches": [{"profile": "text_appeared"}],
                    "completed_watches": [],
                },
            ),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_status", target="observer", risk="safe", need_confirmation=False),
                user_text="статус спостереження",
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.data["active_count"], 1)
        self.assertEqual(result.data["active_watches"], [{"profile": "text_appeared"}])

    def test_browser_watch_events_list(self) -> None:
        with patch(
            "command_router.read_browser_watch_action",
            return_value=WatchManagerResult(
                status="events",
                reason_code=None,
                message="Browser watch events.",
                executed=True,
                data={
                    "events": [{"event_id": "event-1", "event_type": "text_appeared"}],
                    "returned_count": 1,
                    "matched_count": 1,
                    "events_count": 1,
                    "matching_events_count": 1,
                    "valid_events_count": 1,
                    "skipped_records": 0,
                    "next_position": 1,
                    "truncated": False,
                    "filters": {"profile": None, "event_types": None, "limit": 5},
                },
            ),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_events", target="observer", risk="safe", need_confirmation=False),
                user_text="покажи події спостереження",
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.data["events_count"], 1)
        self.assertEqual(result.data["next_position"], 1)
        self.assertIsNone(result.details)

    def test_browser_watch_events_dry_run_executes_read_only_with_filters(self) -> None:
        action_result = WatchManagerResult(
            status="events",
            reason_code=None,
            message="Browser watch events.",
            executed=True,
            data={
                "events": [],
                "returned_count": 0,
                "matched_count": 0,
                "events_count": 0,
                "next_position": 0,
                "truncated": False,
                "filters": {"profile": "text_appeared", "limit": 10},
            },
        )
        with patch("command_router.read_browser_watch_action", return_value=action_result) as read_only:
            result = CommandRouter(dry_run=True).route(
                ActionIntent(
                    action="browser_watch_events",
                    target="observer",
                    risk="safe",
                    need_confirmation=False,
                    params={
                        "profile": "text_appeared",
                        "event_types": ["text_appeared", "future_extension"],
                        "site": "example.com",
                        "after_position": 2,
                        "limit": 10,
                    },
                ),
                user_text="покажи події спостереження",
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")
        read_only.assert_called_once_with(
            "browser_watch_events",
            "observer",
            {
                "profile": "text_appeared",
                "event_types": ["text_appeared", "future_extension"],
                "site": "example.com",
                "after_position": 2,
                "limit": 10,
            },
        )

    def test_browser_watch_events_invalid_filters_return_ukrainian_error(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(
                action="browser_watch_events",
                target="observer",
                risk="safe",
                need_confirmation=False,
                params={"limit": 101},
            ),
            user_text="покажи події спостереження limit=101",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "invalid_params")
        self.assertIn("Limit має бути від 1 до 100", result.message)

    def test_browser_watch_events_boolean_limit_from_resolver_reaches_validation(self) -> None:
        resolved = IntentResolver(
            type(
                "FakeLlm",
                (),
                {
                    "chat": lambda self, messages: (
                        '{"action":"browser_watch_events","target":"observer","risk":"safe",'
                        '"need_confirmation":false,"confidence":0.9,"params":{"limit":true}}',
                        None,
                    )
                },
            )()
        ).resolve("покажи observer журнал", use_llm=True)
        intent = resolved.to_action_intent()
        assert intent is not None

        result = CommandRouter(dry_run=True).route(intent, user_text="покажи observer журнал")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "invalid_params")
        self.assertIn("цілим числом", result.message)
        self.assertIs(result.params["limit"], True)

    def test_browser_watch_events_naive_time_from_resolver_reaches_validation(self) -> None:
        resolved = IntentResolver().resolve(
            "покажи события с 2026-07-23T10:00:00",
            use_llm=False,
        )
        intent = resolved.to_action_intent()
        assert intent is not None

        result = CommandRouter(dry_run=True).route(
            intent,
            user_text="покажи события с 2026-07-23T10:00:00",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "invalid_params")
        self.assertIn("timezone", result.message)

    def test_browser_watch_events_malformed_time_from_resolver_reaches_validation(self) -> None:
        resolved = IntentResolver().resolve(
            "покажи события since=not-a-time",
            use_llm=False,
        )
        intent = resolved.to_action_intent()
        assert intent is not None

        result = CommandRouter(dry_run=True).route(
            intent,
            user_text="покажи события since=not-a-time",
        )

        self.assertEqual(resolved.params, {"since": "not-a-time"})
        self.assertFalse(result.executed)
        self.assertEqual(result.status, "invalid_params")
        self.assertIn("ISO 8601", result.message)

    def test_browser_watch_events_unknown_natural_filter_is_not_silently_dropped(self) -> None:
        resolved = IntentResolver().resolve(
            "покажи события unknown_filter=value",
            use_llm=False,
        )
        intent = resolved.to_action_intent()
        assert intent is not None

        result = CommandRouter(dry_run=True).route(intent, user_text="покажи события")

        self.assertEqual(resolved.params, {"unknown_filter": "value"})
        self.assertFalse(result.executed)
        self.assertEqual(result.status, "invalid_params")
        self.assertIn("невідомий фільтр", result.message)

    def test_browser_watch_event_credentials_are_not_stored_in_command_result(self) -> None:
        user_text = (
            "покажи события "
            "url_prefix=https://user:password@example.com/page?token=router-secret&view=ok "
            "limit=true"
        )
        resolved = IntentResolver().resolve(user_text, use_llm=False)
        intent = resolved.to_action_intent()
        assert intent is not None

        result = CommandRouter(dry_run=True).route(intent, user_text=user_text)

        serialized = str(
            {
                "params": result.params,
                "original_target": result.original_target,
                "normalized_target": result.normalized_target,
                "original_user_text": result.original_user_text,
                "message": result.message,
                "data": result.data,
            }
        )
        self.assertFalse(result.executed)
        self.assertEqual(result.status, "invalid_params")
        self.assertIn("view=ok", serialized)
        self.assertNotIn("router-secret", serialized)
        self.assertNotIn("user:password", serialized)

    def test_browser_watch_target_is_sanitized_before_normalization(self) -> None:
        secret = "direct-target-secret"
        result = CommandRouter(dry_run=True).route(
            ActionIntent(
                action="browser_watch_start",
                target=f"https://user:password@example.com/page?token={secret}&view=ok",
                risk="safe",
                need_confirmation=False,
            ),
            user_text=(
                "start browser watch "
                f"https://user:password@example.com/page?token={secret}&view=ok"
            ),
        )

        serialized = str(result)
        rendered = render_final_response("", result)
        self.assertFalse(result.executed)
        self.assertNotIn(secret, serialized)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("user:password", serialized)
        self.assertNotIn("user:password", rendered)
        self.assertIn("REDACTED", serialized)
        self.assertIn("redacted", rendered.lower())

    def test_browser_watch_events_command_result_data_is_privacy_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".runtime" / "browser_observer" / "events.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "event-private",
                        "watch_id": "observer",
                        "timestamp": "2026-07-23T10:00:00+00:00",
                        "event_type": "page_changed",
                        "source": "browser_observer",
                        "profile": "observer",
                        "page": {
                            "url": (
                                "https://user:password@example.com/page?"
                                "token=command-result-secret&view=ok"
                            )
                        },
                        "message": "Authorization: Bearer command-message-secret",
                        "payload": {
                            "cookie_header": "sid=command-cookie-secret",
                            "safe": "keep",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            manager_result = BrowserWatchManager(
                project_root=tmp,
                start_threads=False,
            ).events("observer", {})

            with patch(
                "command_router.read_browser_watch_action",
                return_value=manager_result,
            ):
                result = CommandRouter(dry_run=True).route(
                    ActionIntent(
                        action="browser_watch_events",
                        target="observer",
                        risk="safe",
                        need_confirmation=False,
                    ),
                    user_text="покажи последние события браузера",
                )

        serialized = json.dumps(result.data)
        rendered = render_final_response("", result)
        for marker in (
            "command-result-secret",
            "command-message-secret",
            "command-cookie-secret",
            "user:password",
        ):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, serialized)
                self.assertNotIn(marker, rendered)
        self.assertIn("view=ok", serialized)
        self.assertIn('"safe": "keep"', serialized)
        self.assertEqual(result.data["events"][0]["message"], "Authorization: REDACTED")
        self.assertEqual(result.data["events"][0]["payload"]["cookie_header"], "REDACTED")

    def test_unknown_browser_watch_profile_is_blocked(self) -> None:
        result = CommandRouter(dry_run=False).route(
            ActionIntent(action="browser_watch_poll_once", target="random_site", risk="safe", need_confirmation=False),
            user_text="poll watch profile random_site",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "unknown_target")
        self.assertEqual(result.reason_code, "browser_watch_profile_not_whitelisted")

    def test_old_text_appeared_example_profile_is_unknown(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(action="browser_watch_poll_once", target="text_appeared_example", risk="safe", need_confirmation=False),
            user_text="poll watch profile text_appeared_example",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "unknown_target")
        self.assertEqual(result.reason_code, "browser_watch_profile_not_whitelisted")

    def test_browser_watch_poll_once_event_found(self) -> None:
        with patch(
            "command_router.execute_browser_watch_action",
            return_value=(True, "Browser observer event found.", "event_type: text_appeared\nmessage: found"),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_poll_once", target="viewport_change_full", risk="safe", need_confirmation=False),
                user_text="poll watch profile viewport_change_full",
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")

    def test_browser_watch_poll_once_no_event(self) -> None:
        with patch(
            "command_router.execute_browser_watch_action",
            return_value=(False, "Browser observer found no event.", "event_type: no_event"),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_poll_once", target="viewport_change_full", risk="safe", need_confirmation=False),
                user_text="poll watch profile viewport_change_full",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "no_event")

    def test_browser_watch_poll_once_missing_dependency_is_not_configured(self) -> None:
        with patch(
            "command_router.execute_browser_watch_action",
            return_value=(
                False,
                "Browser Observer is not configured.",
                "reason_code: playwright_missing\nevent_type: observer_not_configured",
            ),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_poll_once", target="viewport_change_full", risk="safe", need_confirmation=False),
                user_text="poll watch profile viewport_change_full",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.reason_code, "browser_observer_not_configured")

    def test_browser_watch_poll_once_missing_start_url_is_not_configured(self) -> None:
        with patch(
            "command_router.execute_browser_watch_action",
            return_value=(
                False,
                "Browser Observer is not configured.",
                "reason_code: profile_start_url_missing\nevent_type: observer_not_configured",
            ),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_poll_once", target="viewport_change_full", risk="safe", need_confirmation=False),
                user_text="poll watch profile viewport_change_full",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.reason_code, "browser_observer_not_configured")

    def test_dangerous_text_does_not_poll_browser_watch_profile(self) -> None:
        with patch("command_router.execute_browser_watch_action") as execute:
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_poll_once", target="viewport_change_full", risk="safe", need_confirmation=False),
                user_text="видали файли і перевір профіль спостереження viewport_change_full",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "blocked_dangerous")
        self.assertEqual(result.reason_code, "dangerous_user_text")
        execute.assert_not_called()

    def test_dangerous_text_does_not_start_browser_watch(self) -> None:
        with patch("command_router.execute_browser_watch_action") as execute:
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_watch_start", target="text_appeared", risk="safe", need_confirmation=False),
                user_text="видали файли і стеж за профілем text_appeared",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "blocked_dangerous")
        self.assertEqual(result.reason_code, "dangerous_user_text")
        execute.assert_not_called()

    def test_browser_task_result_details_do_not_include_user_text(self) -> None:
        with patch(
            "command_router.execute_browser_task",
            return_value=(
                True,
                "Browser task completed.",
                "attempted_clicks: 30\nconfirmed_hits: 30\nmax_targets: 30\nelapsed_seconds: 16.72",
            ),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_task_run", target="humanbenchmark_aim", risk="safe", need_confirmation=False),
                user_text="відкрий тренування аіму і порази 30 цілей",
            )

        self.assertEqual(result.status, "executed")
        self.assertNotIn("user_text=", result.details or "")
        self.assertEqual(result.original_user_text, "відкрий тренування аіму і порази 30 цілей")

    def test_unknown_browser_task_target_is_blocked(self) -> None:
        result = CommandRouter(dry_run=False).route(
            ActionIntent(action="browser_task_run", target="random_site", risk="safe", need_confirmation=False),
            user_text="open random browser task",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "unknown_target")
        self.assertEqual(result.reason_code, "browser_task_target_not_whitelisted")

    def test_dangerous_text_does_not_run_browser_task(self) -> None:
        with patch("command_router.execute_browser_task") as execute:
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_task_run", target="humanbenchmark_aim", risk="safe", need_confirmation=False),
                user_text="видали файли і відкрий aim trainer",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "blocked_dangerous")
        self.assertEqual(result.reason_code, "dangerous_user_text")
        self.assertTrue(result.is_safety_block)
        execute.assert_not_called()

    def test_browser_task_blocked_outcome(self) -> None:
        with patch(
            "command_router.execute_browser_task",
            return_value=(False, "Browser task blocked.", "last_error: navigated_outside_whitelist:https://ads.example/"),
        ):
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="browser_task_run", target="humanbenchmark_aim", risk="safe", need_confirmation=False),
                user_text="open aim trainer",
            )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason_code, "browser_task_blocked")

    def test_legacy_aim_game_module_normalizes_to_browser_task(self) -> None:
        with patch(
            "command_router.execute_browser_task",
            return_value=(
                True,
                "Browser task completed.",
                "attempted_clicks: 0\nconfirmed_hits: 0\nmax_targets: 30\nelapsed_seconds: 0.00",
            ),
        ) as execute:
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="launch_game_module", target="aim_trainer", risk="safe", need_confirmation=False),
                user_text="відкрий тренування aimу і порази 30 цілей",
            )

        self.assertEqual(result.normalized_action, "browser_task_run")
        self.assertEqual(result.normalized_target, "humanbenchmark_aim")
        self.assertNotEqual(result.normalized_action, "launch_game_module")
        execute.assert_called_once_with("humanbenchmark_aim")
        self.assertFalse(result.is_safety_block)

    def test_legacy_aim_training_target_normalizes_to_browser_task(self) -> None:
        with patch(
            "command_router.execute_browser_task",
            return_value=(
                True,
                "Browser task completed.",
                "attempted_clicks: 0\nconfirmed_hits: 0\nmax_targets: 30\nelapsed_seconds: 0.00",
            ),
        ) as execute:
            result = CommandRouter(dry_run=False).route(
                ActionIntent(action="launch_game_module", target="aim_training", risk="safe", need_confirmation=False),
                user_text="відкрий тренування аіму і порази 30 цілей",
            )

        self.assertEqual(result.normalized_action, "browser_task_run")
        self.assertEqual(result.normalized_target, "humanbenchmark_aim")
        execute.assert_called_once_with("humanbenchmark_aim")

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

    def test_volume_set_dry_run_uses_level_percent(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(
                action="volume_set",
                target="system",
                risk="safe",
                need_confirmation=False,
                params={"level_percent": 30},
            ),
            user_text="постав звук на 30",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.params, {"level_percent": 30})
        self.assertIn("30%", result.details or "")

    def test_volume_status_dry_run_is_whitelisted(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(action="volume_status", target="system", risk="safe", need_confirmation=False),
            user_text="яка гучність?",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.normalized_action, "volume_status")

    def test_media_status_dry_run_is_whitelisted(self) -> None:
        result = CommandRouter(dry_run=True).route(
            ActionIntent(action="media_status", target="media", risk="safe", need_confirmation=False),
            user_text="що зараз грає?",
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.normalized_action, "media_status")

    def test_volume_status_execute_parses_wpctl_output(self) -> None:
        with patch(
            "actions.volume.subprocess.run",
            return_value=subprocess.CompletedProcess(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                0,
                "Volume: 0.42 [MUTED]\n",
                "",
            ),
        ):
            executed, message, details = execute_volume_action("volume_status")

        self.assertTrue(executed)
        self.assertEqual(message, "Volume status fetched.")
        self.assertIn("volume_percent: 42", details or "")
        self.assertIn("muted: True", details or "")

    def test_volume_set_execute_uses_clamped_percent(self) -> None:
        with patch(
            "actions.volume.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as run:
            executed, message, details = execute_volume_action("volume_set", {"level_percent": 999})

        self.assertTrue(executed)
        self.assertIn("100%", message)
        self.assertIn("level_percent: 100", details or "")
        run.assert_called_once_with(
            ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "100%"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_media_status_execute_reads_selected_player_metadata(self) -> None:
        responses = [
            subprocess.CompletedProcess(["playerctl", "-l"], 0, "brave.instance\nspotify\n", ""),
            subprocess.CompletedProcess(["playerctl", "-p", "spotify", "status"], 0, "Playing\n", ""),
            subprocess.CompletedProcess(["playerctl", "-p", "spotify", "metadata", "artist"], 0, "Ren\n", ""),
            subprocess.CompletedProcess(["playerctl", "-p", "spotify", "metadata", "title"], 0, "Depression\n", ""),
            subprocess.CompletedProcess(["playerctl", "-p", "spotify", "metadata", "album"], 1, "", "missing"),
        ]

        with patch("actions.media._run_playerctl", side_effect=responses):
            executed, message, details = execute_media_action("media_status")

        self.assertTrue(executed)
        self.assertEqual(message, "Media status fetched.")
        self.assertIn("player: spotify", details or "")
        self.assertIn("artist: Ren", details or "")
        self.assertIn("title: Depression", details or "")

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

    def test_minecraft_server_start_script_missing_is_not_configured(self) -> None:
        with patch(
            "command_router.execute_minecraft_server_action",
            return_value=type(
                "FakeMinecraftResult",
                (),
                {
                    "executed": False,
                    "status": "not_configured",
                    "reason_code": "minecraft_start_script_missing",
                    "message": "Minecraft start script is missing.",
                    "details": "path=/missing/start-server.sh",
                    "is_safety_block": False,
                },
            )(),
        ):
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
        self.assertEqual(result.reason_code, "minecraft_start_script_missing")
        self.assertFalse(result.is_safety_block)
        self.assertIn("start script", result.message.lower())

    def test_minecraft_generic_action_aliases_normalize_to_default(self) -> None:
        cases = [
            ("stop_server", "minecraft_server_stop"),
            ("start_server", "minecraft_server_start"),
            ("restart_server", "minecraft_server_restart"),
            ("server_status", "minecraft_server_status"),
        ]

        for raw_action, expected_action in cases:
            with self.subTest(raw_action=raw_action), patch(
                "command_router.execute_minecraft_server_action",
                return_value=type(
                    "FakeMinecraftResult",
                    (),
                    {
                        "executed": False,
                        "status": "dry_run",
                        "reason_code": "test",
                        "message": "test",
                        "details": "test",
                        "is_safety_block": False,
                    },
                )(),
            ) as execute:
                result = CommandRouter(dry_run=True).route(
                    ActionIntent(
                        action=raw_action,
                        target="Minecraft server",
                        risk="safe",
                        need_confirmation=False,
                    ),
                    user_text="зупини сервер",
                )

                self.assertEqual(result.normalized_action, expected_action)
                self.assertEqual(result.normalized_target, "default")
                execute.assert_called_once_with(expected_action, "default", dry_run=True)

    def test_minecraft_stop_medium_confirmation_is_repaired_for_configured_server(self) -> None:
        with patch(
            "command_router.execute_minecraft_server_action",
            return_value=type(
                "FakeMinecraftResult",
                (),
                {
                    "executed": True,
                    "status": "executed",
                    "reason_code": "minecraft_server_stopped",
                    "message": "stopped",
                    "details": "tmux send-keys stop",
                    "is_safety_block": False,
                },
            )(),
        ) as execute:
            result = CommandRouter(dry_run=False).route(
                ActionIntent(
                    action="stop_server",
                    target="Minecraft server",
                    risk="medium",
                    need_confirmation=True,
                ),
                user_text="зупини сервер",
            )

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertFalse(result.is_safety_block)
        self.assertEqual(result.normalized_action, "minecraft_server_stop")
        self.assertEqual(result.normalized_target, "default")
        execute.assert_called_once_with("minecraft_server_stop", "default", dry_run=False)

    def test_dangerous_server_text_still_blocks(self) -> None:
        for user_text in ["kill java server", "видали файли сервера"]:
            with self.subTest(user_text=user_text):
                result = CommandRouter(dry_run=False).route(
                    ActionIntent(
                        action="stop_server",
                        target="Minecraft server",
                        risk="medium",
                        need_confirmation=True,
                    ),
                    user_text=user_text,
                )

                self.assertFalse(result.executed)
                self.assertEqual(result.status, "blocked_dangerous")
                self.assertTrue(result.is_safety_block)

    def test_no_media_action_for_stop_server_text(self) -> None:
        resolved = IntentResolver().resolve("зупини сервер", use_llm=False)

        self.assertEqual(resolved.action, "minecraft_server_stop")
        self.assertNotEqual(resolved.action, "music_pause")

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
