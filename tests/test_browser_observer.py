from __future__ import annotations

import builtins
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from actions import browser_observer
from actions.browser_observer import BrowserObserver
from actions.browser_observer import WatchProfile
from actions.browser_observer import resolve_viewport_region


class FakeLocator:
    def __init__(self, count: int = 0, text: str = "") -> None:
        self._count = count
        self._text = text

    def count(self) -> int:
        return self._count

    def inner_text(self) -> str:
        return self._text


class FakePage:
    def __init__(
        self,
        url: str = "https://example.com/",
        content: str = "",
        locator_count: int = 0,
        screenshots: list[bytes] | None = None,
    ) -> None:
        self.url = url
        self._content = content
        self._locator_count = locator_count
        self._screenshots = list(screenshots or [b"frame"])
        self.viewport_size = {"width": 1280, "height": 800}
        self.screenshot_calls: list[dict] = []

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(count=self._locator_count, text=self._content)

    def content(self) -> str:
        return self._content

    def screenshot(self, **kwargs) -> bytes:
        self.screenshot_calls.append(kwargs)
        if len(self._screenshots) > 1:
            return self._screenshots.pop(0)
        return self._screenshots[0]


class BrowserObserverTests(unittest.TestCase):
    def test_watch_profile_loads_valid_profile(self) -> None:
        observer = BrowserObserver()

        profile = observer.load_profile("examples/watch_profiles/viewport_change_full.json")

        self.assertEqual(profile.name, "viewport_change_full")
        self.assertEqual(profile.mode, "viewport_change")
        self.assertEqual(profile.region, {"type": "full"})

    def test_url_allowlist_blocks_wrong_url_without_screenshot(self) -> None:
        profile = WatchProfile(name="watch", mode="viewport_change", url_allowlist=["https://example.com/"])
        page = FakePage(url="https://evil.example/", screenshots=[b"one", b"two"])

        event = BrowserObserver().poll_once(profile, page)

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "observer_blocked")
        self.assertEqual(page.screenshot_calls, [])

    def test_full_viewport_region_resolves_to_full_viewport(self) -> None:
        self.assertEqual(resolve_viewport_region(1280, 800, {"type": "full"}), {"x": 0, "y": 0, "width": 1280, "height": 800})

    def test_top_right_percent_region_resolves_for_common_viewports(self) -> None:
        region = {
            "anchor": "top_right",
            "width_percent": 25,
            "height_percent": 25,
            "offset_percent_x": 0,
            "offset_percent_y": 0,
        }

        self.assertEqual(resolve_viewport_region(1280, 800, region), {"x": 960, "y": 0, "width": 320, "height": 200})
        self.assertEqual(resolve_viewport_region(1920, 1080, region), {"x": 1440, "y": 0, "width": 480, "height": 270})

    def test_dom_selector_emits_event_with_mocked_page(self) -> None:
        profile = WatchProfile(
            name="selector_watch",
            mode="dom_selector",
            url_allowlist=["https://example.com/"],
            selector="#ready",
        )

        event = BrowserObserver().poll_once(profile, FakePage(locator_count=2))

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "dom_selector_appeared")
        self.assertEqual(event.metadata["count"], 2)

    def test_text_appeared_emits_event_with_mocked_page(self) -> None:
        profile = WatchProfile(
            name="text_watch",
            mode="text_appeared",
            url_allowlist=["https://example.com/"],
            text="Example Domain",
        )

        event = BrowserObserver().poll_once(profile, FakePage(content="<h1>Example Domain</h1>"))

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "text_appeared")

    def test_viewport_change_emits_event_when_image_changes(self) -> None:
        profile = WatchProfile(
            name="viewport_watch",
            mode="viewport_change",
            url_allowlist=["https://example.com/"],
            threshold=0.1,
        )
        observer = BrowserObserver()
        page = FakePage(screenshots=[b"aaaaaaaaaa", b"bbbbbbbbbb"])

        self.assertIsNone(observer.poll_once(profile, page))
        event = observer.poll_once(profile, page)

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "viewport_changed")

    def test_viewport_change_emits_no_event_when_image_same(self) -> None:
        profile = WatchProfile(
            name="viewport_watch",
            mode="viewport_change",
            url_allowlist=["https://example.com/"],
            threshold=0.1,
        )
        observer = BrowserObserver()
        page = FakePage(screenshots=[b"aaaaaaaaaa", b"aaaaaaaaaa"])

        self.assertIsNone(observer.poll_once(profile, page))
        self.assertIsNone(observer.poll_once(profile, page))

    def test_event_log_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            observer = BrowserObserver(project_root=tmp)
            profile = WatchProfile(name="text_watch", mode="text_appeared", url_allowlist=["https://example.com/"], text="ok")
            event = observer.poll_once(profile, FakePage(content="ok"))
            self.assertIsNotNone(event)

            observer.write_event(event)

            rows = (Path(tmp) / ".runtime" / "browser_observer" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])["event_type"], "text_appeared")

    def test_observer_source_does_not_use_public_forbidden_actions(self) -> None:
        source = inspect.getsource(browser_observer)

        for forbidden in ["mouse.click", "keyboard.press", "pyautogui", "xdg-open", "webbrowser.open", "open_app", ".submit("]:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_template_match_missing_deps_returns_not_configured_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template_dir = Path(tmp) / "examples" / "watch_profiles"
            template_dir.mkdir(parents=True)
            (template_dir / "template.png").write_bytes(b"not really an image")
            profile = WatchProfile(
                name="template_watch",
                mode="template_match",
                url_allowlist=["https://example.com/"],
                template_path="examples/watch_profiles/template.png",
            )

            real_import = builtins.__import__

            def fake_import(name, *args, **kwargs):
                if name in {"cv2", "numpy"}:
                    raise ImportError(name)
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                event = BrowserObserver(project_root=tmp).poll_once(profile, FakePage())

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "observer_not_configured")
        self.assertEqual(event.metadata["reason_code"], "template_dependencies_missing")


if __name__ == "__main__":
    unittest.main()
