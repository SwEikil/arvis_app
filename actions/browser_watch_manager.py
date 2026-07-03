from __future__ import annotations

import atexit
from dataclasses import dataclass
from pathlib import Path
import re
import threading
import time
from typing import Callable

from actions.browser_observer import EVENT_LOG_PATH
from actions.browser_observer import BrowserObserver
from actions.browser_observer import WatchEvent
from actions.browser_observer import WatchProfile
from actions.browser_observer import event_count
from actions.browser_observer import format_watch_event_details
from actions.browser_observer import format_watch_profile_details
from actions.browser_observer import list_watch_profiles
from actions.browser_observer import load_watch_profile
from actions.browser_observer import normalize_browser_watch_profile_name
from actions.browser_observer import read_event_log_details
from actions.browser_observer import _url_allowed
from actions.browser_observer_runtime import BrowserPageSession
from actions.browser_observer_runtime import BrowserRuntimeResult
from actions.browser_observer_runtime import PlaywrightPageProvider
from actions.browser_observer_runtime import validate_runtime_profile


DEFAULT_TIMEOUT_SECONDS = 300
MIN_INTERVAL_MS = 500
DEFAULT_MAX_ACTIVE_WATCHES = 3
DEFAULT_DEBOUNCE_SECONDS = 30
DEFAULT_MAX_EVENTS_PER_MINUTE = 30
BLOCKED_PAGE_SIGNAL_PHRASES = (
    ("captcha", "captcha"),
    ("login", "login"),
    ("login", "log in"),
    ("login", "sign in"),
    ("login", "sign-in"),
    ("login", "signin"),
    ("payment", "payment"),
    ("checkout", "checkout"),
    ("billing", "billing"),
    ("download", "download"),
    ("allow_notifications", "allow notifications"),
    ("enable_notifications", "enable notifications"),
    ("camera_permission", "camera permission"),
    ("microphone_permission", "microphone permission"),
    ("grant_permission", "grant permission"),
    ("browser_permission", "browser permission"),
    ("location_permission", "location permission"),
)


@dataclass
class WatchManagerResult:
    status: str
    reason_code: str | None
    message: str
    details: str | None = None
    executed: bool = False


@dataclass
class WatchStatus:
    watch_id: str
    profile: str
    status: str
    started_at: str
    elapsed_seconds: float
    events_count: int
    last_event: str
    last_error: str


@dataclass
class ActiveWatch:
    watch_id: str
    profile: WatchProfile
    started_at: str
    started_monotonic: float
    timeout_seconds: int
    interval_ms: int
    stop_event: threading.Event
    session: BrowserPageSession | None = None
    thread: threading.Thread | None = None
    startup_event: threading.Event | None = None
    startup_result: WatchManagerResult | None = None
    status: str = "starting"
    events_count: int = 0
    suppressed_duplicates: int = 0
    suppressed_rate_limited: int = 0
    sequence: int = 0
    last_event: str = ""
    last_error: str = ""
    completed_at: str = ""
    current_url: str = ""
    block_type: str = ""
    signal: str = ""


class BrowserWatchManager:
    def __init__(
        self,
        project_root: Path | str | None = None,
        observer: BrowserObserver | None = None,
        page_provider: PlaywrightPageProvider | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
        time_func: Callable[[], float] = time.monotonic,
        timestamp_func: Callable[[], str] | None = None,
        max_loop_iterations: int | None = None,
        max_active_watches: int = DEFAULT_MAX_ACTIVE_WATCHES,
        debounce_seconds: int = DEFAULT_DEBOUNCE_SECONDS,
        max_events_per_minute: int = DEFAULT_MAX_EVENTS_PER_MINUTE,
        start_threads: bool = True,
    ) -> None:
        self.project_root = Path(project_root or ".").resolve()
        self.observer = observer or BrowserObserver(project_root=self.project_root)
        self.page_provider = page_provider or PlaywrightPageProvider()
        self.sleep_func = sleep_func
        self.time_func = time_func
        self.timestamp_func = timestamp_func or _timestamp
        self.max_loop_iterations = max_loop_iterations
        self.max_active_watches = max_active_watches
        self.debounce_seconds = debounce_seconds
        self.max_events_per_minute = max_events_per_minute
        self.start_threads = start_threads
        self._lock = threading.Lock()
        self._active: dict[str, ActiveWatch] = {}
        self._completed: dict[str, ActiveWatch] = {}
        self._recent_events: dict[str, float] = {}
        self._event_times: list[float] = []

    def start_watch(self, profile: WatchProfile) -> WatchManagerResult:
        watch_id = profile.name
        validation = validate_runtime_profile(profile)
        if validation is not None:
            status = "blocked" if validation.status == "blocked" else "not_configured"
            return WatchManagerResult(
                status=status,
                reason_code=validation.reason_code,
                message=validation.message,
                details=_details(
                    {
                        "watch_id": watch_id,
                        "profile": profile.name,
                        "reason_code": validation.reason_code or "",
                        "message": validation.message,
                    }
                ),
            )

        with self._lock:
            existing = self._find_active_locked(watch_id)
            if existing is not None and existing.status in {"starting", "running"}:
                return WatchManagerResult(
                    status="already_running",
                    reason_code="browser_watch_already_running",
                    message="Browser watch is already running.",
                    details=self._watch_details_locked(existing),
                )
            if len(self._active) >= self.max_active_watches:
                return WatchManagerResult(
                    status="too_many_watches",
                    reason_code="too_many_browser_watches",
                    message="Browser watch limit reached.",
                    details=_details({"max_active_watches": self.max_active_watches}),
                )

        started_at = self.timestamp_func()
        active = ActiveWatch(
            watch_id=watch_id,
            profile=profile,
            started_at=started_at,
            started_monotonic=self.time_func(),
            timeout_seconds=int(profile.timeout_seconds or DEFAULT_TIMEOUT_SECONDS),
            interval_ms=max(int(profile.interval_ms or MIN_INTERVAL_MS), MIN_INTERVAL_MS),
            stop_event=threading.Event(),
            startup_event=threading.Event(),
        )
        thread = threading.Thread(target=self._run_watch, args=(active,), name=f"arvis-browser-watch-{watch_id}", daemon=True) if self.start_threads else None
        active.thread = thread
        with self._lock:
            self._active[watch_id] = active
        if thread is not None:
            thread.start()
            if active.startup_event is not None and active.startup_event.wait(timeout=5.0):
                with self._lock:
                    if active.startup_result is not None:
                        return active.startup_result
            elif thread.is_alive():
                return WatchManagerResult(
                    status="starting",
                    reason_code="browser_watch_start_pending",
                    message="Browser watch is still starting.",
                    details=self._watch_details(active),
                    executed=True,
                )
            else:
                with self._lock:
                    return active.startup_result or WatchManagerResult(
                        status="error",
                        reason_code="browser_watch_start_failed",
                        message="Browser watch worker exited before startup completed.",
                        details=self._watch_details_locked(active),
                    )
        else:
            active.status = "running"
        return WatchManagerResult(
            status="started",
            reason_code=None,
            message="Browser watch started.",
            details=self._watch_details(active),
            executed=True,
        )

    def stop_watch(self, target: str | None) -> WatchManagerResult:
        watch_id = normalize_browser_watch_profile_name(target)
        with self._lock:
            active = self._find_active_locked(watch_id)
            if active is None:
                completed = self._find_completed_locked(watch_id)
                if completed is not None:
                    return self._not_running_result_locked(completed)
                known_profile = load_watch_profile(watch_id, self.project_root)
                if known_profile is not None:
                    return WatchManagerResult(
                        status="not_running",
                        reason_code="browser_watch_not_running",
                        message="Browser watch is not running.",
                        details=_details(
                            {
                                "watch_id": known_profile.name,
                                "profile": known_profile.name,
                                "last_status": "not_started",
                                "last_error": "",
                            }
                        ),
                    )
                return WatchManagerResult(
                    status="not_found",
                    reason_code="browser_watch_not_found",
                    message="Browser watch not found.",
                    details=_details({"watch_id": watch_id}),
                )
            active.stop_event.set()
            thread = active.thread
        if thread is not None:
            thread.join(timeout=2.0)
        with self._lock:
            if watch_id in self._active:
                self._finish_watch_locked(active, "stopped", "")
        return WatchManagerResult(
            status="stopped",
            reason_code=None,
            message="Browser watch stopped.",
            details=self._watch_details(active),
            executed=True,
        )

    def status(self) -> WatchManagerResult:
        with self._lock:
            active = [self._watch_details_locked(watch) for watch in self._active.values()]
            completed = [self._watch_details_locked(watch) for watch in self._completed.values()]
            profiles = ", ".join(list_watch_profiles(self.project_root)) or "(none)"
            details = "\n".join(
                [
                    f"profiles: {profiles}",
                    f"active_count: {len(self._active)}",
                    f"active_watches: {' | '.join(active)}",
                    f"completed_watches: {' | '.join(completed)}",
                    f"events_count: {event_count(self.project_root / EVENT_LOG_PATH)}",
                ]
            )
        return WatchManagerResult(status="status", reason_code=None, message="Browser watch status.", details=details, executed=True)

    def events(self, target: str | None = None) -> WatchManagerResult:
        profile = normalize_browser_watch_profile_name(target)
        if profile == "observer":
            profile = ""
        return WatchManagerResult(
            status="events",
            reason_code=None,
            message="Browser watch events.",
            details=read_event_log_details(self.project_root / EVENT_LOG_PATH, profile=profile or None),
            executed=True,
        )

    def shutdown_all(self) -> None:
        with self._lock:
            watches = list(self._active.values())
        for watch in watches:
            watch.stop_event.set()
        for watch in watches:
            if watch.thread is not None:
                watch.thread.join(timeout=2.0)
            with self._lock:
                if watch.watch_id in self._active:
                    self._finish_watch_locked(watch, "stopped", "shutdown")

    def _run_watch(self, watch: ActiveWatch) -> None:
        iterations = 0
        session: BrowserPageSession | None = None
        try:
            startup = self._open_session_for_watch(watch)
            if startup is not None:
                self._set_startup_result(watch, startup)
                return
            session = watch.session
            if session is None:
                result = self._finish_with_result(watch, "error", "browser_watch_start_failed", "provider_start RuntimeError: page session missing")
                self._set_startup_result(watch, result)
                return

            blocked_reason = self._safe_blocked_page_reason(watch, session.page)
            if blocked_reason:
                if blocked_reason.startswith("url_check_error "):
                    result = self._finish_with_result(watch, "error", "browser_watch_page_error", blocked_reason)
                    self._set_startup_result(watch, result)
                    return
                result = self._finish_with_result(watch, "blocked", "browser_observer_blocked", blocked_reason)
                self._set_startup_result(watch, result)
                return

            with self._lock:
                watch.status = "running"
            self._set_startup_result(
                watch,
                WatchManagerResult(
                    status="started",
                    reason_code=None,
                    message="Browser watch started.",
                    details=self._watch_details(watch),
                    executed=True,
                ),
            )

            while not watch.stop_event.is_set():
                if self.max_loop_iterations is not None and iterations >= self.max_loop_iterations:
                    self._finish_watch(watch, "completed", "max_loop_iterations")
                    return
                iterations += 1
                elapsed = self.time_func() - watch.started_monotonic
                if elapsed >= watch.timeout_seconds:
                    self._finish_watch(watch, "completed", "timeout")
                    return
                blocked_reason = self._safe_blocked_page_reason(watch, session.page)
                if blocked_reason:
                    if blocked_reason.startswith("url_check_error "):
                        self._finish_watch(watch, "error", blocked_reason)
                        return
                    self._finish_watch(watch, "blocked", blocked_reason)
                    return
                try:
                    event = self.observer.poll_once(watch.profile, session.page)
                except Exception as exc:
                    self._finish_watch(watch, "error", _stage_error("poll_once", exc))
                    return
                if event is not None:
                    if event.event_type == "observer_blocked":
                        self._finish_watch(watch, "blocked", event.message)
                        return
                    if event.event_type == "observer_error":
                        self._finish_watch(watch, "error", _observer_event_error("poll_once", event))
                        return
                    if event.event_type == "observer_not_configured":
                        self._finish_watch(watch, "not_configured", event.message)
                        return
                    try:
                        self._record_event(watch, event)
                    except Exception as exc:
                        self._finish_watch(watch, "error", _stage_error("event_write", exc))
                        return
                self.sleep_func(watch.interval_ms / 1000)
        except Exception as exc:
            self._finish_watch(watch, "error", _stage_error("watch_loop", exc))
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception as exc:
                    with self._lock:
                        if watch.watch_id in self._active:
                            self._finish_watch_locked(watch, "error", _stage_error("cleanup", exc))

    def _open_session_for_watch(self, watch: ActiveWatch) -> WatchManagerResult | None:
        try:
            session_result = self.page_provider.open_page(watch.profile.start_url or "")
        except Exception as exc:
            return self._finish_with_result(watch, "error", "browser_watch_provider_error", _stage_error("provider_start", exc))
        if isinstance(session_result, BrowserRuntimeResult):
            status = session_result.status
            if status not in {"blocked", "not_configured", "error"}:
                status = "error"
            last_error = _runtime_result_error("provider_start", session_result)
            return self._finish_with_result(watch, status, session_result.reason_code or f"browser_watch_{status}", last_error, session_result.details)
        with self._lock:
            watch.session = session_result
        return None

    def _record_event(self, watch: ActiveWatch, event: WatchEvent) -> None:
        now = self.time_func()
        key = _event_key(event)
        with self._lock:
            previous = self._recent_events.get(key)
            if previous is not None and now - previous < self.debounce_seconds:
                watch.suppressed_duplicates += 1
                return
            self._event_times = [value for value in self._event_times if now - value < 60]
            if len(self._event_times) >= self.max_events_per_minute:
                watch.suppressed_rate_limited += 1
                return
            watch.sequence += 1
            watch.events_count += 1
            watch.last_event = event.event_type
            self._recent_events[key] = now
            self._event_times.append(now)
            event.metadata.update(
                {
                    "source": "background_watch",
                    "watch_id": watch.watch_id,
                    "profile": watch.profile.name,
                    "mode": watch.profile.mode,
                    "sequence": watch.sequence,
                    "started_at": watch.started_at,
                    "detected_at": self.timestamp_func(),
                    "current_url": _page_url(watch.session.page) if watch.session is not None else "",
                    "suppressed_duplicate": False,
                }
            )
        self.observer.write_event(event)

    def _safe_blocked_page_reason(self, watch: ActiveWatch, page: object) -> str:
        try:
            current_url = _page_url(page)
            with self._lock:
                watch.current_url = current_url
            if not _url_allowed(current_url, watch.profile.url_allowlist):
                with self._lock:
                    watch.block_type = "url_allowlist"
                    watch.signal = ""
                return f"url_outside_allowlist:{current_url}"
            title = _page_title(page).lower()
            text = _page_text(page).lower()
        except Exception as exc:
            return _stage_error("url_check_error", exc)
        haystack = f"{current_url.lower()} {title} {text}"
        match = _page_signal_match(haystack)
        if match:
            signal, _phrase = match
            with self._lock:
                watch.block_type = "page_signal"
                watch.signal = signal
            return f"blocked_page_signal:{signal}"
        return ""

    def _set_startup_result(self, watch: ActiveWatch, result: WatchManagerResult) -> None:
        with self._lock:
            watch.startup_result = result
            if watch.startup_event is not None:
                watch.startup_event.set()

    def _finish_with_result(
        self,
        watch: ActiveWatch,
        status: str,
        reason_code: str,
        error: str,
        details: str | None = None,
    ) -> WatchManagerResult:
        self._finish_watch(watch, status, error)
        return WatchManagerResult(
            status=status,
            reason_code=reason_code,
            message=error,
            details=details or self._watch_details(watch),
        )

    def _finish_watch(self, watch: ActiveWatch, status: str, error: str) -> None:
        with self._lock:
            self._finish_watch_locked(watch, status, error)

    def _finish_watch_locked(self, watch: ActiveWatch, status: str, error: str) -> None:
        watch.status = status
        watch.last_error = error
        watch.completed_at = self.timestamp_func()
        self._active.pop(watch.watch_id, None)
        self._completed[watch.watch_id] = watch

    def _find_active_locked(self, target: str) -> ActiveWatch | None:
        active = self._active.get(target)
        if active is not None:
            return active
        for watch in self._active.values():
            if watch.profile.name == target:
                return watch
        return None

    def _find_completed_locked(self, target: str) -> ActiveWatch | None:
        completed = self._completed.get(target)
        if completed is not None:
            return completed
        for watch in self._completed.values():
            if watch.profile.name == target:
                return watch
        return None

    def _not_running_result_locked(self, watch: ActiveWatch) -> WatchManagerResult:
        values = {
            "watch_id": watch.watch_id,
            "profile": watch.profile.name,
            "last_status": watch.status,
            "last_error": watch.last_error,
        }
        if watch.block_type:
            values["block_type"] = watch.block_type
        if watch.signal:
            values["signal"] = watch.signal
        if watch.current_url:
            values["current_url"] = watch.current_url
        return WatchManagerResult(
            status="not_running",
            reason_code="browser_watch_not_running",
            message="Browser watch is not running.",
            details=_details(values),
        )

    def _watch_details(self, watch: ActiveWatch) -> str:
        with self._lock:
            return self._watch_details_locked(watch)

    def _watch_details_locked(self, watch: ActiveWatch) -> str:
        elapsed = self.time_func() - watch.started_monotonic
        return "\n".join(
            [
                f"watch_id={watch.watch_id}",
                f"profile={watch.profile.name}",
                f"status={watch.status}",
                f"last_status={watch.status}",
                f"started_at={watch.started_at}",
                f"elapsed_seconds={elapsed:.2f}",
                f"events_count={watch.events_count}",
                f"last_event={watch.last_event}",
                f"last_error={watch.last_error}",
                f"suppressed_duplicates={watch.suppressed_duplicates}",
                f"suppressed_rate_limited={watch.suppressed_rate_limited}",
            ]
            + _block_detail_lines(watch)
        )


_MANAGERS_LOCK = threading.Lock()
_MANAGERS: dict[Path, BrowserWatchManager] = {}


def get_browser_watch_manager(project_root: Path | str | None = None) -> BrowserWatchManager:
    root = Path(project_root or ".").resolve()
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(root)
        if manager is None:
            manager = BrowserWatchManager(project_root=root)
            _MANAGERS[root] = manager
        return manager


def shutdown_all_browser_watch_managers() -> None:
    with _MANAGERS_LOCK:
        managers = list(_MANAGERS.values())
    for manager in managers:
        manager.shutdown_all()


def preview_browser_watch_action(action: str, target: str | None, project_root: Path | str | None = None) -> tuple[bool, str, str | None]:
    root = Path(project_root or ".").resolve()
    if action in {"browser_watch_start", "browser_watch_poll_once"}:
        profile = load_watch_profile(target, root)
        if profile is None:
            return False, "Browser watch profile is not in the whitelist.", f"target: {normalize_browser_watch_profile_name(target)}"
        verb = "start browser watch" if action == "browser_watch_start" else "poll browser watch profile once"
        return False, f"Dry-run: would {verb} `{profile.name}`.", format_watch_profile_details(profile)
    if action == "browser_watch_stop":
        return False, "Dry-run: would stop browser watch.", _details({"watch_id": normalize_browser_watch_profile_name(target)})
    if action == "browser_watch_status":
        return False, "Dry-run: would inspect browser watch status.", get_browser_watch_manager(root).status().details
    if action == "browser_watch_events":
        return False, "Dry-run: would read browser watch events.", read_event_log_details(root / EVENT_LOG_PATH)
    return False, "Browser observer action is not supported.", f"action: {action}"


def execute_browser_watch_action(
    action: str,
    target: str | None,
    project_root: Path | str | None = None,
    page: object | None = None,
) -> tuple[bool, str, str | None]:
    root = Path(project_root or ".").resolve()
    observer = BrowserObserver(project_root=root)
    manager = get_browser_watch_manager(root)
    if action == "browser_watch_status":
        return _manager_result_to_tuple(manager.status())
    if action == "browser_watch_events":
        return _manager_result_to_tuple(manager.events(target))
    if action == "browser_watch_start":
        profile = load_watch_profile(target, root)
        if profile is None:
            return False, "Browser watch profile is not in the whitelist.", f"target: {normalize_browser_watch_profile_name(target)}"
        return _manager_result_to_tuple(manager.start_watch(profile))
    if action == "browser_watch_stop":
        return _manager_result_to_tuple(manager.stop_watch(target))
    if action == "browser_watch_poll_once":
        profile = load_watch_profile(target, root)
        if profile is None:
            return False, "Browser watch profile is not in the whitelist.", f"target: {normalize_browser_watch_profile_name(target)}"
        if page is None:
            from actions.browser_observer_runtime import BrowserObserverRuntime

            result = BrowserObserverRuntime(project_root=root).poll_once(profile)
            return _runtime_result_to_tuple(result)
        event = observer.poll_once(profile, page)
        if event is None:
            return False, "Browser observer found no event.", f"profile: {profile.name}\nevent_type: no_event"
        observer.write_event(event)
        return _event_to_tuple(event)
    return False, "Browser observer action is not supported.", f"action: {action}"


def _manager_result_to_tuple(result: WatchManagerResult) -> tuple[bool, str, str | None]:
    if result.status == "started":
        return True, "Browser watch started.", result.details
    if result.status == "starting":
        return True, "Browser watch is still starting.", result.details
    if result.status == "already_running":
        return False, "Browser watch already running.", result.details
    if result.status == "stopped":
        return True, "Browser watch stopped.", result.details
    if result.status == "not_running":
        return False, "Browser watch is not running.", result.details
    if result.status == "not_found":
        return False, "Browser watch not found.", result.details
    if result.status == "too_many_watches":
        return False, "Browser watch limit reached.", result.details
    if result.status == "blocked":
        return False, "Browser observer blocked.", result.details
    if result.status == "not_configured":
        return False, "Browser Observer is not configured.", result.details
    if result.status == "error":
        return False, "Browser observer error.", result.details
    return result.executed, result.message, result.details


def _runtime_result_to_tuple(result: BrowserRuntimeResult) -> tuple[bool, str, str | None]:
    if result.event is not None:
        return _event_to_tuple(result.event)
    if result.status == "no_event":
        return False, "Browser observer found no event.", result.details
    if result.status == "blocked":
        return False, "Browser observer blocked.", result.details
    if result.status == "not_configured":
        return False, "Browser Observer is not configured.", result.details
    if result.status == "error":
        return False, "Browser observer error.", result.details
    return False, result.message, result.details


def _event_to_tuple(event: WatchEvent) -> tuple[bool, str, str | None]:
    details = format_watch_event_details(event)
    if event.event_type == "observer_blocked":
        return False, "Browser observer blocked.", details
    if event.event_type == "observer_not_configured":
        return False, "Browser Observer is not configured.", details
    if event.event_type == "observer_error":
        return False, "Browser observer error.", details
    return True, "Browser observer event found.", details


def _event_key(event: WatchEvent) -> str:
    url = event.metadata.get("url") or event.metadata.get("current_url") or ""
    return f"{event.watch_id}:{event.event_type}:{event.message}:{url}"


def _page_signal_match(haystack: str) -> tuple[str, str] | None:
    for signal, phrase in BLOCKED_PAGE_SIGNAL_PHRASES:
        pattern = r"(?<![a-z0-9_])" + re.escape(phrase) + r"(?![a-z0-9_])"
        if re.search(pattern, haystack):
            return signal, phrase
    return None


def _block_detail_lines(watch: ActiveWatch) -> list[str]:
    lines: list[str] = []
    if watch.block_type:
        lines.append(f"block_type={watch.block_type}")
    if watch.signal:
        lines.append(f"signal={watch.signal}")
    if watch.current_url:
        lines.append(f"current_url={watch.current_url}")
    return lines


def _page_url(page: object) -> str:
    return str(getattr(page, "url", "") or "")


def _page_title(page: object) -> str:
    if hasattr(page, "title"):
        try:
            return str(page.title())
        except Exception:
            return ""
    return ""


def _page_text(page: object) -> str:
    if hasattr(page, "content"):
        try:
            return str(page.content())
        except Exception:
            return ""
    return ""


def _stage_error(stage: str, exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return f"{stage} {type(exc).__name__}: {message}"
    return f"{stage} {type(exc).__name__}"


def _runtime_result_error(stage: str, result: BrowserRuntimeResult) -> str:
    details = _parse_details(result.details)
    error_type = details.get("error_type") or result.reason_code or result.status
    error = details.get("error") or details.get("message") or result.message
    pieces = [stage]
    if error_type:
        pieces.append(str(error_type))
    if error:
        pieces.append(str(error))
    return ": ".join([" ".join(pieces[:2]), " ".join(pieces[2:])]) if len(pieces) > 2 else " ".join(pieces)


def _observer_event_error(stage: str, event: WatchEvent) -> str:
    error_type = event.metadata.get("error_type") or event.metadata.get("reason_code") or event.event_type
    error = event.metadata.get("error") or event.message
    if str(error).strip().lower() == "error":
        error = f"{event.event_type} returned error"
    return f"{stage} {error_type}: {error}"


def _parse_details(details: str | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in (details or "").splitlines():
        if "=" in line and (":" not in line or line.index("=") < line.index(":")):
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        parsed[key.strip()] = value.strip()
    return parsed


def _details(values: dict[str, object]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in values.items())


def _timestamp() -> str:
    from datetime import datetime
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat()


atexit.register(shutdown_all_browser_watch_managers)
