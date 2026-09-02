from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv.main import DotEnv
from dotenv.variables import parse_variables


PROFILE_CODEX = "codex"
PROFILE_CHATGPT = "chatgpt"
SUPPORTED_PROFILES = {PROFILE_CODEX, PROFILE_CHATGPT}

PROFILE_ENV = "ARVIS_MCP_PROFILE"
PROJECT_ROOT_ENV = "ARVIS_MCP_PROJECT_ROOT"
ALLOWED_ROOTS_ENV = "ARVIS_MCP_ALLOWED_ROOTS"
WRITABLE_ROOTS_ENV = "ARVIS_MCP_WRITABLE_ROOTS"


@dataclass(frozen=True)
class McpAccessConfig:
    """Сформована публічна політика MCP із локальними значеннями в env."""

    profile: str
    allowed_roots: tuple[Path, ...]
    writable_roots: tuple[Path, ...]
    default_root: Path | None
    configuration_error: str | None = None

    @property
    def memory_writes_allowed(self) -> bool:
        return self.profile == PROFILE_CODEX and self.configuration_error is None


def load_local_mcp_environment(base_dir: Path | None = None) -> None:
    """Завантажити ignored локальні значення, не замінюючи явний env процесу."""

    root = (base_dir or Path(__file__).resolve().parent).resolve()
    effective = read_local_mcp_environment(root, environ=os.environ)
    for key, value in effective.items():
        os.environ.setdefault(key, value)


def read_local_mcp_environment(
    base_dir: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return MCP startup dotenv values without mutating the process environment.

    The order and ``override=False`` interpolation behavior intentionally match
    :func:`load_local_mcp_environment`: explicit process values win, then
    ``.env.local``, then ``.env``.
    """

    effective, _local_keys = read_local_mcp_environment_with_keys(
        base_dir,
        environ=environ,
    )
    return effective


def read_local_mcp_environment_with_keys(
    base_dir: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], frozenset[str]]:
    """Return effective dotenv values plus keys declared in local dotenv files."""

    root = (base_dir or Path(__file__).resolve().parent).resolve()
    effective = dict(os.environ if environ is None else environ)
    local_keys: set[str] = set()
    for path in (root / ".env.local", root / ".env"):
        resolved_in_file: dict[str, str | None] = {}
        for key, raw_value in DotEnv(path, interpolate=False, override=False).parse():
            local_keys.add(key)
            if raw_value is None:
                resolved_in_file[key] = None
                continue
            interpolation_env: dict[str, str | None] = dict(resolved_in_file)
            interpolation_env.update(effective)
            resolved_in_file[key] = "".join(
                atom.resolve(interpolation_env) for atom in parse_variables(raw_value)
            )
        for key, value in resolved_in_file.items():
            if value is not None:
                effective.setdefault(key, value)
            elif key == WRITABLE_ROOTS_ENV:
                # Unlike an absent variable, a bare writable-roots declaration
                # is an explicit malformed restriction and must survive parsing.
                effective.setdefault(key, "")
    return effective, frozenset(local_keys)


def load_mcp_access_config(
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> McpAccessConfig:
    env = os.environ if environ is None else environ
    working_directory = (cwd or Path.cwd()).resolve()
    profile = (env.get(PROFILE_ENV) or PROFILE_CODEX).strip().lower()

    if profile not in SUPPORTED_PROFILES:
        return McpAccessConfig(
            profile=profile,
            allowed_roots=(),
            writable_roots=(),
            default_root=None,
            configuration_error="Непідтримуваний профіль доступу MCP.",
        )

    configured_allowed = _parse_allowed_roots(env.get(ALLOWED_ROOTS_ENV), working_directory)
    writable_was_configured = WRITABLE_ROOTS_ENV in env
    raw_writable = env.get(WRITABLE_ROOTS_ENV)
    configured_writable = _parse_allowed_roots(raw_writable, working_directory)
    configured_default = _resolve_configured_path(env.get(PROJECT_ROOT_ENV), working_directory)

    if writable_was_configured and not _configured_roots_are_valid(
        raw_writable,
        working_directory,
    ):
        return McpAccessConfig(
            profile=profile,
            allowed_roots=configured_allowed,
            writable_roots=(),
            default_root=None,
            configuration_error=(
                "Явний список writable MCP roots порожній або містить некоректний шлях."
            ),
        )

    if profile == PROFILE_CHATGPT and not configured_allowed:
        return McpAccessConfig(
            profile=profile,
            allowed_roots=(),
            writable_roots=(),
            default_root=None,
            configuration_error="Профіль MCP для ChatGPT потребує явного списку дозволених коренів проєктів.",
        )

    allowed_roots = configured_allowed
    if not allowed_roots:
        allowed_roots = (configured_default or working_directory,)

    writable_roots = configured_writable
    if not writable_roots and profile == PROFILE_CODEX:
        writable_roots = allowed_roots

    if any(_is_filesystem_anchor(root) for root in allowed_roots):
        return McpAccessConfig(
            profile=profile,
            allowed_roots=(),
            writable_roots=(),
            default_root=None,
            configuration_error="Корінь файлової системи не можна використовувати як корінь MCP-проєкту.",
        )

    default_root = configured_default or allowed_roots[0]
    if not any(path_is_within(default_root, allowed_root) for allowed_root in allowed_roots):
        return McpAccessConfig(
            profile=profile,
            allowed_roots=allowed_roots,
            writable_roots=(),
            default_root=None,
            configuration_error="Стандартний корінь MCP-проєкту перебуває поза налаштованим списком дозволених коренів.",
        )

    if any(_is_filesystem_anchor(root) for root in writable_roots):
        return McpAccessConfig(
            profile=profile,
            allowed_roots=allowed_roots,
            writable_roots=(),
            default_root=None,
            configuration_error="Корінь файлової системи не можна використовувати як writable MCP root.",
        )
    if any(not any(path_is_within(root, allowed) for allowed in allowed_roots) for root in writable_roots):
        return McpAccessConfig(
            profile=profile,
            allowed_roots=allowed_roots,
            writable_roots=(),
            default_root=None,
            configuration_error="Writable MCP root має перебувати в дозволеному read root.",
        )

    return McpAccessConfig(
        profile=profile,
        allowed_roots=allowed_roots,
        writable_roots=writable_roots,
        default_root=default_root,
    )


def path_is_within(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def _parse_allowed_roots(value: str | None, cwd: Path) -> tuple[Path, ...]:
    if not isinstance(value, str) or not value.strip():
        return ()

    roots: list[Path] = []
    for item in value.split(os.pathsep):
        resolved = _resolve_configured_path(item, cwd)
        if resolved is not None and resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _configured_roots_are_valid(value: str | None, cwd: Path) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    items = value.split(os.pathsep)
    for item in items:
        if not item.strip():
            return False
        resolved = _resolve_configured_path(item, cwd)
        try:
            if resolved is None or not resolved.is_dir():
                return False
        except (OSError, RuntimeError, ValueError):
            return False
    return True


def _resolve_configured_path(value: str | None, cwd: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def _is_filesystem_anchor(path: Path) -> bool:
    return path == Path(path.anchor)
