from __future__ import annotations

import builtins
from datetime import datetime
from datetime import timezone
import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from actions import browser_observer
from actions.browser_observer import BrowserObserver
from actions.browser_observer import WatchProfile
from actions.browser_observer import resolve_viewport_region
from actions.browser_observer_log import read_event_log


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
        self.assertEqual(profile.start_url, "https://example.com/")
        self.assertEqual(profile.region, {"type": "full"})

    def test_text_appeared_profile_uses_canonical_name(self) -> None:
        observer = BrowserObserver()

        profile = observer.load_profile("examples/watch_profiles/text_appeared.json")
        event = observer.poll_once(profile, FakePage(content="<h1>Example Domain</h1>"))

        self.assertEqual(profile.name, "text_appeared")
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.watch_id, "text_appeared")

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
            event.timestamp = "2026-07-30T12:00:00+03:00"

            observer.write_event(event)

            rows = (Path(tmp) / ".runtime" / "browser_observer" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0])
            self.assertEqual(payload["schema_version"], 1)
            UUID(payload["event_id"])
            self.assertEqual(payload["profile"], "text_watch")
            self.assertEqual(payload["source"], "browser_observer")
            self.assertEqual(payload["event_type"], "text_appeared")
            self.assertEqual(payload["timestamp"], "2026-07-30T09:00:00+00:00")
            self.assertEqual(payload["page"], {"url": "https://example.com/"})
            self.assertEqual(payload["payload"], {"mode": "text_appeared", "text": "ok"})
            self.assertEqual(
                set(payload),
                {
                    "schema_version",
                    "event_id",
                    "watch_id",
                    "timestamp",
                    "event_type",
                    "source",
                    "profile",
                    "payload",
                    "page",
                    "message",
                    "confidence",
                },
            )

    def test_event_log_sanitizes_url_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            observer = BrowserObserver(project_root=tmp)
            profile = WatchProfile(
                name="text_watch",
                mode="text_appeared",
                url_allowlist=["https://example.com/"],
                text="ok",
            )
            event = observer.poll_once(
                profile,
                FakePage(url="https://example.com/?ToKeN=secret&view=ok#private", content="ok"),
            )
            self.assertIsNotNone(event)

            observer.write_event(event)

            payload = json.loads(observer.events_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["page"]["url"], "https://example.com/?ToKeN=REDACTED&view=ok")
        self.assertNotIn("secret", json.dumps(payload))
        self.assertNotIn("private", json.dumps(payload))

    def test_writer_recursively_sanitizes_jsonl_and_reader_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            observer = BrowserObserver(project_root=tmp)
            profile = WatchProfile(
                name="text_watch",
                mode="text_appeared",
                url_allowlist=["https://example.com/"],
                text="ok",
            )
            event = observer.poll_once(
                profile,
                FakePage(
                    url="https://alice:url-password@example.com/page?token=url-token&view=ok#private",
                    content="ok",
                ),
            )
            self.assertIsNotNone(event)
            event.payload = {
                "nested": {"apiKey": "payload-key", "safe": "keep"},
                "headers": {
                    "Authorization": "Bearer header-token",
                    "Cookie": "session=cookie-value",
                    "Content-Type": "application/json",
                },
            }
            event.metadata = {
                "sequence": {"authToken": "metadata-token", "safe": 7},
                "mode": "text_appeared",
            }
            event.message = "Authorization: Bearer message-token"
            event.page_title = "Cookie: session=title-cookie"
            event.region = {"x": 1, "y": 2, "width": 3, "height": 4}
            event.bbox = {"x": 5, "y": 6, "width": 7, "height": 8}
            event.center = {"x": 9, "y": 10}
            event.screenshot_path = ".runtime/browser_observer/screenshots/text_watch/frame.png"

            observer.write_event(event)

            raw = observer.events_path.read_text(encoding="utf-8")
            stored = json.loads(raw)
            returned = read_event_log(observer.events_path).events[0]

        for secret in (
            "alice",
            "url-password",
            "url-token",
            "payload-key",
            "header-token",
            "cookie-value",
            "metadata-token",
            "message-token",
            "title-cookie",
            "#private",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, raw)
                self.assertNotIn(secret, json.dumps(returned))
        self.assertEqual(stored["page"]["url"], "https://example.com/page?token=REDACTED&view=ok")
        self.assertEqual(stored["page"]["title"], "Cookie: REDACTED")
        self.assertEqual(stored["message"], "Authorization: REDACTED")
        self.assertEqual(returned["page"]["title"], "Cookie: REDACTED")
        self.assertEqual(returned["message"], "Authorization: REDACTED")
        self.assertEqual(stored["payload"]["nested"]["safe"], "keep")
        self.assertEqual(stored["payload"]["headers"]["Content-Type"], "application/json")
        self.assertEqual(stored["payload"]["region"], {"height": 4, "width": 3, "x": 1, "y": 2})
        self.assertEqual(stored["payload"]["bbox"], {"height": 8, "width": 7, "x": 5, "y": 6})
        self.assertEqual(stored["payload"]["center"], {"x": 9, "y": 10})
        self.assertEqual(
            stored["payload"]["screenshot_path"],
            ".runtime/browser_observer/screenshots/text_watch/frame.png",
        )
        self.assertEqual(stored["metadata"]["sequence"]["safe"], 7)

    def test_events_receive_unique_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            observer = BrowserObserver(project_root=tmp)
            profile = WatchProfile(
                name="text_watch",
                mode="text_appeared",
                url_allowlist=["https://example.com/"],
                text="ok",
            )

            first = observer.poll_once(profile, FakePage(content="ok"))
            second = observer.poll_once(profile, FakePage(content="ok"))
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            observer.write_event(first)
            observer.write_event(second)
            rows = [
                json.loads(line)
                for line in observer.events_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertNotEqual(first.event_id, second.event_id)
        self.assertNotEqual(rows[0]["event_id"], rows[1]["event_id"])

    def test_event_timestamp_is_utc_iso_8601(self) -> None:
        observer = BrowserObserver()
        profile = WatchProfile(
            name="text_watch",
            mode="text_appeared",
            url_allowlist=["https://example.com/"],
            text="ok",
        )

        event = observer.poll_once(profile, FakePage(content="ok"))

        self.assertIsNotNone(event)
        timestamp = datetime.fromisoformat(event.timestamp)
        self.assertEqual(timestamp.utcoffset(), timezone.utc.utcoffset(timestamp))

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
