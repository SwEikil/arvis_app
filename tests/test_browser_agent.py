from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    import numpy as np
except Exception:
    np = None  # type: ignore[assignment]

from actions.browser_agent import execute_browser_task
from actions.browser_agent import AimClickRegion
from actions.browser_agent import AimTaskStats
from actions.browser_agent import BROWSER_TASKS
from actions.browser_agent import BrowserBaseline
from actions.browser_agent import BrowserDebug
from actions.browser_agent import BrowserState
from actions.browser_agent import MAX_AIM_ATTEMPTS
from actions.browser_agent import MAX_CONSECUTIVE_UNCONFIRMED
from actions.browser_agent import _attach_popup_guard
from actions.browser_agent import _check_browser_state
from actions.browser_agent import _click_detected_target
from actions.browser_agent import _limit_stop_reason
from actions.browser_agent import _run_humanbenchmark_aim
from actions.browser_agent import _task_details
from actions.browser_agent import find_aim_target_center
from actions.browser_agent import find_aim_target


class BrowserAgentTests(unittest.TestCase):
    def test_missing_dependencies_are_not_configured(self) -> None:
        with patch("actions.browser_agent.importlib.util.find_spec", return_value=None):
            executed, message, details = execute_browser_task("humanbenchmark_aim")

        self.assertFalse(executed)
        self.assertIn("not configured", message)
        self.assertIn("missing_dependencies", details or "")

    @unittest.skipIf(np is None, "numpy is optional for Browser Agent detector tests")
    def test_detector_finds_synthetic_target_center(self) -> None:
        image = _blank_image()
        _draw_circle(image, 70, 45, 16, (40, 120, 240))

        center = find_aim_target_center(image)

        self.assertIsNotNone(center)
        assert center is not None
        self.assertLessEqual(abs(center[0] - 70), 2)
        self.assertLessEqual(abs(center[1] - 45), 2)

    @unittest.skipIf(np is None, "numpy is optional for Browser Agent detector tests")
    def test_detector_returns_none_without_target(self) -> None:
        image = _blank_image()

        self.assertIsNone(find_aim_target_center(image))

    @unittest.skipIf(np is None, "numpy is optional for Browser Agent detector tests")
    def test_detector_chooses_largest_plausible_round_target(self) -> None:
        image = _blank_image(width=180, height=120)
        image[10:14, 10:14] = (240, 20, 20)
        image[80:85, 150:155] = (20, 240, 20)
        _draw_circle(image, 92, 56, 20, (40, 120, 240))

        center = find_aim_target_center(image)

        self.assertIsNotNone(center)
        assert center is not None
        self.assertLessEqual(abs(center[0] - 92), 2)
        self.assertLessEqual(abs(center[1] - 56), 2)

    @unittest.skipIf(np is None, "numpy is optional for Browser Agent detector tests")
    def test_detector_ignores_large_rectangular_banner(self) -> None:
        image = _blank_image(width=220, height=140)
        image[20:70, 20:200] = (40, 120, 240)

        self.assertIsNone(find_aim_target(image))

    @unittest.skipIf(np is None, "numpy is optional for Browser Agent detector tests")
    def test_detector_ignores_cookie_area_outside_allowed_region(self) -> None:
        image = _blank_image(width=220, height=160)
        _draw_circle(image, 110, 135, 16, (40, 120, 240))
        region = AimClickRegion(left=20, top=20, right=200, bottom=100)

        self.assertIsNone(find_aim_target(image, allowed_region=region))

    @unittest.skipIf(np is None, "numpy is optional for Browser Agent detector tests")
    def test_detector_finds_target_inside_allowed_region(self) -> None:
        image = _blank_image(width=220, height=160)
        _draw_circle(image, 110, 75, 16, (40, 120, 240))
        region = AimClickRegion(left=20, top=20, right=200, bottom=120)

        result = find_aim_target(image, allowed_region=region)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertGreaterEqual(result.confidence, 0.72)
        self.assertLessEqual(abs(result.center_x - 110), 2)
        self.assertLessEqual(abs(result.center_y - 75), 2)

    @unittest.skipIf(np is None, "numpy is optional for Browser Agent detector tests")
    def test_detector_returns_none_for_random_colored_noise(self) -> None:
        image = _blank_image(width=220, height=160)
        for index in range(25):
            x = (index * 37) % 220
            y = (index * 19) % 160
            image[y : y + 2, x : x + 2] = (240, 40, 20)

        self.assertIsNone(find_aim_target(image))

    @unittest.skipIf(np is None, "numpy is optional for Browser Agent detector tests")
    def test_missing_target_does_not_click_random_coordinates(self) -> None:
        page = FakePage()
        image = _blank_image(width=220, height=160)

        clicked, target, reason = _click_detected_target(
            page,
            image,
            AimClickRegion(left=20, top=20, right=200, bottom=120),
            BrowserDebug(False),
            1,
        )

        self.assertFalse(clicked)
        self.assertIsNone(target)
        self.assertEqual(reason, "target_not_found")
        self.assertEqual(page.mouse.clicks, [])

    def test_popup_guard_closes_unexpected_pages(self) -> None:
        context = FakeContext()
        main_page = FakePage(url="https://humanbenchmark.com/tests/aim")
        popup = FakePage(url="https://ads.example/")
        state = BrowserState(main_page=main_page)

        _attach_popup_guard(context, main_page, BrowserDebug(False), state)
        context.handlers["page"](popup)

        self.assertTrue(popup.closed)
        self.assertEqual(state.unexpected_page_count, 1)

    def test_extra_page_state_aborts_before_click_loop(self) -> None:
        main_page = FakePage(url="https://humanbenchmark.com/tests/aim")
        extra_page = FakePage(url="https://ads.example/")
        context = FakeContext([main_page, extra_page])
        state = BrowserState(main_page=main_page)

        reason = _check_browser_state(
            context,
            main_page,
            BROWSER_TASKS["humanbenchmark_aim"],
            {},
            BrowserBaseline(1280, 800, 0, 0),
            state,
            BrowserDebug(False),
        )

        self.assertEqual(reason, "browser_state_unstable:extra_page")
        self.assertTrue(extra_page.closed)
        self.assertEqual(main_page.mouse.clicks, [])

    def test_devtools_page_state_aborts(self) -> None:
        main_page = FakePage(url="https://humanbenchmark.com/tests/aim")
        devtools_page = FakePage(url="devtools://devtools/bundled/inspector.html")
        context = FakeContext([main_page, devtools_page])
        state = BrowserState(main_page=main_page)

        reason = _check_browser_state(
            context,
            main_page,
            BROWSER_TASKS["humanbenchmark_aim"],
            {},
            BrowserBaseline(1280, 800, 0, 0),
            state,
            BrowserDebug(False),
        )

        self.assertEqual(reason, "browser_state_unstable:devtools_page")
        self.assertTrue(devtools_page.closed)

    def test_repeated_unexpected_pages_abort(self) -> None:
        main_page = FakePage(url="https://humanbenchmark.com/tests/aim")
        context = FakeContext([main_page])
        state = BrowserState(main_page=main_page, unexpected_page_count=2)

        reason = _check_browser_state(
            context,
            main_page,
            BROWSER_TASKS["humanbenchmark_aim"],
            {},
            BrowserBaseline(1280, 800, 0, 0),
            state,
            BrowserDebug(False),
        )

        self.assertEqual(reason, "browser_state_unstable:repeated_unexpected_page")

    def test_limit_stop_reason_for_consecutive_unconfirmed(self) -> None:
        stats = AimTaskStats(consecutive_unconfirmed=MAX_CONSECUTIVE_UNCONFIRMED)

        self.assertEqual(_limit_stop_reason(stats, 0), "max_consecutive_unconfirmed")

    def test_limit_stop_reason_for_max_attempts(self) -> None:
        stats = AimTaskStats(attempted_clicks=MAX_AIM_ATTEMPTS)

        self.assertEqual(_limit_stop_reason(stats, 0), "max_attempts")

    def test_scroll_changed_restores_once_then_aborts(self) -> None:
        main_page = FakePage(url="https://humanbenchmark.com/tests/aim")
        main_page.scroll = (0, 120)
        context = FakeContext([main_page])
        state = BrowserState(main_page=main_page)
        baseline = BrowserBaseline(1280, 800, 0, 0)

        first_reason = _check_browser_state(
            context,
            main_page,
            BROWSER_TASKS["humanbenchmark_aim"],
            {},
            baseline,
            state,
            BrowserDebug(False),
        )
        main_page.scroll = (0, 120)
        second_reason = _check_browser_state(
            context,
            main_page,
            BROWSER_TASKS["humanbenchmark_aim"],
            {},
            baseline,
            state,
            BrowserDebug(False),
        )

        self.assertEqual(first_reason, "")
        self.assertEqual(second_reason, "user_interference_or_layout_changed")

    def test_task_details_include_stop_reason(self) -> None:
        stats = AimTaskStats(attempted_clicks=60, confirmed_hits=21, stop_reason="max_attempts")

        details = _task_details(BROWSER_TASKS["humanbenchmark_aim"], stats)

        self.assertIn("max_attempts: 60", details)
        self.assertIn("stop_reason: max_attempts", details)

    def test_browser_agent_does_not_use_system_browser_launchers(self) -> None:
        import inspect
        import actions.browser_agent as browser_agent

        source = inspect.getsource(browser_agent)

        self.assertNotIn("xdg-open", source)
        self.assertNotIn("webbrowser", source)
        self.assertNotIn("execute_app_action", source)
        self.assertNotIn("devtools=False", source)
        self.assertNotIn("devtools=True", source)

    def test_chromium_launch_does_not_pass_devtools_keyword(self) -> None:
        fake_sync = FakeSyncPlaywright()

        _run_humanbenchmark_aim(BROWSER_TASKS["humanbenchmark_aim"], fake_sync, TimeoutError)

        self.assertIn("headless", fake_sync.chromium.launch_kwargs)
        self.assertNotIn("devtools", fake_sync.chromium.launch_kwargs)


class FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))


class FakePage:
    def __init__(self, url: str = "") -> None:
        self.url = url
        self.mouse = FakeMouse()
        self.closed = False
        self.handlers: dict[str, object] = {}
        self.viewport_size = {"width": 1280, "height": 800}
        self.scroll = (0, 0)
        self.visibility = "visible"

    def close(self) -> None:
        self.closed = True

    def is_closed(self) -> bool:
        return self.closed

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def evaluate(self, expression: str):
        if "document.visibilityState" in expression:
            return self.visibility
        if "window.innerWidth" in expression:
            return [self.viewport_size["width"], self.viewport_size["height"]]
        if "window.scrollX" in expression:
            return [self.scroll[0], self.scroll[1]]
        if "window.scrollTo" in expression:
            self.scroll = (0, 0)
            return None
        return None

    def bring_to_front(self) -> None:
        self.visibility = "visible"


class FakeContext:
    def __init__(self, pages: list[FakePage] | None = None) -> None:
        self.handlers: dict[str, object] = {}
        self.pages = pages or []

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def route(self, pattern: str, handler) -> None:
        self.handlers[f"route:{pattern}"] = handler

    def new_page(self) -> FakePage:
        page = FakePage(url="https://humanbenchmark.com/tests/aim")
        self.pages.append(page)
        return page


class FakeLocator:
    @property
    def first(self) -> "FakeLocator":
        return self

    def click(self, timeout: int | None = None) -> None:
        return None

    def inner_text(self, timeout: int | None = None) -> str:
        return "Result 123 ms"


class FakePlaywrightPage(FakePage):
    def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None) -> None:
        self.url = url

    def wait_for_timeout(self, timeout: int) -> None:
        return None

    def get_by_role(self, role: str, name=None) -> FakeLocator:
        return FakeLocator()

    def get_by_text(self, pattern) -> FakeLocator:
        return FakeLocator()

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator()


class FakePlaywrightContext(FakeContext):
    def new_page(self) -> FakePlaywrightPage:
        page = FakePlaywrightPage(url="https://humanbenchmark.com/tests/aim")
        self.pages.append(page)
        return page


class FakeBrowser:
    def __init__(self) -> None:
        self.context = FakePlaywrightContext()
        self.closed = False

    def new_context(self, viewport: dict[str, int]):
        return self.context

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self) -> None:
        self.launch_kwargs: dict[str, object] = {}
        self.browser = FakeBrowser()

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium


class FakeSyncPlaywright:
    def __init__(self) -> None:
        self.chromium = FakeChromium()

    def __call__(self):
        return self

    def __enter__(self):
        return FakePlaywright(self.chromium)

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _blank_image(width: int = 140, height: int = 100):
    assert np is not None
    return np.full((height, width, 3), 240, dtype=np.uint8)


def _draw_circle(image, center_x: int, center_y: int, radius: int, color: tuple[int, int, int]) -> None:
    assert np is not None
    yy, xx = np.ogrid[: image.shape[0], : image.shape[1]]
    mask = (xx - center_x) ** 2 + (yy - center_y) ** 2 <= radius**2
    image[mask] = color


if __name__ == "__main__":
    unittest.main()
