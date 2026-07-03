from __future__ import annotations

import inspect
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
        manager = BrowserWatchManager(
            observer=Mock(spec=BrowserObserver),
            page_provider=FakeProvider(),
            start_threads=False,
        )

        result = manager.start_watch(_profile())
        status = manager.status()

        self.assertEqual(result.status, "started")
        self.assertIn("active_count: 1", status.details or "")
        manager.shutdown_all()

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

        self.assertIn("status=completed", status.details or "")
        self.assertIn("last_error=timeout", status.details or "")

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

        self.assertIn("events_count=1", status.details or "")
        self.assertIn("suppressed_duplicates=1", status.details or "")

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

        self.assertIn("events_count=1", status.details or "")
        self.assertIn("suppressed_rate_limited=1", status.details or "")

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

        self.assertIn("status=blocked", status.details or "")
        self.assertIn("blocked_page_signal:captcha", status.details or "")
        self.assertIn("block_type=page_signal", status.details or "")
        self.assertIn("signal=captcha", status.details or "")

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
        self.assertNotIn("blocked_page_signal:permission", status.details or "")
        self.assertNotIn("status=blocked", status.details or "")

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
            self.assertNotIn("blocked_page_signal:permission", status.details or "")

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
                self.assertIn(f"blocked_page_signal:{signal}", status.details or "")
                self.assertIn(f"signal={signal}", status.details or "")

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
                self.assertIn(f"blocked_page_signal:{signal}", status.details or "")

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
        self.assertIn("active_count: 1", status.details or "")
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

        self.assertIn("last_error=poll_once RuntimeError: page closed", status.details or "")
        self.assertNotIn("last_error=error", status.details or "")

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
