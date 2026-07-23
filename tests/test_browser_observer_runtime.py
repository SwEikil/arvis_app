from __future__ import annotations

import builtins
import inspect
import types
import unittest
from unittest.mock import Mock
from unittest.mock import patch

from actions import browser_observer_runtime
from actions.browser_observer import BrowserObserver
from actions.browser_observer import WatchEvent
from actions.browser_observer import WatchProfile
from actions.browser_observer_runtime import BrowserObserverRuntime
from actions.browser_observer_runtime import BrowserRuntimeResult
from actions.browser_observer_runtime import PlaywrightPageProvider
from actions.browser_observer_runtime import validate_runtime_profile


class BrowserObserverRuntimeTests(unittest.TestCase):
    def test_missing_playwright_returns_not_configured(self) -> None:
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "playwright.sync_api":
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = PlaywrightPageProvider().with_page("https://example.com/", lambda page: None)

        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.reason_code, "playwright_missing")

    def test_profile_start_url_must_match_allowlist(self) -> None:
        profile = WatchProfile(
            name="watch",
            mode="text_appeared",
            url_allowlist=["https://example.com/"],
            start_url="https://evil.example/",
            text="Example",
        )

        result = validate_runtime_profile(profile)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason_code, "start_url_not_allowed")

    def test_missing_start_url_is_not_configured_without_allowlist_fallback(self) -> None:
        profile = WatchProfile(
            name="watch",
            mode="text_appeared",
            url_allowlist=["https://example.com/"],
            text="Example",
        )

        result = validate_runtime_profile(profile)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.reason_code, "profile_start_url_missing")

    def test_runtime_calls_observer_poll_once_with_provider_page(self) -> None:
        event = _event("text_appeared")
        observer = Mock(spec=BrowserObserver)
        observer.poll_once.return_value = event
        provider = FakeProvider(page=object())
        profile = _profile()

        result = BrowserObserverRuntime(observer=observer, page_provider=provider).poll_once(profile)

        self.assertEqual(result.status, "event")
        self.assertEqual(event.source, "poll_once")
        observer.poll_once.assert_called_once_with(profile, provider.page)
        observer.write_event.assert_called_once_with(event)

    def test_runtime_returns_no_event_without_writing(self) -> None:
        observer = Mock(spec=BrowserObserver)
        observer.poll_once.return_value = None

        result = BrowserObserverRuntime(observer=observer, page_provider=FakeProvider(page=object())).poll_once(_profile())

        self.assertEqual(result.status, "no_event")
        observer.write_event.assert_not_called()

    def test_provider_closes_browser_context_on_success(self) -> None:
        fake_sync = FakeSyncPlaywright()

        with _fake_playwright_import(fake_sync):
            result = PlaywrightPageProvider().with_page("https://example.com/", lambda page: _event("text_appeared"))

        self.assertEqual(result.status, "event")
        self.assertTrue(fake_sync.browser.context.closed)
        self.assertTrue(fake_sync.browser.closed)
        self.assertTrue(fake_sync.manager.exited)

    def test_provider_closes_browser_context_on_exception(self) -> None:
        fake_sync = FakeSyncPlaywright()

        def explode(page):
            raise RuntimeError("observer failed")

        with _fake_playwright_import(fake_sync):
            result = PlaywrightPageProvider().with_page("https://example.com/", explode)

        self.assertEqual(result.status, "error")
        self.assertTrue(fake_sync.browser.context.closed)
        self.assertTrue(fake_sync.browser.closed)
        self.assertTrue(fake_sync.manager.exited)

    def test_runtime_source_does_not_use_forbidden_public_actions(self) -> None:
        source = inspect.getsource(browser_observer_runtime)

        for forbidden in ["mouse.click", "keyboard.press", "pyautogui", "webbrowser", "xdg-open", "open_app", "connect_over_cdp"]:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class FakeProvider:
    def __init__(self, page: object) -> None:
        self.page = page

    def with_page(self, start_url: str, callback):
        event = callback(self.page)
        if event is None:
            return BrowserRuntimeResult(status="no_event", reason_code=None, message="no event")
        return BrowserRuntimeResult(status="event", reason_code=None, message="event", event=event)


class FakePage:
    def __init__(self) -> None:
        self.url = ""

    def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> None:
        self.url = url


class FakeContext:
    def __init__(self) -> None:
        self.closed = False
        self.page = FakePage()

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.closed = False
        self.context = FakeContext()

    def new_context(self, viewport: dict[str, int]) -> FakeContext:
        return self.context

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.launch_kwargs: dict[str, object] = {}

    def launch(self, **kwargs) -> FakeBrowser:
        self.launch_kwargs = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = FakeChromium(browser)


class FakeManager:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright
        self.exited = False

    def __enter__(self) -> FakePlaywright:
        return self.playwright

    def __exit__(self, exc_type, exc, tb) -> None:
        self.exited = True


class FakeSyncPlaywright:
    def __init__(self) -> None:
        self.browser = FakeBrowser()
        self.playwright = FakePlaywright(self.browser)
        self.manager = FakeManager(self.playwright)

    def __call__(self) -> FakeManager:
        return self.manager


def _fake_playwright_import(fake_sync: FakeSyncPlaywright):
    real_import = builtins.__import__
    module = types.SimpleNamespace(sync_playwright=fake_sync)

    def fake_import(name, *args, **kwargs):
        if name == "playwright.sync_api":
            return module
        return real_import(name, *args, **kwargs)

    return patch("builtins.__import__", side_effect=fake_import)


def _profile() -> WatchProfile:
    return WatchProfile(
        name="watch",
        mode="text_appeared",
        url_allowlist=["https://example.com/"],
        start_url="https://example.com/",
        text="Example",
    )


def _event(event_type: str) -> WatchEvent:
    return WatchEvent(
        watch_id="watch",
        event_type=event_type,
        confidence=1.0,
        message="found",
        timestamp="2026-07-03T00:00:00+00:00",
        region=None,
        bbox=None,
        center=None,
        screenshot_path=None,
        metadata={},
    )


if __name__ == "__main__":
    unittest.main()
