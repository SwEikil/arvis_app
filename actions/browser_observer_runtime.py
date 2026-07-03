from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from actions.browser_observer import BrowserObserver
from actions.browser_observer import ObserverValidationResult
from actions.browser_observer import SAFETY_NOTICE
from actions.browser_observer import WatchEvent
from actions.browser_observer import WatchProfile
from actions.browser_observer import _url_allowed
from config import ARVIS_BROWSER_OBSERVER_HEADFUL


DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
GOTO_TIMEOUT_MS = 30_000


@dataclass(frozen=True)
class BrowserRuntimeResult:
    status: str
    reason_code: str | None
    message: str
    event: WatchEvent | None = None
    details: str | None = None


@dataclass
class BrowserPageSession:
    manager: object | None
    browser: object | None
    context: object | None
    page: object
    headless: bool

    def close(self) -> None:
        _close_quietly(self.context)
        _close_quietly(self.browser)
        if self.manager is not None:
            try:
                self.manager.__exit__(None, None, None)
            except Exception:
                pass


class PlaywrightPageProvider:
    def __init__(
        self,
        headful: bool = ARVIS_BROWSER_OBSERVER_HEADFUL,
        viewport: dict[str, int] | None = None,
        goto_timeout_ms: int = GOTO_TIMEOUT_MS,
    ) -> None:
        self.headful = headful
        self.viewport = dict(viewport or DEFAULT_VIEWPORT)
        self.goto_timeout_ms = goto_timeout_ms

    def with_page(self, start_url: str, callback: Callable[[object], WatchEvent | None]) -> BrowserRuntimeResult:
        session_result = self.open_page(start_url)
        if isinstance(session_result, BrowserRuntimeResult):
            return session_result
        session = session_result
        try:
            event = callback(session.page)
            if event is None:
                return BrowserRuntimeResult(
                    status="no_event",
                    reason_code=None,
                    message="Browser observer poll-once completed with no event.",
                    details=_details(
                        {
                            "event_type": "no_event",
                            "start_url": start_url,
                            "headless": str(session.headless),
                            "reason_code": "",
                        }
                    ),
                )
            return BrowserRuntimeResult(
                status="event",
                reason_code=None,
                message="Browser observer event found.",
                event=event,
                details=_details({"event_type": event.event_type, "start_url": start_url, "headless": str(session.headless)}),
            )
        except Exception as exc:
            return BrowserRuntimeResult(
                status="error",
                reason_code="browser_observer_runtime_error",
                message=f"Browser Observer runtime error: {type(exc).__name__}",
                details=_details(
                    {
                        "reason_code": "browser_observer_runtime_error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "start_url": start_url,
                        "headless": str(session.headless),
                        "notice": SAFETY_NOTICE,
                    }
                ),
            )
        finally:
            session.close()

    def open_page(self, start_url: str) -> BrowserPageSession | BrowserRuntimeResult:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            return _not_configured(
                "playwright_missing",
                "Playwright is not installed for Browser Observer runtime.",
                {"error_type": type(exc).__name__},
            )

        manager = None
        browser = None
        context = None
        try:
            manager = sync_playwright()
            playwright = manager.__enter__()
            browser = playwright.chromium.launch(
                headless=not self.headful,
                args=[
                    "--disable-popup-blocking=false",
                    "--disable-extensions",
                    "--no-first-run",
                ],
            )
            context = browser.new_context(viewport=self.viewport)
            page = context.new_page()
            page.goto(start_url, wait_until="domcontentloaded", timeout=self.goto_timeout_ms)
            return BrowserPageSession(manager=manager, browser=browser, context=context, page=page, headless=not self.headful)
        except Exception as exc:
            _close_quietly(context)
            _close_quietly(browser)
            if manager is not None:
                try:
                    manager.__exit__(None, None, None)
                except Exception:
                    pass
            if _looks_like_playwright_install_issue(exc):
                return _not_configured(
                    "playwright_browser_missing",
                    "Playwright Chromium is not installed for Browser Observer runtime.",
                    {"error_type": type(exc).__name__},
                )
            return BrowserRuntimeResult(
                status="error",
                reason_code="browser_observer_runtime_error",
                message=f"Browser Observer runtime error: {type(exc).__name__}",
                details=_details(
                    {
                        "reason_code": "browser_observer_runtime_error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "start_url": start_url,
                        "headless": str(not self.headful),
                        "notice": SAFETY_NOTICE,
                    }
                ),
            )


class BrowserObserverRuntime:
    def __init__(
        self,
        project_root: Path | str | None = None,
        observer: BrowserObserver | None = None,
        page_provider: PlaywrightPageProvider | None = None,
    ) -> None:
        self.project_root = Path(project_root or ".").resolve()
        self.observer = observer or BrowserObserver(project_root=self.project_root)
        self.page_provider = page_provider or PlaywrightPageProvider()

    def poll_once(self, profile: WatchProfile) -> BrowserRuntimeResult:
        start_validation = validate_runtime_profile(profile)
        if start_validation is not None:
            status = "blocked" if start_validation.status == "blocked" else "not_configured"
            return BrowserRuntimeResult(
                status=status,
                reason_code=start_validation.reason_code,
                message=start_validation.message,
                details=_details(
                    {
                        "profile": profile.name,
                        "event_type": "observer_blocked" if status == "blocked" else "observer_not_configured",
                        "reason_code": start_validation.reason_code or "",
                        "message": start_validation.message,
                        "start_url": profile.start_url or "",
                        "notice": SAFETY_NOTICE,
                    }
                ),
            )

        def observe(page: object) -> WatchEvent | None:
            return self.observer.poll_once(profile, page)

        result = self.page_provider.with_page(profile.start_url or "", observe)
        if result.event is not None:
            self.observer.write_event(result.event)
        return result


def validate_runtime_profile(profile: WatchProfile) -> ObserverValidationResult | None:
    if not profile.start_url:
        return ObserverValidationResult(
            False,
            "not_configured",
            "profile_start_url_missing",
            "Browser observer profile is missing start_url.",
        )
    if not _url_allowed(profile.start_url, profile.url_allowlist):
        return ObserverValidationResult(
            False,
            "blocked",
            "start_url_not_allowed",
            "Browser observer start_url is not in the profile allowlist.",
        )
    return None


def _not_configured(reason_code: str, message: str, extra: dict[str, str] | None = None) -> BrowserRuntimeResult:
    details = {"event_type": "observer_not_configured", "reason_code": reason_code, "message": message, "notice": SAFETY_NOTICE}
    details.update(extra or {})
    return BrowserRuntimeResult(status="not_configured", reason_code=reason_code, message=message, details=_details(details))


def _details(values: dict[str, object]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in values.items())


def _close_quietly(value: object | None) -> None:
    if value is None or not hasattr(value, "close"):
        return
    try:
        value.close()
    except Exception:
        pass


def _looks_like_playwright_install_issue(exc: Exception) -> bool:
    text = str(exc).lower()
    return "executable" in text or "browser" in text and "install" in text or "playwright install" in text
