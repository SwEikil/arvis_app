from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from actions.browser_observer_log import EVENT_SCHEMA_VERSION
from actions.browser_observer_log import EventQuery
from actions.browser_observer_log import count_valid_events
from actions.browser_observer_log import event_to_v1_record
from actions.browser_observer_log import normalize_event_record
from actions.browser_observer_log import read_event_log
from actions.browser_observer_log import sanitize_event_data
from actions.browser_observer_log import sanitize_text
from actions.browser_observer_log import sanitize_url


WATCH_MODES = {"dom_selector", "text_appeared", "viewport_change", "template_match"}
WATCH_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
RUNTIME_DIR = Path(".runtime") / "browser_observer"
EXAMPLE_PROFILE_DIR = Path("examples") / "watch_profiles"
RUNTIME_PROFILE_DIR = RUNTIME_DIR / "watch_profiles"
RUNTIME_TEMPLATE_DIR = RUNTIME_DIR / "templates"
EVENT_LOG_PATH = RUNTIME_DIR / "events.jsonl"
SAFETY_NOTICE = "Я тільки спостерігаю, сер. Generic кліки/натискання в public модулі не виконуються."


@dataclass
class WatchProfile:
    name: str
    mode: str
    url_allowlist: list[str]
    start_url: str | None = None
    interval_ms: int = 500
    timeout_seconds: int | None = None
    area: str = "visible_viewport"
    region: dict | None = None
    selector: str | None = None
    text: str | None = None
    template_path: str | None = None
    threshold: float | None = None
    debug_save: bool = False
    notify: bool = True


@dataclass
class WatchEvent:
    watch_id: str
    event_type: str
    confidence: float | None
    message: str
    timestamp: str
    region: dict | None
    bbox: dict | None
    center: dict | None
    screenshot_path: str | None
    metadata: dict
    schema_version: int = EVENT_SCHEMA_VERSION
    event_id: str = field(default_factory=lambda: str(uuid4()))
    profile: str = ""
    source: str = "browser_observer"
    page_url: str | None = None
    page_title: str | None = None
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ObserverValidationResult:
    ok: bool
    status: str
    reason_code: str | None
    message: str


class BrowserObserver:
    def __init__(self, project_root: Path | str | None = None, runtime_dir: Path | str | None = None) -> None:
        self.project_root = Path(project_root or ".").resolve()
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None else self.project_root / RUNTIME_DIR
        self.events_path = self.runtime_dir / "events.jsonl"
        self.screenshots_dir = self.runtime_dir / "screenshots"
        self.state_dir = self.runtime_dir / "state"
        self._previous_frames: dict[str, bytes] = {}

    def poll_once(self, profile: WatchProfile, page: object) -> WatchEvent | None:
        current_url = ""
        page_title = ""
        try:
            current_url = _page_url(page)
            page_title = _page_title(page)
            validation = self.validate_profile(profile, current_url)
            if validation is not None:
                return self._with_page_context(
                    self._event(
                        profile,
                        "observer_blocked" if validation.status == "blocked" else "observer_not_configured",
                        validation.message,
                        confidence=None,
                        metadata={"reason_code": validation.reason_code, "url": current_url, "mode": profile.mode},
                    ),
                    current_url,
                    page_title,
                )

            if profile.mode == "dom_selector":
                return self._with_page_context(self._poll_dom_selector(profile, page, current_url), current_url, page_title)
            if profile.mode == "text_appeared":
                return self._with_page_context(self._poll_text_appeared(profile, page, current_url), current_url, page_title)
            if profile.mode == "viewport_change":
                return self._with_page_context(self._poll_viewport_change(profile, page, current_url), current_url, page_title)
            if profile.mode == "template_match":
                return self._with_page_context(self._poll_template_match(profile, page, current_url), current_url, page_title)
            return self._with_page_context(
                self._event(
                    profile,
                    "observer_not_configured",
                    f"Unsupported watch mode: {profile.mode}",
                    confidence=None,
                    metadata={"reason_code": "unsupported_watch_mode", "mode": profile.mode},
                ),
                current_url,
                page_title,
            )
        except Exception as exc:
            return self._with_page_context(
                self._event(
                    profile,
                    "observer_error",
                    f"Browser observer error: {type(exc).__name__}",
                    confidence=None,
                    metadata={"error": str(exc), "error_type": type(exc).__name__, "mode": profile.mode},
                ),
                current_url,
                page_title,
            )

    def write_event(self, event: WatchEvent) -> None:
        record = event_to_v1_record(asdict(event), self.project_root)
        normalized = normalize_event_record(record, 0)
        if normalized is None:
            raise ValueError("Browser Observer event does not satisfy schema version 1.")
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(normalized, ensure_ascii=False, sort_keys=True) + "\n")

    def load_profile(self, path: str | Path) -> WatchProfile:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return WatchProfile(
            name=str(data["name"]),
            mode=str(data["mode"]),
            url_allowlist=list(data.get("url_allowlist") or []),
            start_url=data.get("start_url"),
            interval_ms=int(data.get("interval_ms", 500)),
            timeout_seconds=data.get("timeout_seconds"),
            area=str(data.get("area", "visible_viewport")),
            region=data.get("region"),
            selector=data.get("selector"),
            text=data.get("text"),
            template_path=data.get("template_path"),
            threshold=data.get("threshold"),
            debug_save=bool(data.get("debug_save", False)),
            notify=bool(data.get("notify", True)),
        )

    def validate_profile(self, profile: WatchProfile, current_url: str) -> ObserverValidationResult | None:
        if profile.mode not in WATCH_MODES:
            return ObserverValidationResult(False, "not_configured", "unsupported_watch_mode", "Watch mode is not supported.")
        if profile.area != "visible_viewport":
            return ObserverValidationResult(False, "not_configured", "unsupported_area", "Only visible browser viewport observation is supported.")
        if not profile.url_allowlist or not _url_allowed(current_url, profile.url_allowlist):
            return ObserverValidationResult(False, "blocked", "url_not_allowed", "Observer blocked: current URL is not in the profile allowlist.")
        if profile.mode == "dom_selector" and not profile.selector:
            return ObserverValidationResult(False, "not_configured", "selector_missing", "DOM selector watch profile is missing selector.")
        if profile.mode == "text_appeared" and not profile.text:
            return ObserverValidationResult(False, "not_configured", "text_missing", "Text watch profile is missing text.")
        if profile.mode == "template_match":
            template_result = self._validate_template_path(profile.template_path)
            if template_result is not None:
                return template_result
        return None

    def _poll_dom_selector(self, profile: WatchProfile, page: object, current_url: str) -> WatchEvent | None:
        locator = page.locator(profile.selector)
        count = int(locator.count())
        if count <= 0:
            return None
        return self._event(
            profile,
            "dom_selector_appeared",
            f"DOM selector appeared: {profile.selector}",
            confidence=1.0,
            metadata={"selector": profile.selector, "count": count, "url": current_url, "mode": profile.mode},
        )

    def _poll_text_appeared(self, profile: WatchProfile, page: object, current_url: str) -> WatchEvent | None:
        content = _page_text(page)
        if profile.text not in content:
            return None
        return self._event(
            profile,
            "text_appeared",
            f"Text appeared: {profile.text}",
            confidence=1.0,
            metadata={"text": profile.text, "url": current_url, "mode": profile.mode},
        )

    def _poll_viewport_change(self, profile: WatchProfile, page: object, current_url: str) -> WatchEvent | None:
        width, height = _viewport_size(page)
        region = profile.region or {"type": "full"}
        clip = resolve_viewport_region(width, height, region)
        screenshot = page.screenshot(clip=clip)
        if not isinstance(screenshot, (bytes, bytearray)):
            return self._event(
                profile,
                "observer_error",
                "Browser viewport screenshot did not return bytes.",
                confidence=None,
                region=clip,
                metadata={"reason_code": "invalid_screenshot", "url": current_url, "mode": profile.mode},
            )

        watch_key = profile.name
        previous = self._previous_frames.get(watch_key)
        self._previous_frames[watch_key] = bytes(screenshot)
        if previous is None:
            return None

        ratio = _change_ratio(previous, bytes(screenshot))
        threshold = float(profile.threshold if profile.threshold is not None else 0.01)
        if ratio < threshold:
            return None

        screenshot_path = self._save_debug_screenshot(profile, bytes(screenshot)) if profile.debug_save else None
        return self._event(
            profile,
            "viewport_changed",
            "Visible browser viewport changed.",
            confidence=min(1.0, ratio / max(threshold, 0.000001)),
            region=clip,
            screenshot_path=screenshot_path,
            metadata={"change_ratio": ratio, "threshold": threshold, "url": current_url, "mode": profile.mode},
        )

    def _poll_template_match(self, profile: WatchProfile, page: object, current_url: str) -> WatchEvent | None:
        try:
            import cv2  # noqa: F401
            import numpy as np  # noqa: F401
        except Exception as exc:
            return self._event(
                profile,
                "observer_not_configured",
                "Template matching needs optional OpenCV/NumPy dependencies.",
                confidence=None,
                metadata={"reason_code": "template_dependencies_missing", "error": type(exc).__name__, "url": current_url},
            )
        return None

    def _validate_template_path(self, template_path: str | None) -> ObserverValidationResult | None:
        if not template_path:
            return ObserverValidationResult(False, "not_configured", "template_path_missing", "Template watch profile is missing template_path.")
        candidate = (self.project_root / template_path).resolve()
        allowed_roots = [
            (self.project_root / EXAMPLE_PROFILE_DIR).resolve(),
            (self.project_root / RUNTIME_TEMPLATE_DIR).resolve(),
        ]
        if not any(_is_relative_to(candidate, root) for root in allowed_roots):
            return ObserverValidationResult(False, "not_configured", "template_path_not_allowed", "Template path is outside allowed profile/template directories.")
        if not candidate.exists() or not candidate.is_file():
            return ObserverValidationResult(False, "not_configured", "template_not_found", "Template file was not found.")
        return None

    def _save_debug_screenshot(self, profile: WatchProfile, screenshot: bytes) -> str:
        directory = self.screenshots_dir / profile.name
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{_timestamp().replace(':', '').replace('.', '_')}.png"
        path = directory / filename
        path.write_bytes(screenshot)
        try:
            return str(path.resolve().relative_to(self.project_root))
        except ValueError:
            return ""

    def _event(
        self,
        profile: WatchProfile,
        event_type: str,
        message: str,
        confidence: float | None,
        region: dict | None = None,
        bbox: dict | None = None,
        center: dict | None = None,
        screenshot_path: str | None = None,
        metadata: dict | None = None,
    ) -> WatchEvent:
        return WatchEvent(
            watch_id=profile.name,
            event_type=event_type,
            confidence=confidence,
            message=message,
            timestamp=_timestamp(),
            region=region,
            bbox=bbox,
            center=center,
            screenshot_path=screenshot_path,
            metadata=metadata or {},
            profile=profile.name,
        )

    @staticmethod
    def _with_page_context(event: WatchEvent | None, page_url: str, page_title: str) -> WatchEvent | None:
        if event is None:
            return None
        event.page_url = page_url or None
        event.page_title = page_title or None
        return event


def resolve_viewport_region(viewport_width: int, viewport_height: int, region: dict | None) -> dict:
    if not region or region.get("type") == "full":
        return {"x": 0, "y": 0, "width": int(viewport_width), "height": int(viewport_height)}

    anchor = str(region.get("anchor", "top_left"))
    if anchor not in {"top_left", "top_right", "bottom_left", "bottom_right", "center"}:
        raise ValueError(f"Unsupported viewport region anchor: {anchor}")

    width = _percent_to_pixels(viewport_width, region.get("width_percent", 100))
    height = _percent_to_pixels(viewport_height, region.get("height_percent", 100))
    offset_x = round(viewport_width * float(region.get("offset_percent_x", 0)) / 100)
    offset_y = round(viewport_height * float(region.get("offset_percent_y", 0)) / 100)

    if anchor == "top_left":
        x, y = 0, 0
    elif anchor == "top_right":
        x, y = viewport_width - width, 0
    elif anchor == "bottom_left":
        x, y = 0, viewport_height - height
    elif anchor == "bottom_right":
        x, y = viewport_width - width, viewport_height - height
    else:
        x, y = round((viewport_width - width) / 2), round((viewport_height - height) / 2)

    return {
        "x": _clamp(x + offset_x, 0, viewport_width - 1),
        "y": _clamp(y + offset_y, 0, viewport_height - 1),
        "width": max(1, min(width, viewport_width - _clamp(x + offset_x, 0, viewport_width - 1))),
        "height": max(1, min(height, viewport_height - _clamp(y + offset_y, 0, viewport_height - 1))),
    }


def normalize_browser_watch_profile_name(target: str | None) -> str:
    normalized = (target or "").strip().replace("\\", "/").split("/")[-1]
    if normalized.endswith(".json"):
        normalized = normalized[:-5]
    normalized = normalized.strip().lower().replace(" ", "_")
    return normalized


def list_watch_profiles(project_root: Path | str | None = None) -> list[str]:
    root = Path(project_root or ".").resolve()
    names: set[str] = set()
    for directory in (root / EXAMPLE_PROFILE_DIR, root / RUNTIME_PROFILE_DIR):
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            names.add(path.stem)
    return sorted(names)


def load_watch_profile(target: str | None, project_root: Path | str | None = None) -> WatchProfile | None:
    root = Path(project_root or ".").resolve()
    name = normalize_browser_watch_profile_name(target)
    if not name or not WATCH_PROFILE_NAME_RE.fullmatch(name):
        return None
    observer = BrowserObserver(project_root=root)
    for directory in (root / EXAMPLE_PROFILE_DIR, root / RUNTIME_PROFILE_DIR):
        path = directory / f"{name}.json"
        if path.exists() and path.is_file():
            return observer.load_profile(path)
    return None


def format_watch_profile_details(profile: WatchProfile) -> str:
    return "\n".join(
        [
            f"profile: {profile.name}",
            f"mode: {profile.mode}",
            f"start_url: {sanitize_url(profile.start_url)}",
            f"url_allowlist: {', '.join(sanitize_url(url) for url in profile.url_allowlist if sanitize_url(url))}",
            f"area: {profile.area}",
            f"region: {json.dumps(profile.region, ensure_ascii=False, sort_keys=True) if profile.region else 'full'}",
            SAFETY_NOTICE,
        ]
    )


def format_watch_event_details(event: WatchEvent) -> str:
    details = [
        f"profile: {event.watch_id}",
        f"event_type: {event.event_type}",
        f"message: {sanitize_text(event.message)}",
        f"timestamp: {event.timestamp}",
        f"confidence: {event.confidence}" if event.confidence is not None else "confidence: ",
    ]
    if event.page_url:
        details.append(f"page_url: {sanitize_url(event.page_url)}")
    metadata = sanitize_event_data(event.metadata, key="metadata")
    for key, value in sorted(metadata.items() if isinstance(metadata, dict) else []):
        details.append(f"{key}: {value}")
    return "\n".join(details)


def read_event_log_details(path: Path, limit: int = 5, profile: str | None = None) -> str:
    result = read_event_log(path, EventQuery(limit=limit, profile=profile))
    last_events = [
        f"{event.get('timestamp', '')} {event.get('profile', '')} {event.get('event_type', '')}".strip()
        for event in result.events
    ]
    return "\n".join(
        [
            f"events_count: {len(result.events)}",
            f"last_events: {' | '.join(last_events)}",
        ]
    )


def event_count(path: Path) -> int:
    return count_valid_events(path)


def _url_allowed(current_url: str, allowlist: list[str]) -> bool:
    parsed = urlparse(current_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    for allowed in allowlist:
        allowed = allowed.strip()
        if not allowed:
            continue
        if allowed.endswith("*") and current_url.startswith(allowed[:-1]):
            return True
        if current_url == allowed or current_url.startswith(allowed):
            return True
    return False


def _page_url(page: object) -> str:
    return str(getattr(page, "url", "") or "")


def _page_text(page: object) -> str:
    if hasattr(page, "content"):
        return str(page.content())
    if hasattr(page, "inner_text"):
        return str(page.inner_text("body"))
    locator = page.locator("body")
    if hasattr(locator, "inner_text"):
        return str(locator.inner_text())
    if hasattr(locator, "text_content"):
        return str(locator.text_content())
    return ""


def _page_title(page: object) -> str:
    title = getattr(page, "title", None)
    if callable(title):
        return str(title() or "")
    return str(title or "")


def _viewport_size(page: object) -> tuple[int, int]:
    viewport = getattr(page, "viewport_size", None)
    if isinstance(viewport, dict):
        return int(viewport.get("width", 1280)), int(viewport.get("height", 800))
    if hasattr(page, "evaluate"):
        width = int(page.evaluate("() => window.innerWidth"))
        height = int(page.evaluate("() => window.innerHeight"))
        return width, height
    return 1280, 800


def _change_ratio(previous: bytes, current: bytes) -> float:
    length = max(len(previous), len(current))
    if length == 0:
        return 0.0
    shared = min(len(previous), len(current))
    changed = abs(len(previous) - len(current))
    changed += sum(1 for index in range(shared) if previous[index] != current[index])
    return changed / length


def _percent_to_pixels(total: int, percent: object) -> int:
    value = float(percent)
    if value <= 0 or value > 100:
        raise ValueError("Viewport region percent must be > 0 and <= 100.")
    return max(1, round(total * value / 100))


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
