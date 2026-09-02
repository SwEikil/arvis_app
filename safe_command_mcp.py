from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import project_context
from mcp_access import McpAccessConfig
from safe_commands import (
    AccessMode,
    CwdMode,
    SafeCommandExecutionError,
    SafeCommandRecipe,
    execute_safe_command,
    load_safe_commands,
)


CONTROL_ENABLED_ENV = "ARVIS_SAFE_COMMAND_CONTROL_ENABLED"
CONFIG_PATH_ENV = "ARVIS_SAFE_COMMAND_CONFIG"
HOST_CONTROL_ENABLED_ENV = "ARVIS_SAFE_COMMAND_HOST_CONTROL_ENABLED"


class SafeCommandIntegrationError(ValueError):
    """Controlled, client-safe failure at the MCP policy boundary."""


@dataclass(frozen=True)
class SafeCommandController:
    """Lifecycle-scoped trusted recipe policy for the narrow MCP adapter."""

    enabled: bool
    host_control_enabled: bool
    _recipes: Mapping[str, SafeCommandRecipe] = field(repr=False)
    _configuration_error: str | None = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        return self.enabled and bool(self._recipes) and self._configuration_error is None

    def run(
        self,
        recipe_name: str,
        params: Mapping[str, str],
        project_root: str | None,
        *,
        access_config: McpAccessConfig,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise SafeCommandIntegrationError("Safe-command control is disabled by local policy.")
        if self._configuration_error is not None:
            raise SafeCommandIntegrationError(self._configuration_error)
        if not isinstance(recipe_name, str) or not recipe_name:
            raise SafeCommandIntegrationError("A safe-command recipe name is required.")

        recipe = self._recipes.get(recipe_name)
        if recipe is None:
            raise SafeCommandIntegrationError("The requested safe-command recipe is not authorized.")

        resolved_root: Path | None = None
        needs_project_root = (
            project_root is not None
            or recipe.cwd_mode is CwdMode.PROJECT_ROOT
            or recipe.access is AccessMode.WORKSPACE_WRITE
        )
        if needs_project_root:
            resolved_root = project_context.resolve_project_root(
                project_root,
                access_config=access_config,
            )

        writable_project_root = False
        if recipe.access is AccessMode.WORKSPACE_WRITE:
            if resolved_root is None:  # Defensive: the branch above must resolve one.
                raise SafeCommandIntegrationError("A writable project root is required.")
            project_context.require_writable_project_root(
                resolved_root,
                access_config=access_config,
            )
            writable_project_root = True

        try:
            result = execute_safe_command(
                recipe,
                params,
                project_root=resolved_root,
                writable_project_root=writable_project_root,
                host_control_enabled=self.host_control_enabled,
            )
        except SafeCommandExecutionError as exc:
            raise SafeCommandIntegrationError(str(exc)) from exc

        return {
            "recipe_name": result.recipe_name,
            "access": result.access.value,
            "return_code": result.return_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
            "truncated": result.truncated,
        }


def load_safe_command_controller(
    *,
    environ: Mapping[str, str] | None = None,
) -> SafeCommandController:
    """Load trusted local policy once without allowing client-driven reloads."""

    env = os.environ if environ is None else environ
    enabled = _explicitly_enabled(env.get(CONTROL_ENABLED_ENV))
    host_control_enabled = _explicitly_enabled(env.get(HOST_CONTROL_ENABLED_ENV))
    if not enabled:
        return SafeCommandController(False, host_control_enabled, {})

    raw_config_path = (env.get(CONFIG_PATH_ENV) or "").strip()
    if not raw_config_path:
        return _unavailable_controller(host_control_enabled)

    try:
        config_path = Path(raw_config_path).expanduser()
    except (TypeError, ValueError, RuntimeError):
        return _unavailable_controller(host_control_enabled)
    if not config_path.is_absolute():
        return _unavailable_controller(host_control_enabled)

    try:
        recipes = load_safe_commands(config_path)
    except Exception:  # A malformed local policy must never abort MCP startup.
        return _unavailable_controller(host_control_enabled)
    return SafeCommandController(True, host_control_enabled, recipes)


def _unavailable_controller(host_control_enabled: bool) -> SafeCommandController:
    return SafeCommandController(
        True,
        host_control_enabled,
        {},
        "Safe-command control is unavailable because local trusted policy could not be loaded.",
    )


def _explicitly_enabled(value: str | None) -> bool:
    return isinstance(value, str) and value.strip().casefold() == "true"
