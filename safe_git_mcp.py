from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import project_context
import safe_git_control
from mcp_access import McpAccessConfig, path_is_within
from mcp_security import redact_sensitive_text


CONTROL_ENABLED_ENV = "ARVIS_SAFE_GIT_CONTROL_ENABLED"
REMOTE_NAME_ENV = "ARVIS_SAFE_GIT_REMOTE_NAME"
EXPECTED_REMOTE_URL_ENV = "ARVIS_SAFE_GIT_EXPECTED_REMOTE_URL"
PUBLIC_NAME_ENV = "ARVIS_SAFE_GIT_PUBLIC_NAME"
PUBLIC_EMAIL_ENV = "ARVIS_SAFE_GIT_PUBLIC_EMAIL"
PUSH_ENABLED_ENV = "ARVIS_SAFE_GIT_PUSH_ENABLED"
HISTORY_REWRITE_ENABLED_ENV = "ARVIS_SAFE_GIT_HISTORY_REWRITE_ENABLED"

_FILE_URI_MARKER_RE = re.compile(r"(?i)(?<![A-Za-z0-9+.-])file:(?:/{1,3}|\\\\)")
_POSIX_ABSOLUTE_PATH_MARKER_RE = re.compile(
    r"(?<![A-Za-z0-9_/.-])/+(?=[^/\s])"
)
_WINDOWS_ABSOLUTE_PATH_MARKER_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:[A-Z]:[\\/]|\\\\(?=[^\\/\s]+[\\/]))"
)
_LOCAL_PATH_ERROR = "Safe Git operation was rejected: <LOCAL_PATH>."


class SafeGitIntegrationError(ValueError):
    """Controlled, client-safe failure at the MCP Git policy boundary."""


@dataclass(frozen=True)
class SafeGitController:
    """Lifecycle-scoped trusted policy for the narrow MCP Git adapter."""

    enabled: bool
    push_enabled: bool
    history_rewrite_enabled: bool
    _policy: safe_git_control.SafeGitPolicy | None = field(default=None, repr=False)
    _configuration_error: str | None = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        return self.enabled and self._policy is not None and self._configuration_error is None

    @property
    def push_available(self) -> bool:
        return (
            self.available
            and self.push_enabled
            and self._policy is not None
            and safe_git_control.supports_safe_push(self._policy)
        )

    def available_for(self, access_config: McpAccessConfig) -> bool:
        """Return whether startup has an effective, validated write policy."""

        if not self.available or access_config.configuration_error is not None:
            return False
        if not access_config.writable_roots or not access_config.allowed_roots:
            return False
        for root in access_config.writable_roots:
            if not isinstance(root, Path) or not root.is_absolute():
                return False
            try:
                resolved = root.resolve(strict=True)
            except (OSError, RuntimeError, ValueError):
                return False
            if (
                resolved != root
                or not resolved.is_dir()
                or not any(
                    isinstance(allowed, Path)
                    and allowed.is_absolute()
                    and path_is_within(resolved, allowed)
                    for allowed in access_config.allowed_roots
                )
            ):
                return False
        return True

    def preflight(
        self,
        project_root: str | None,
        *,
        access_config: McpAccessConfig,
    ) -> dict[str, Any]:
        root, policy = self._authorized(project_root, access_config=access_config, write=False)
        try:
            result = safe_git_control.preflight(root, policy)
        except (safe_git_control.SafeGitConfigError, safe_git_control.SafeGitOperationError) as exc:
            raise _safe_engine_error(exc, root, policy) from exc
        return {
            "branch": result.branch,
            "head": result.head,
            "staged_paths": list(result.staged_paths),
            "changed_count": result.changed_count,
            "remote_state": result.remote_state,
            "remote_head": result.remote_head,
            "ahead_count": result.ahead_count,
        }

    def stage_paths(
        self,
        paths: list[str],
        project_root: str | None,
        *,
        access_config: McpAccessConfig,
    ) -> dict[str, Any]:
        root, policy = self._authorized(project_root, access_config=access_config, write=True)
        try:
            result = safe_git_control.stage_paths(
                root,
                paths,
                policy,
                writable_roots=access_config.writable_roots,
            )
        except (safe_git_control.SafeGitConfigError, safe_git_control.SafeGitOperationError) as exc:
            raise _safe_engine_error(exc, root, policy) from exc
        return {
            "staged_paths": list(result.staged_paths),
            "staged_count": result.staged_count,
        }

    def commit_staged(
        self,
        subject: str,
        project_root: str | None,
        *,
        access_config: McpAccessConfig,
    ) -> dict[str, Any]:
        root, policy = self._authorized(project_root, access_config=access_config, write=True)
        try:
            result = safe_git_control.commit_staged(
                root,
                subject,
                policy,
                writable_roots=access_config.writable_roots,
            )
        except (safe_git_control.SafeGitConfigError, safe_git_control.SafeGitOperationError) as exc:
            raise _safe_engine_error(exc, root, policy) from exc
        return {
            "sha": result.sha,
            "subject": result.subject,
            "committed_paths": list(result.committed_paths),
        }

    def push_current(
        self,
        project_root: str | None,
        *,
        access_config: McpAccessConfig,
    ) -> dict[str, Any]:
        root, policy = self._authorized(project_root, access_config=access_config, write=True)
        try:
            result = safe_git_control.push_current(
                root,
                policy,
                writable_roots=access_config.writable_roots,
            )
        except (safe_git_control.SafeGitConfigError, safe_git_control.SafeGitOperationError) as exc:
            raise _safe_engine_error(exc, root, policy) from exc
        return {
            "branch": result.branch,
            "old_remote_head": result.old_remote_head,
            "new_head": result.new_head,
            "pushed": result.pushed,
        }

    def rewrite_unpushed_identity(
        self,
        project_root: str | None,
        *,
        access_config: McpAccessConfig,
    ) -> dict[str, Any]:
        root, policy = self._authorized(project_root, access_config=access_config, write=True)
        try:
            result = safe_git_control.rewrite_unpushed_identity(
                root,
                policy,
                writable_roots=access_config.writable_roots,
            )
        except (safe_git_control.SafeGitConfigError, safe_git_control.SafeGitOperationError) as exc:
            raise _safe_engine_error(exc, root, policy) from exc
        return {
            "rewritten_count": result.rewritten_count,
            "old_head": result.old_head,
            "new_head": result.new_head,
            "branch": result.branch,
        }

    def _authorized(
        self,
        project_root: str | None,
        *,
        access_config: McpAccessConfig,
        write: bool,
    ) -> tuple[Path, safe_git_control.SafeGitPolicy]:
        if not self.enabled:
            raise SafeGitIntegrationError("Safe Git control is disabled by local policy.")
        if self._configuration_error is not None or self._policy is None:
            raise SafeGitIntegrationError(
                "Safe Git control is unavailable because trusted local policy is invalid or incomplete."
            )

        root = project_context.resolve_project_root(
            project_root,
            access_config=access_config,
        )
        if write:
            project_context.require_writable_project_root(root, access_config=access_config)
        return root, self._policy


def load_safe_git_controller(
    *,
    environ: Mapping[str, str] | None = None,
) -> SafeGitController:
    """Load and validate trusted local Git policy once at MCP startup."""

    env = os.environ if environ is None else environ
    if not _explicitly_enabled(env.get(CONTROL_ENABLED_ENV)):
        return SafeGitController(False, False, False)

    try:
        push_enabled = _strict_optional_bool(env.get(PUSH_ENABLED_ENV))
        rewrite_enabled = _strict_optional_bool(env.get(HISTORY_REWRITE_ENABLED_ENV))
        policy = safe_git_control.SafeGitPolicy(
            remote_name=env.get(REMOTE_NAME_ENV) or "",
            expected_remote_url=env.get(EXPECTED_REMOTE_URL_ENV) or "",
            public_name=env.get(PUBLIC_NAME_ENV) or "",
            public_email=env.get(PUBLIC_EMAIL_ENV) or "",
            push_enabled=push_enabled,
            history_rewrite_enabled=rewrite_enabled,
        )
    except (SafeGitIntegrationError, safe_git_control.SafeGitConfigError):
        return SafeGitController(
            True,
            False,
            False,
            _configuration_error=(
                "Safe Git control is unavailable because trusted local policy is invalid or incomplete."
            ),
        )

    return SafeGitController(
        True,
        push_enabled,
        rewrite_enabled,
        _policy=policy,
    )


def _explicitly_enabled(value: str | None) -> bool:
    return isinstance(value, str) and value.strip().casefold() == "true"


def _strict_optional_bool(value: str | None) -> bool:
    if value is None or not value.strip():
        return False
    normalized = value.strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise SafeGitIntegrationError("Trusted Safe Git opt-in flag is invalid.")


def _safe_engine_error(
    error: Exception,
    root: Path,
    policy: safe_git_control.SafeGitPolicy,
) -> SafeGitIntegrationError:
    message = redact_sensitive_text(str(error))
    private_values = sorted(
        (
            str(root),
            policy.remote_name,
            policy.expected_remote_url,
            policy.public_name,
            policy.public_email,
        ),
        key=len,
        reverse=True,
    )
    for private_value in private_values:
        if private_value:
            message = message.replace(private_value, "[local policy]")
    message = _redact_absolute_local_paths(message)
    message = message.strip()[:1000]
    return SafeGitIntegrationError(message or "Safe Git operation was rejected.")


def _redact_absolute_local_paths(message: str) -> str:
    # An unstructured error cannot reliably tell where an unquoted path with
    # spaces ends. Once any absolute local-path form is present, discard the
    # entire diagnostic instead of risking a partially redacted suffix.
    if any(
        pattern.search(message)
        for pattern in (
            _FILE_URI_MARKER_RE,
            _POSIX_ABSOLUTE_PATH_MARKER_RE,
            _WINDOWS_ABSOLUTE_PATH_MARKER_RE,
        )
    ):
        return _LOCAL_PATH_ERROR
    return message
