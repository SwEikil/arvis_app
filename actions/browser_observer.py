from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse


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
        try:
            current_url = _page_url(page)
            validation = self.validate_profile(profile, current_url)
            if validation is not None:
                return self._event(
                    profile,
                    "observer_blocked" if validation.status == "blocked" else "observer_not_configured",
                    validation.message,
                    confidence=None,
                    metadata={"reason_code": validation.reason_code, "url": current_url, "mode": profile.mode},
                )

            if profile.mode == "dom_selector":
                return self._poll_dom_selector(profile, page, current_url)
            if profile.mode == "text_appeared":
                return self._poll_text_appeared(profile, page, current_url)
            if profile.mode == "viewport_change":
                return self._poll_viewport_change(profile, page, current_url)
            if profile.mode == "template_match":
                return self._poll_template_match(profile, page, current_url)
            return self._event(
                profile,
                "observer_not_configured",
                f"Unsupported watch mode: {profile.mode}",
                confidence=None,
                metadata={"reason_code": "unsupported_watch_mode", "mode": profile.mode},
            )
        except Exception as exc:
            return self._event(
                profile,
                "observer_error",
                f"Browser observer error: {type(exc).__name__}",
                confidence=None,
                metadata={"error": str(exc), "error_type": type(exc).__name__, "mode": profile.mode},
            )

    def write_event(self, event: WatchEvent) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=False, sort_keys=True) + "\n")

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
        return str(path)

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
        )


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


def preview_browser_watch_action(action: str, target: str | None, project_root: Path | str | None = None) -> tuple[bool, str, str | None]:
    root = Path(project_root or ".").resolve()
    if action == "browser_watch_status":
        return False, "Dry-run: would inspect browser observer status.", _observer_status_details(root)
    if action == "browser_watch_events":
        return False, "Dry-run: would read browser observer events.", f"events_path: {root / EVENT_LOG_PATH}"
    if action == "browser_watch_poll_once":
        profile = _load_named_profile(target, root)
        if profile is None:
            return False, "Browser watch profile is not in the whitelist.", f"target: {normalize_browser_watch_profile_name(target)}"
        return False, f"Dry-run: would poll browser watch profile `{profile.name}` once.", _profile_details(profile)
    return False, "Browser observer action is not supported.", f"action: {action}"


def execute_browser_watch_action(
    action: str,
    target: str | None,
    project_root: Path | str | None = None,
    page: object | None = None,
) -> tuple[bool, str, str | None]:
    root = Path(project_root or ".").resolve()
    observer = BrowserObserver(project_root=root)
    if action == "browser_watch_status":
        return True, "Browser observer status.", _observer_status_details(root)
    if action == "browser_watch_events":
        return True, "Browser observer events.", _events_details(root / EVENT_LOG_PATH)
    if action == "browser_watch_poll_once":
        profile = _load_named_profile(target, root)
        if profile is None:
            return False, "Browser watch profile is not in the whitelist.", f"target: {normalize_browser_watch_profile_name(target)}"
        if page is None:
            from actions.browser_observer_runtime import BrowserObserverRuntime

            result = BrowserObserverRuntime(project_root=root).poll_once(profile)
            if result.event is not None:
                return _event_to_router_tuple(result.event)
            if result.status == "no_event":
                return False, "Browser observer found no event.", result.details
            if result.status == "blocked":
                return False, "Browser observer blocked.", result.details
            if result.status == "not_configured":
                return False, "Browser Observer is not configured.", result.details
            if result.status == "error":
                return False, "Browser observer error.", result.details
            return False, result.message, result.details
        event = observer.poll_once(profile, page)
        if event is None:
            return False, "Browser observer found no event.", f"profile: {profile.name}\nevent_type: no_event\n{SAFETY_NOTICE}"
        observer.write_event(event)
        return _event_to_router_tuple(event)
    return False, "Browser observer action is not supported.", f"action: {action}"


def _event_to_router_tuple(event: WatchEvent) -> tuple[bool, str, str | None]:
    details = [
        f"profile: {event.watch_id}",
        f"event_type: {event.event_type}",
        f"message: {event.message}",
        f"timestamp: {event.timestamp}",
        f"confidence: {event.confidence}" if event.confidence is not None else "confidence: ",
        f"{SAFETY_NOTICE}",
    ]
    for key, value in sorted(event.metadata.items()):
        details.append(f"{key}: {value}")
    if event.event_type == "observer_blocked":
        return False, "Browser observer blocked.", "\n".join(details)
    if event.event_type == "observer_not_configured":
        return False, "Browser Observer is not configured.", "\n".join(details)
    if event.event_type == "observer_error":
        return False, "Browser observer error.", "\n".join(details)
    return True, "Browser observer event found.", "\n".join(details)


def _load_named_profile(target: str | None, project_root: Path) -> WatchProfile | None:
    name = normalize_browser_watch_profile_name(target)
    if not name or not WATCH_PROFILE_NAME_RE.fullmatch(name):
        return None
    observer = BrowserObserver(project_root=project_root)
    for directory in (project_root / EXAMPLE_PROFILE_DIR, project_root / RUNTIME_PROFILE_DIR):
        path = directory / f"{name}.json"
        if path.exists() and path.is_file():
            return observer.load_profile(path)
    return None


def _observer_status_details(project_root: Path) -> str:
    profiles = list_watch_profiles(project_root)
    event_path = project_root / EVENT_LOG_PATH
    return "\n".join(
        [
            f"profiles: {', '.join(profiles) if profiles else '(none)'}",
            f"events_path: {event_path}",
            f"events_count: {_event_count(event_path)}",
            SAFETY_NOTICE,
        ]
    )


def _profile_details(profile: WatchProfile) -> str:
    return "\n".join(
        [
            f"profile: {profile.name}",
            f"mode: {profile.mode}",
            f"start_url: {profile.start_url or ''}",
            f"url_allowlist: {', '.join(profile.url_allowlist)}",
            f"area: {profile.area}",
            f"region: {json.dumps(profile.region, ensure_ascii=False, sort_keys=True) if profile.region else 'full'}",
            SAFETY_NOTICE,
        ]
    )


def _events_details(path: Path, limit: int = 5) -> str:
    if not path.exists():
        return f"events_count: 0\nlast_events: \n{SAFETY_NOTICE}"
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    last_events: list[str] = []
    for line in lines[-limit:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        last_events.append(f"{payload.get('timestamp', '')} {payload.get('watch_id', '')} {payload.get('event_type', '')}".strip())
    return "\n".join(
        [
            f"events_count: {len(lines)}",
            f"last_events: {' | '.join(last_events)}",
            SAFETY_NOTICE,
        ]
    )


def _event_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


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
