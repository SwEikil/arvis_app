from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    import numpy as np
except Exception:
    np = None  # type: ignore[assignment]

from actions.browser_agent import execute_browser_task
from actions.browser_agent import AimClickRegion
from actions.browser_agent import BrowserDebug
from actions.browser_agent import _attach_popup_guard
from actions.browser_agent import _click_detected_target
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

        _attach_popup_guard(context, main_page, BrowserDebug(False))
        context.handlers["page"](popup)

        self.assertTrue(popup.closed)

    def test_browser_agent_does_not_use_system_browser_launchers(self) -> None:
        import inspect
        import actions.browser_agent as browser_agent

        source = inspect.getsource(browser_agent)

        self.assertNotIn("xdg-open", source)
        self.assertNotIn("webbrowser", source)
        self.assertNotIn("execute_app_action", source)


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

    def close(self) -> None:
        self.closed = True

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler


class FakeContext:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler


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
