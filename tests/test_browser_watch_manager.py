from __future__ import annotations

import inspect
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from actions import browser_watch_manager
from actions.browser_observer import BrowserObserver
from actions.browser_observer import WatchEvent
from actions.browser_observer import WatchProfile
from actions.browser_observer_runtime import BrowserRuntimeResult
from actions.browser_watch_manager import BrowserWatchManager


class BrowserWatchManagerTests(unittest.TestCase):
    def test_start_watch_creates_active_watch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = BrowserWatchManager(
                project_root=tmp,
                observer=Mock(spec=BrowserObserver),
                page_provider=FakeProvider(),
                start_threads=False,
            )

            result = manager.start_watch(_profile())
            status = manager.status()

            self.assertEqual(result.status, "started")
            self.assertEqual(status.data["active_count"], 1)
            self.assertEqual(status.data["completed_count"], 0)
            self.assertEqual(status.data["valid_events_count"], 0)
            self.assertIsNone(status.details)
            watch = status.data["active_watches"][0]
            self.assertEqual(watch["watch_id"], "text_appeared")
            self.assertEqual(watch["profile"], "text_appeared")
            self.assertEqual(watch["source"], "background_watch")
            self.assertEqual(watch["status"], "running")
            self.assertIsNotNone(watch["started_at"])
            self.assertIsNone(watch["completed_at"])
            self.assertIsNone(watch["last_event_type"])
            self.assertIsNone(watch["last_event_at"])
            self.assertIsNone(watch["last_error"])
            self.assertIsNone(watch["stop_reason"])
            self.assertEqual(
                watch["limits"],
                {
                    "interval_ms": 500,
                    "timeout_seconds": 300,
                    "debounce_seconds": 30,
                    "max_events_per_minute": 30,
                },
            )
            manager.shutdown_all()

    def test_status_without_watches_returns_empty_structured_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = BrowserWatchManager(
                project_root=tmp,
                observer=Mock(spec=BrowserObserver),
                page_provider=FakeProvider(),
                start_threads=False,
            )

            status = manager.status()

        self.assertTrue(status.executed)
        self.assertEqual(status.data["active_count"], 0)
        self.assertEqual(status.data["completed_count"], 0)
        self.assertEqual(status.data["valid_events_count"], 0)
        self.assertEqual(status.data["legacy_events_count"], 0)
        self.assertEqual(status.data["invalid_events_count"], 0)
        self.assertEqual(status.data["unsupported_events_count"], 0)
        self.assertEqual(status.data["active_watches"], [])
        self.assertEqual(status.data["completed_watches"], [])

    def test_duplicate_start_returns_already_running(self) -> None:
        manager = BrowserWatchManager(observer=Mock(spec=BrowserObserver), page_provider=FakeProvider(), start_threads=False)

        first = manager.start_watch(_profile())
        second = manager.start_watch(_profile())

        self.assertEqual(first.status, "started")
        self.assertEqual(second.status, "already_running")
        manager.shutdown_all()

    def test_max_active_watches_blocks_extra_contexts(self) -> None:
        manager = BrowserWatchManager(
            observer=Mock(spec=BrowserObserver),
            page_provider=FakeProvider(),
            start_threads=False,
            max_active_watches=1,
        )

        first = manager.start_watch(_profile("one"))
        second = manager.start_watch(_profile("two"))

        self.assertEqual(first.status, "started")
        self.assertEqual(second.status, "too_many_watches")
        manager.shutdown_all()

    def test_stop_watch_closes_session(self) -> None:
        provider = FakeProvider()
        observer = Mock(spec=BrowserObserver)
        observer.poll_once.return_value = None
        manager = BrowserWatchManager(
            observer=observer,
            page_provider=provider,
            sleep_func=lambda seconds: time.sleep(0.01),
        )
        manager.start_watch(_profile())

        result = manager.stop_watch("text_appeared")

        self.assertEqual(result.status, "stopped")
        self.assertTrue(provider.session.closed)

    def test_stop_unknown_watch_returns_not_found(self) -> None:
        manager = BrowserWatchManager(observer=Mock(spec=BrowserObserver), page_provider=FakeProvider(), start_threads=False)

        result = manager.stop_watch("missing")

        self.assertEqual(result.status, "not_found")

    def test_stop_known_profile_with_no_active_watch_returns_not_running(self) -> None:
        manager = BrowserWatchManager(observer=Mock(spec=BrowserObserver), page_provider=FakeProvider(), start_threads=False)

        result = manager.stop_watch("text_appeared")

        self.assertEqual(result.status, "not_running")
        self.assertEqual(result.reason_code, "browser_watch_not_running")
        self.assertIn("last_status: not_started", result.details or "")

    def test_stop_completed_error_watch_returns_not_running(self) -> None:
        observer = Mock(spec=BrowserObserver)
        observer.poll_once.side_effect = RuntimeError("page closed")
        manager = BrowserWatchManager(
            observer=observer,
            page_provider=FakeProvider(),
            sleep_func=lambda seconds: None,
        )

        manager.start_watch(_profile())
        _wait_until_completed(manager)
        result = manager.stop_watch("text_appeared")

        self.assertEqual(result.status, "not_running")
        self.assertEqual(result.reason_code, "browser_watch_not_running")
        self.assertIn("last_status: error", result.details or "")
        self.assertIn("poll_once RuntimeError: page closed", result.details or "")

    def test_timeout_stops_watch(self) -> None:
        observer = Mock(spec=BrowserObserver)
        observer.poll_once.return_value = None
        clock = FakeClock([0.0, 10.0])
        manager = BrowserWatchManager(
            observer=observer,
            page_provider=FakeProvider(),
            time_func=clock.time,
            sleep_func=lambda seconds: None,
            max_loop_iterations=3,
        )

        manager.start_watch(_profile(timeout_seconds=1))
        _wait_until_completed(manager)
        status = manager.status()

        completed = status.data["completed_watches"][0]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["stop_reason"], "timeout")
        self.assertIsNone(completed["last_error"])

    def test_url_outside_allowlist_blocks_before_start(self) -> None:
        manager = BrowserWatchManager(observer=Mock(spec=BrowserObserver), page_provider=FakeProvider(), start_threads=False)

        result = manager.start_watch(_profile(start_url="https://evil.example/"))

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason_code, "start_url_not_allowed")

    def test_missing_playwright_returns_not_configured(self) -> None:
        provider = FakeProvider(result=BrowserRuntimeResult(status="not_configured", reason_code="playwright_missing", message="missing"))
        manager = BrowserWatchManager(observer=Mock(spec=BrowserObserver), page_provider=provider)

        result = manager.start_watch(_profile())

        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.reason_code, "playwright_missing")

    def test_event_written_to_jsonl_with_background_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            observer = BrowserObserver(project_root=tmp)
            observer.poll_once = Mock(return_value=_event("text_appeared"))  # type: ignore[method-assign]
            manager = BrowserWatchManager(
                project_root=tmp,
                observer=observer,
                page_provider=FakeProvider(),
                sleep_func=lambda seconds: None,
                max_loop_iterations=1,
            )

            manager.start_watch(_profile())
            _wait_until_completed(manager)

            event_log = Path(tmp) / ".runtime" / "browser_observer" / "events.jsonl"
            text = event_log.read_text(encoding="utf-8")
            self.assertIn('"source": "background_watch"', text)
            self.assertIn('"profile": "text_appeared"', text)
            self.assertIn('"event_type": "text_appeared"', text)

    def test_duplicate_event_debounce_suppresses_repeat(self) -> None:
        observer = Mock(spec=BrowserObserver)
        observer.poll_once.side_effect = [_event("text_appeared"), _event("text_appeared")]
        manager = BrowserWatchManager(
            observer=observer,
            page_provider=FakeProvider(),
            sleep_func=lambda seconds: None,
            time_func=lambda: 0.0,
            max_loop_iterations=2,
        )

        manager.start_watch(_profile())
        _wait_until_completed(manager)
        status = manager.status()

        completed = status.data["completed_watches"][0]
        self.assertEqual(completed["events_count"], 1)
        self.assertEqual(completed["suppressed_duplicates"], 1)

    def test_rate_limit_suppresses_excess_events(self) -> None:
        observer = Mock(spec=BrowserObserver)
        observer.poll_once.side_effect = [_event("one", message="one"), _event("two", message="two")]
        manager = BrowserWatchManager(
            observer=observer,
            page_provider=FakeProvider(),
            sleep_func=lambda seconds: None,
            time_func=lambda: 0.0,
            max_loop_iterations=2,
            max_events_per_minute=1,
        )

        manager.start_watch(_profile())
        _wait_until_completed(manager)
        status = manager.status()

        completed = status.data["completed_watches"][0]
        self.assertEqual(completed["events_count"], 1)
        self.assertEqual(completed["suppressed_rate_limited"], 1)

    def test_safety_signal_blocks_watch(self) -> None:
        observer = Mock(spec=BrowserObserver)
        observer.poll_once.return_value = None
        page = FakePage(content="captcha challenge")
        manager = BrowserWatchManager(
            observer=observer,
            page_provider=FakeProvider(page=page),
            sleep_func=lambda seconds: None,
            max_loop_iterations=1,
        )

        manager.start_watch(_profile())
        _wait_until_completed(manager)
        status = manager.status()

        completed = status.data["completed_watches"][0]
        self.assertEqual(completed["status"], "blocked")
        self.assertEqual(completed["stop_reason"], "blocked_page_signal:captcha")
        self.assertIsNone(completed["last_error"])

    def test_benign_permission_text_does_not_block(self) -> None:
        observer = Mock(spec=BrowserObserver)
        observer.poll_once.return_value = None
        page = FakePage(content="Example Domain without prior coordination or asking for permission.")
        manager = BrowserWatchManager(
            observer=observer,
            page_provider=FakeProvider(page=page),
            sleep_func=lambda seconds: None,
            max_loop_iterations=1,
        )

        start = manager.start_watch(_profile())
        _wait_until_completed(manager)
        status = manager.status()

        self.assertEqual(start.status, "started")
        completed = status.data["completed_watches"][0]
        self.assertEqual(completed["status"], "completed")
        self.assertNotIn("blocked_page_signal", completed["stop_reason"])

    def test_example_domain_content_writes_text_event_without_permission_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            observer = BrowserObserver(project_root=tmp)
            page = FakePage(content="Example Domain\nThis domain is for use in illustrative examples without prior coordination or asking for permission.")
            manager = BrowserWatchManager(
                project_root=tmp,
                observer=observer,
                page_provider=FakeProvider(page=page),
                sleep_func=lambda seconds: None,
                max_loop_iterations=1,
            )

            start = manager.start_watch(_profile())
            _wait_until_completed(manager)
            status = manager.status()

            event_log = Path(tmp) / ".runtime" / "browser_observer" / "events.jsonl"
            text = event_log.read_text(encoding="utf-8")
            self.assertEqual(start.status, "started")
            self.assertIn('"event_type": "text_appeared"', text)
            self.assertIn('"profile": "text_appeared"', text)
            self.assertNotIn("blocked_page_signal", status.data["completed_watches"][0]["stop_reason"])

    def test_specific_permission_prompts_block_watch(self) -> None:
        for content, signal in [
            ("please allow notifications to continue", "allow_notifications"),
            ("camera permission is required", "camera_permission"),
        ]:
            with self.subTest(content=content):
                observer = Mock(spec=BrowserObserver)
                observer.poll_once.return_value = None
                manager = BrowserWatchManager(
                    observer=observer,
                    page_provider=FakeProvider(page=FakePage(content=content)),
                    sleep_func=lambda seconds: None,
                    max_loop_iterations=1,
                )

                result = manager.start_watch(_profile())
                _wait_until_completed(manager)
                status = manager.status()

                self.assertEqual(result.status, "blocked")
                completed = status.data["completed_watches"][0]
                self.assertEqual(completed["stop_reason"], f"blocked_page_signal:{signal}")

    def test_payment_checkout_and_captcha_block_watch(self) -> None:
        for content, signal in [
            ("captcha challenge", "captcha"),
            ("continue to checkout", "checkout"),
            ("payment required", "payment"),
        ]:
            with self.subTest(content=content):
                observer = Mock(spec=BrowserObserver)
                observer.poll_once.return_value = None
                manager = BrowserWatchManager(
                    observer=observer,
                    page_provider=FakeProvider(page=FakePage(content=content)),
                    sleep_func=lambda seconds: None,
                    max_loop_iterations=1,
                )

                result = manager.start_watch(_profile())
                _wait_until_completed(manager)
                status = manager.status()

                self.assertEqual(result.status, "blocked")
                completed = status.data["completed_watches"][0]
                self.assertEqual(completed["stop_reason"], f"blocked_page_signal:{signal}")

    def test_success_path_keeps_watch_running_until_stop(self) -> None:
        observer = Mock(spec=BrowserObserver)
        observer.poll_once.return_value = _event("text_appeared")
        manager = BrowserWatchManager(
            observer=observer,
            page_provider=FakeProvider(),
            sleep_func=lambda seconds: time.sleep(0.01),
        )

        start = manager.start_watch(_profile())
        status = manager.status()
        stop = manager.stop_watch("text_appeared")

        self.assertEqual(start.status, "started")
        self.assertEqual(status.data["active_count"], 1)
        self.assertEqual(len(status.data["active_watches"]), 1)
        self.assertEqual(stop.status, "stopped")

    def test_observer_error_event_has_meaningful_last_error(self) -> None:
        observer = Mock(spec=BrowserObserver)
        observer.poll_once.return_value = WatchEvent(
            watch_id="text_appeared",
            event_type="observer_error",
            confidence=None,
            message="error",
            timestamp="2026-07-03T00:00:00+00:00",
            region=None,
            bbox=None,
            center=None,
            screenshot_path=None,
            metadata={"error_type": "RuntimeError", "error": "page closed"},
        )
        manager = BrowserWatchManager(
            observer=observer,
            page_provider=FakeProvider(),
            sleep_func=lambda seconds: None,
        )

        manager.start_watch(_profile())
        _wait_until_completed(manager)
        status = manager.status()

        completed = status.data["completed_watches"][0]
        self.assertEqual(completed["last_error"], "poll_once RuntimeError: page closed")
        self.assertNotEqual(completed["last_error"], "error")

    def test_status_normalizes_completion_and_last_event_times_to_utc(self) -> None:
        timestamps = iter(
            [
                "2026-07-30T12:00:00+02:00",
                "2026-07-30T12:05:00+02:00",
            ]
        )
        observer = Mock(spec=BrowserObserver)
        manager = BrowserWatchManager(
            observer=observer,
            page_provider=FakeProvider(),
            timestamp_func=lambda: next(timestamps),
            start_threads=False,
        )
        manager.start_watch(_profile())
        with manager._lock:  # noqa: SLF001 - test verifies in-memory status serialization.
            watch = manager._active["text_appeared"]  # noqa: SLF001
        event = _event("text_appeared")
        event.timestamp = "2026-07-30T15:03:00+05:00"
        manager._record_event(watch, event)  # noqa: SLF001

        manager.stop_watch("text_appeared")
        status = manager.status()

        completed = status.data["completed_watches"][0]
        self.assertEqual(completed["started_at"], "2026-07-30T10:00:00+00:00")
        self.assertEqual(completed["completed_at"], "2026-07-30T10:05:00+00:00")
        self.assertEqual(completed["stop_reason"], "explicit_stop")
        self.assertEqual(completed["last_event_type"], "text_appeared")
        self.assertEqual(completed["last_event_at"], "2026-07-30T10:03:00+00:00")
        self.assertEqual(completed["events_count"], 1)

    def test_status_preserves_repeated_completed_watches_for_same_profile(self) -> None:
        manager = BrowserWatchManager(
            observer=Mock(spec=BrowserObserver),
            page_provider=FakeProvider(),
            start_threads=False,
        )

        for _ in range(2):
            manager.start_watch(_profile())
            manager.stop_watch("text_appeared")
        status = manager.status()

        self.assertEqual(status.data["completed_count"], 2)
        self.assertEqual(len(status.data["completed_watches"]), 2)
        self.assertEqual(
            [watch["watch_id"] for watch in status.data["completed_watches"]],
            ["text_appeared", "text_appeared"],
        )

    def test_status_reports_journal_diagnostic_counters(self) -> None:
        legacy = {
            "watch_id": "legacy",
            "event_type": "text_appeared",
            "message": "found",
            "timestamp": "2026-07-23T10:00:00+00:00",
            "metadata": {},
        }
        future = _stored_event("future", "future-event", "text_appeared", "2026-07-23T10:01:00+00:00")
        future["schema_version"] = 2
        with tempfile.TemporaryDirectory() as tmp:
            event_log = Path(tmp) / ".runtime" / "browser_observer" / "events.jsonl"
            event_log.parent.mkdir(parents=True)
            event_log.write_text(
                "\n".join(
                    [
                        json.dumps(_stored_event("one", "event-1", "text_appeared", "2026-07-23T10:00:00+00:00")),
                        json.dumps(legacy),
                        "{",
                        json.dumps(future),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            manager = BrowserWatchManager(project_root=tmp, start_threads=False)

            status = manager.status()

        self.assertEqual(status.data["valid_events_count"], 2)
        self.assertEqual(status.data["events_count"], 2)
        self.assertEqual(status.data["legacy_events_count"], 1)
        self.assertEqual(status.data["invalid_events_count"], 1)
        self.assertEqual(status.data["unsupported_events_count"], 1)

    def test_status_redacts_credentials_in_watch_data(self) -> None:
        manager = BrowserWatchManager(
            observer=Mock(spec=BrowserObserver),
            page_provider=FakeProvider(),
            start_threads=False,
        )
        manager.start_watch(_profile())
        with manager._lock:  # noqa: SLF001 - test verifies status privacy.
            watch = manager._active["text_appeared"]  # noqa: SLF001
            watch.last_error = "Authorization: Bearer status-error-secret"
            watch.stop_reason = "Cookie: sid=status-reason-secret"

        status = manager.status()
        serialized = json.dumps(status.data)
        active = status.data["active_watches"][0]

        self.assertNotIn("status-error-secret", serialized)
        self.assertNotIn("status-reason-secret", serialized)
        self.assertEqual(active["last_error"], "Authorization: REDACTED")
        self.assertEqual(active["stop_reason"], "Cookie: REDACTED")
        manager.shutdown_all()

    def test_status_supports_multiple_watches_as_structured_records(self) -> None:
        manager = BrowserWatchManager(
            observer=Mock(spec=BrowserObserver),
            page_provider=FakeProvider(),
            start_threads=False,
        )

        manager.start_watch(_profile("one"))
        manager.start_watch(_profile("two"))
        active_status = manager.status()
        manager.stop_watch("one")
        completed_status = manager.status()

        self.assertEqual(active_status.data["active_count"], 2)
        self.assertEqual(
            {watch["profile"] for watch in active_status.data["active_watches"]},
            {"one", "two"},
        )
        self.assertEqual(len(active_status.data["active_watches"]), 2)
        self.assertEqual(
            {watch["watch_id"] for watch in active_status.data["active_watches"]},
            {"one", "two"},
        )
        self.assertEqual(completed_status.data["active_count"], 1)
        self.assertEqual(completed_status.data["completed_count"], 1)
        completed = completed_status.data["completed_watches"][0]
        self.assertEqual(completed["stop_reason"], "explicit_stop")
        self.assertIsNone(completed["last_error"])
        manager.shutdown_all()

    def test_status_sanitizes_current_url(self) -> None:
        manager = BrowserWatchManager(
            observer=Mock(spec=BrowserObserver),
            page_provider=FakeProvider(),
            start_threads=False,
        )
        manager.start_watch(_profile())
        with manager._lock:  # noqa: SLF001 - test verifies status serialization.
            manager._active["text_appeared"].current_url = "https://example.com/?token=secret&view=ok#private"  # noqa: SLF001

        status = manager.status()

        current_url = status.data["active_watches"][0]["current_url"]
        self.assertEqual(current_url, "https://example.com/?token=REDACTED&view=ok")
        manager.shutdown_all()

    def test_events_combines_filters_and_reports_missing_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event_log = Path(tmp) / ".runtime" / "browser_observer" / "events.jsonl"
            event_log.parent.mkdir(parents=True)
            rows = [
                _stored_event("one", "event-1", "text_appeared", "2026-07-23T10:00:00+00:00"),
                _stored_event("two", "event-2", "text_appeared", "2026-07-23T10:01:00+00:00"),
                _stored_event("one", "event-3", "viewport_changed", "2026-07-23T10:02:00+00:00"),
                _stored_event("one", "event-4", "text_appeared", "2026-07-23T10:03:00+00:00"),
            ]
            event_log.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            manager = BrowserWatchManager(project_root=tmp, start_threads=False)

            result = manager.events(
                "observer",
                {
                    "profile": "one",
                    "event_types": ["text_appeared"],
                    "site": "example.com",
                    "after_event_id": "event-1",
                    "limit": 5,
                },
            )
            missing = manager.events("observer", {"after_event_id": "missing"})
            private_cursor = manager.events(
                "observer",
                {"after_event_id": "authorization=manager-cursor-secret"},
            )

        self.assertEqual([event["event_id"] for event in result.data["events"]], ["event-4"])
        self.assertEqual(result.data["returned_count"], 1)
        self.assertEqual(result.data["matched_count"], 1)
        self.assertEqual(result.data["events_count"], 1)
        self.assertEqual(result.data["next_position"], 4)
        self.assertFalse(result.data["truncated"])
        self.assertIsNone(result.details)
        self.assertEqual(missing.status, "invalid_params")
        self.assertEqual(missing.reason_code, "browser_watch_event_cursor_not_found")
        self.assertIn("не знайдено", missing.message)
        self.assertEqual(private_cursor.status, "invalid_params")
        self.assertNotIn("manager-cursor-secret", private_cursor.message)
        self.assertIsNone(private_cursor.data)

    def test_manager_source_does_not_use_forbidden_public_actions(self) -> None:
        source = inspect.getsource(browser_watch_manager)

        for forbidden in ["mouse.click", "keyboard.press", "pyautogui", "webbrowser", "xdg-open", "open_app", "connect_over_cdp"]:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class FakeClock:
    def __init__(self, values: list[float]) -> None:
        self.values = values
        self.index = 0

    def time(self) -> float:
        value = self.values[min(self.index, len(self.values) - 1)]
        self.index += 1
        return value


class FakeProvider:
    def __init__(self, page: object | None = None, result: BrowserRuntimeResult | None = None) -> None:
        self.session = FakeSession(page or FakePage())
        self.result = result

    def open_page(self, start_url: str):
        if self.result is not None:
            return self.result
        self.session.page.url = start_url
        return self.session


class FakeSession:
    def __init__(self, page: object) -> None:
        self.page = page
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakePage:
    def __init__(self, content: str = "") -> None:
        self.url = "https://example.com/"
        self._content = content

    def content(self) -> str:
        return self._content

    def title(self) -> str:
        return ""


def _profile(name: str = "text_appeared", start_url: str = "https://example.com/", timeout_seconds: int | None = 300) -> WatchProfile:
    return WatchProfile(
        name=name,
        mode="text_appeared",
        url_allowlist=["https://example.com/"],
        start_url=start_url,
        text="Example",
        interval_ms=500,
        timeout_seconds=timeout_seconds,
    )


def _event(event_type: str, message: str = "found") -> WatchEvent:
    return WatchEvent(
        watch_id="text_appeared",
        event_type=event_type,
        confidence=1.0,
        message=message,
        timestamp="2026-07-03T00:00:00+00:00",
        region=None,
        bbox=None,
        center=None,
        screenshot_path=None,
        metadata={},
    )


def _stored_event(profile: str, event_id: str, event_type: str, timestamp: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "watch_id": profile,
        "profile": profile,
        "timestamp": timestamp,
        "event_type": event_type,
        "source": "background_watch",
        "message": "found",
        "page": {"url": "https://example.com/"},
        "payload": {},
        "metadata": {},
    }


def _wait_until_completed(manager: BrowserWatchManager) -> None:
    deadline = time.time() + 2.0
    while time.time() < deadline:
        with manager._lock:  # noqa: SLF001 - tests verify worker lifecycle state.
            if manager._completed:  # noqa: SLF001
                return
            threads = [watch.thread for watch in manager._active.values() if watch.thread is not None]  # noqa: SLF001
        for thread in threads:
            thread.join(timeout=0.05)
        time.sleep(0.01)
    raise AssertionError("watch did not complete")


if __name__ == "__main__":
    unittest.main()
