from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800
MIN_TARGET_CONFIDENCE = 0.72
AD_TRACKER_PATTERNS = (
    "googleads",
    "doubleclick",
    "googlesyndication",
    "googletagmanager",
    "adservice",
    "adsystem",
    "taboola",
    "outbrain",
    "amazon-adsystem",
    "facebook",
    "analytics",
)


@dataclass(frozen=True)
class BrowserTask:
    key: str
    display_name: str
    url: str
    max_targets: int


@dataclass(frozen=True)
class AimClickRegion:
    left: int
    top: int
    right: int
    bottom: int

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


@dataclass(frozen=True)
class AimTargetResult:
    center_x: int
    center_y: int
    confidence: float
    area: int
    reason: str


@dataclass
class AimTaskStats:
    attempted_clicks: int = 0
    confirmed_hits: int = 0
    detected_targets: int = 0
    missed_or_unconfirmed: int = 0
    elapsed_seconds: float = 0.0
    final_site_result_ms: str = ""
    last_error: str = ""


BROWSER_TASKS = {
    "humanbenchmark_aim": BrowserTask(
        key="humanbenchmark_aim",
        display_name="HumanBenchmark Aim",
        url="https://humanbenchmark.com/tests/aim",
        max_targets=30,
    ),
}

DEFAULT_AIM_CLICK_REGION = AimClickRegion(left=260, top=120, right=1020, bottom=720)


def normalize_browser_task_target(target: str | None) -> str:
    normalized = (target or "").strip().lower().replace("-", " ").replace("_", " ")
    return "_".join(normalized.split())


def preview_browser_task(target: str | None) -> tuple[bool, str, str | None]:
    task = _task_for_target(target)
    if task is None:
        normalized_target = normalize_browser_task_target(target)
        return False, "Browser task target is not in the whitelist.", f"target: {normalized_target}"

    return (
        False,
        f"Dry-run: would run browser task `{task.key}`.",
        _task_details(task, AimTaskStats()),
    )


def execute_browser_task(target: str | None) -> tuple[bool, str, str | None]:
    task = _task_for_target(target)
    if task is None:
        normalized_target = normalize_browser_task_target(target)
        return False, "Browser task target is not in the whitelist.", f"target: {normalized_target}"

    missing = _missing_browser_dependencies()
    if missing:
        return (
            False,
            "Browser Agent is not configured: missing dependencies.",
            f"missing_dependencies: {', '.join(missing)}",
        )

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
        import cv2  # noqa: F401
        import numpy as np  # noqa: F401
    except Exception as exc:
        return (
            False,
            "Browser Agent is not configured: dependency import failed.",
            f"error: {type(exc).__name__}: {exc}",
        )

    if task.key == "humanbenchmark_aim":
        return _run_humanbenchmark_aim(task, sync_playwright, PlaywrightTimeoutError)

    return False, "Browser task is not supported.", f"task: {task.key}"


def find_aim_target_center(image: object) -> tuple[int, int] | None:
    result = find_aim_target(image)
    if result is None:
        return None
    return result.center_x, result.center_y


def find_aim_target(
    image: object,
    allowed_region: AimClickRegion | None = None,
    min_confidence: float = MIN_TARGET_CONFIDENCE,
) -> AimTargetResult | None:
    array = _image_to_rgb_array(image)
    if array is None:
        return None

    try:
        import numpy as np
    except Exception:
        return None

    rgb = array[:, :, :3].astype(np.int16)
    channel_range = rgb.max(axis=2) - rgb.min(axis=2)
    brightness = rgb.max(axis=2)
    darkness = rgb.min(axis=2)
    mask = (channel_range >= 55) & (brightness >= 90) & (darkness <= 210)
    if allowed_region is not None:
        region_mask = np.zeros(mask.shape, dtype=bool)
        height, width = mask.shape
        left = max(0, min(width, allowed_region.left))
        right = max(0, min(width - 1, allowed_region.right))
        top = max(0, min(height, allowed_region.top))
        bottom = max(0, min(height - 1, allowed_region.bottom))
        if right >= left and bottom >= top:
            region_mask[top : bottom + 1, left : right + 1] = True
        mask &= region_mask
    if not bool(mask.any()):
        return None

    component = _best_component(mask, allowed_region)
    if component is None:
        return None
    center_x, center_y, area, confidence, reason = component
    if confidence < min_confidence:
        return None
    return AimTargetResult(
        center_x=int(round(center_x)),
        center_y=int(round(center_y)),
        confidence=confidence,
        area=area,
        reason=reason,
    )


def _task_for_target(target: str | None) -> BrowserTask | None:
    return BROWSER_TASKS.get(normalize_browser_task_target(target))


def _missing_browser_dependencies() -> list[str]:
    missing: list[str] = []
    if importlib.util.find_spec("playwright") is None:
        missing.append("playwright")
    if importlib.util.find_spec("cv2") is None:
        missing.append("opencv-python")
    if importlib.util.find_spec("numpy") is None:
        missing.append("numpy")
    return missing


def _run_humanbenchmark_aim(task: BrowserTask, sync_playwright: Any, playwright_timeout_error: type[Exception]) -> tuple[bool, str, str | None]:
    started = time.monotonic()
    stats = AimTaskStats()
    debug = BrowserDebug(_env_bool("ARVIS_BROWSER_DEBUG_SAVE"))
    browser = None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT})
            _install_ad_blocking(context, debug)
            page = context.new_page()
            _attach_popup_guard(context, page, debug)
            blocked_state: dict[str, str] = {}
            _attach_download_guard(page, blocked_state, debug)

            page.goto(task.url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(500)
            blocked_reason = _blocked_page_reason(page, task, blocked_state)
            if blocked_reason:
                stats.last_error = blocked_reason
                stats.elapsed_seconds = time.monotonic() - started
                return False, "Browser task blocked.", _task_details(task, stats)

            _dismiss_cookie_consent(page, playwright_timeout_error, debug)
            if not _click_start(page, playwright_timeout_error, debug):
                debug.log({"event": "start_not_found"})

            deadline = time.monotonic() + 45.0
            iteration = 0
            while stats.confirmed_hits < task.max_targets and time.monotonic() < deadline:
                iteration += 1
                blocked_reason = _blocked_page_reason(page, task, blocked_state)
                if blocked_reason:
                    stats.last_error = blocked_reason
                    stats.elapsed_seconds = time.monotonic() - started
                    return False, "Browser task blocked.", _task_details(task, stats)

                final_result = _read_final_result_ms(page)
                if final_result:
                    stats.final_site_result_ms = final_result
                    break

                screenshot = page.screenshot(full_page=False)
                debug.save_screenshot(f"aim_{iteration:03d}_before.png", screenshot)
                clicked, target, reason = _click_detected_target(
                    page,
                    screenshot,
                    DEFAULT_AIM_CLICK_REGION,
                    debug,
                    iteration,
                )
                if target is not None:
                    stats.detected_targets += 1
                if not clicked:
                    stats.last_error = reason
                    page.wait_for_timeout(150)
                    continue

                stats.attempted_clicks += 1
                page.wait_for_timeout(110)
                after_screenshot = page.screenshot(full_page=False)
                debug.save_screenshot(f"aim_{iteration:03d}_after.png", after_screenshot)

                final_result = _read_final_result_ms(page)
                after_target = find_aim_target(after_screenshot, allowed_region=DEFAULT_AIM_CLICK_REGION)
                if final_result:
                    stats.final_site_result_ms = final_result
                    stats.confirmed_hits += 1
                    break
                if _target_changed_or_disappeared(target, after_target):
                    stats.confirmed_hits += 1
                    stats.last_error = ""
                    debug.log({"event": "confirmed_hit", "iteration": iteration})
                else:
                    stats.missed_or_unconfirmed += 1
                    stats.last_error = "target_not_confirmed_after_click"
                    debug.log({"event": "unconfirmed_click", "iteration": iteration})

            stats.elapsed_seconds = time.monotonic() - started
            stats.missed_or_unconfirmed = max(stats.missed_or_unconfirmed, stats.attempted_clicks - stats.confirmed_hits)
            return True, "Browser task completed.", _task_details(task, stats)
    except Exception as exc:
        stats.elapsed_seconds = time.monotonic() - started
        stats.last_error = f"{type(exc).__name__}: {exc}"
        return (
            stats.attempted_clicks > 0,
            "Browser task failed." if stats.attempted_clicks == 0 else "Browser task partially completed.",
            _task_details(task, stats),
        )
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def _click_detected_target(
    page: Any,
    screenshot: object,
    allowed_region: AimClickRegion,
    debug: "BrowserDebug | None" = None,
    iteration: int = 0,
) -> tuple[bool, AimTargetResult | None, str]:
    target = find_aim_target(screenshot, allowed_region=allowed_region)
    if target is None:
        if debug is not None:
            debug.log({"event": "no_click", "iteration": iteration, "reason": "target_not_found"})
        return False, None, "target_not_found"
    if not allowed_region.contains(target.center_x, target.center_y):
        if debug is not None:
            debug.log(
                {
                    "event": "no_click",
                    "iteration": iteration,
                    "reason": "target_outside_allowed_region",
                    "center": [target.center_x, target.center_y],
                    "confidence": target.confidence,
                }
            )
        return False, target, "target_outside_allowed_region"

    page.mouse.click(target.center_x, target.center_y)
    if debug is not None:
        debug.log(
            {
                "event": "click",
                "iteration": iteration,
                "center": [target.center_x, target.center_y],
                "confidence": target.confidence,
                "area": target.area,
                "reason": target.reason,
            }
        )
    return True, target, "clicked"


def _click_start(page: Any, playwright_timeout_error: type[Exception], debug: "BrowserDebug") -> bool:
    start_pattern = re.compile(r"^(start|begin|click|start test)$", re.IGNORECASE)
    text_pattern = re.compile(r"click the targets as quickly as you can|start", re.IGNORECASE)
    locators = [
        page.get_by_role("button", name=start_pattern),
        page.get_by_text(text_pattern),
    ]
    for locator in locators:
        try:
            locator.first.click(timeout=2_000)
            page.wait_for_timeout(250)
            debug.log({"event": "start_clicked_dom"})
            return True
        except playwright_timeout_error:
            continue
        except Exception as exc:
            debug.log({"event": "start_click_failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
    return False


def _dismiss_cookie_consent(page: Any, playwright_timeout_error: type[Exception], debug: "BrowserDebug") -> None:
    button_pattern = re.compile(r"reject|decline|necessary|accept|agree|allow all|got it|close|×", re.IGNORECASE)
    locators = [
        page.get_by_role("button", name=button_pattern),
        page.get_by_text(button_pattern),
    ]
    for locator in locators:
        try:
            locator.first.click(timeout=1_200)
            page.wait_for_timeout(200)
            debug.log({"event": "cookie_consent_clicked"})
            return
        except playwright_timeout_error:
            continue
        except Exception as exc:
            debug.log({"event": "cookie_consent_click_failed", "error": f"{type(exc).__name__}: {exc}"})
            continue


def _install_ad_blocking(context: Any, debug: "BrowserDebug") -> None:
    def handler(route: Any, request: Any) -> None:
        url = getattr(request, "url", "")
        if _is_blocked_request_url(url):
            debug.log({"event": "request_blocked", "url": url})
            route.abort()
            return
        route.continue_()

    context.route("**/*", handler)


def _is_blocked_request_url(url: str) -> bool:
    lowered = (url or "").lower()
    return any(pattern in lowered for pattern in AD_TRACKER_PATTERNS)


def _attach_popup_guard(context: Any, main_page: Any, debug: "BrowserDebug") -> None:
    def on_page(page: Any) -> None:
        if page is main_page:
            return
        _close_unexpected_page(page, debug, reason="unexpected_page")

    context.on("page", on_page)
    try:
        main_page.on("popup", lambda page: _close_unexpected_page(page, debug, reason="popup"))
    except Exception:
        pass


def _close_unexpected_page(page: Any, debug: "BrowserDebug", reason: str = "unexpected_page") -> None:
    url = getattr(page, "url", "")
    try:
        page.close()
    finally:
        debug.log({"event": "popup_closed", "reason": reason, "url": url})


def _attach_download_guard(page: Any, blocked_state: dict[str, str], debug: "BrowserDebug") -> None:
    def on_download(download: Any) -> None:
        suggested = getattr(download, "suggested_filename", "")
        blocked_state["reason"] = f"download_prompt:{suggested}"
        debug.log({"event": "download_blocked", "filename": suggested})

    try:
        page.on("download", on_download)
    except Exception:
        pass


def _blocked_page_reason(page: Any, task: BrowserTask, blocked_state: dict[str, str]) -> str:
    if blocked_state.get("reason"):
        return blocked_state["reason"]
    current_url = str(getattr(page, "url", "") or "")
    if current_url and not current_url.startswith(task.url):
        return f"navigated_outside_whitelist:{current_url}"
    lowered_url = current_url.lower()
    if any(token in lowered_url for token in ("captcha", "login", "signin", "checkout", "payment", "download")):
        return f"blocked_url:{current_url}"
    try:
        body = page.locator("body").inner_text(timeout=500).lower()
    except Exception:
        body = ""
    body_block_phrases = (
        "verify you are human",
        "captcha",
        "payment",
        "checkout",
        "download file",
        "sign in to continue",
        "log in to continue",
    )
    for phrase in body_block_phrases:
        if phrase in body:
            return f"blocked_page_text:{phrase}"
    return ""


def _read_final_result_ms(page: Any) -> str:
    try:
        body = page.locator("body").inner_text(timeout=500)
    except Exception:
        return ""
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*ms\b", body, flags=re.IGNORECASE)
    if match:
        return f"{match.group(1)} ms"
    return ""


def _target_changed_or_disappeared(before: AimTargetResult | None, after: AimTargetResult | None) -> bool:
    if before is None:
        return False
    if after is None:
        return True
    distance = math.hypot(before.center_x - after.center_x, before.center_y - after.center_y)
    return distance >= 20


def _task_details(task: BrowserTask, stats: AimTaskStats) -> str:
    details = [
        f"task: {task.key}",
        f"display_name: {task.display_name}",
        f"url: {task.url}",
        f"max_targets: {task.max_targets}",
        f"attempted_clicks: {stats.attempted_clicks}",
        f"confirmed_hits: {stats.confirmed_hits}",
        f"detected_targets: {stats.detected_targets}",
        f"missed_or_unconfirmed: {stats.missed_or_unconfirmed}",
        f"elapsed_seconds: {stats.elapsed_seconds:.2f}",
        f"final_site_result_ms: {stats.final_site_result_ms}",
    ]
    if stats.last_error:
        details.append(f"last_error: {stats.last_error}")
    return "\n".join(details)


def _image_to_rgb_array(image: object) -> Any | None:
    try:
        import numpy as np
    except Exception:
        return None

    if isinstance(image, bytes):
        try:
            import cv2
        except Exception:
            return None
        encoded = np.frombuffer(image, dtype=np.uint8)
        decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if decoded is None:
            return None
        return cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)

    array = np.asarray(image)
    if array.ndim == 2:
        return np.stack([array, array, array], axis=2)
    if array.ndim != 3 or array.shape[2] < 3:
        return None
    return array[:, :, :3]


def _best_component(mask: Any, allowed_region: AimClickRegion | None) -> tuple[float, float, int, float, str] | None:
    cv2_component = _best_component_with_cv2(mask, allowed_region)
    if cv2_component is not None:
        return cv2_component
    return _best_component_without_cv2(mask, allowed_region)


def _best_component_with_cv2(mask: Any, allowed_region: AimClickRegion | None) -> tuple[float, float, int, float, str] | None:
    try:
        import cv2
        import numpy as np
    except Exception:
        return None

    mask_uint8 = np.asarray(mask, dtype=np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask_uint8, connectivity=8)
    best: tuple[float, float, int, float, str] | None = None
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        left = int(stats[label, cv2.CC_STAT_LEFT])
        top = int(stats[label, cv2.CC_STAT_TOP])
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        center_x, center_y = centroids[label]
        candidate = _score_component(area, left, top, width, height, float(center_x), float(center_y), allowed_region)
        if candidate is None:
            continue
        if best is None or candidate[3] > best[3]:
            best = candidate
    return best


def _best_component_without_cv2(mask: Any, allowed_region: AimClickRegion | None) -> tuple[float, float, int, float, str] | None:
    try:
        import numpy as np
    except Exception:
        return None

    mask_bool = np.asarray(mask, dtype=bool)
    height, width = mask_bool.shape
    visited = np.zeros(mask_bool.shape, dtype=bool)
    best: tuple[float, float, int, float, str] | None = None

    for start_y, start_x in np.argwhere(mask_bool):
        y = int(start_y)
        x = int(start_x)
        if visited[y, x]:
            continue

        stack = [(x, y)]
        visited[y, x] = True
        area = 0
        sum_x = 0
        sum_y = 0
        min_x = max_x = x
        min_y = max_y = y

        while stack:
            current_x, current_y = stack.pop()
            area += 1
            sum_x += current_x
            sum_y += current_y
            min_x = min(min_x, current_x)
            max_x = max(max_x, current_x)
            min_y = min(min_y, current_y)
            max_y = max(max_y, current_y)

            for next_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                for next_x in range(max(0, current_x - 1), min(width, current_x + 2)):
                    if visited[next_y, next_x] or not mask_bool[next_y, next_x]:
                        continue
                    visited[next_y, next_x] = True
                    stack.append((next_x, next_y))

        center_x = sum_x / area
        center_y = sum_y / area
        candidate = _score_component(
            area,
            min_x,
            min_y,
            max_x - min_x + 1,
            max_y - min_y + 1,
            center_x,
            center_y,
            allowed_region,
        )
        if candidate is None:
            continue
        if best is None or candidate[3] > best[3]:
            best = candidate

    return best


def _score_component(
    area: int,
    left: int,
    top: int,
    width: int,
    height: int,
    center_x: float,
    center_y: float,
    allowed_region: AimClickRegion | None,
) -> tuple[float, float, int, float, str] | None:
    center_x_int = int(round(center_x))
    center_y_int = int(round(center_y))
    if allowed_region is not None and not allowed_region.contains(center_x_int, center_y_int):
        return None
    if area < 80:
        return None
    if area > 7000 or width > 120 or height > 120:
        return None
    if width < 10 or height < 10:
        return None
    aspect = width / max(1, height)
    if aspect < 0.72 or aspect > 1.38:
        return None
    fill_ratio = area / max(1, width * height)
    if fill_ratio < 0.45 or fill_ratio > 0.88:
        return None
    radius = max(width, height) / 2
    circle_area = math.pi * radius * radius
    roundness = min(area / max(1.0, circle_area), 1.0)
    if roundness < 0.55:
        return None
    area_score = min(area / 850.0, 1.0)
    aspect_score = max(0.0, 1.0 - abs(1.0 - aspect) * 2.0)
    fill_score = max(0.0, 1.0 - abs(0.72 - fill_ratio) * 2.0)
    confidence = max(0.0, min(1.0, 0.35 * roundness + 0.25 * aspect_score + 0.25 * fill_score + 0.15 * area_score))
    reason = f"roundness={roundness:.2f}; aspect={aspect:.2f}; fill={fill_ratio:.2f}"
    return center_x, center_y, area, confidence, reason


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class BrowserDebug:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.root = Path(".runtime") / "browser_debug"
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def save_screenshot(self, name: str, data: object) -> None:
        if not self.enabled or not isinstance(data, bytes):
            return
        try:
            (self.root / name).write_bytes(data)
        except OSError:
            pass

    def log(self, event: dict[str, object]) -> None:
        if not self.enabled:
            return
        try:
            with (self.root / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            pass
