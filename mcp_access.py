from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


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
    load_dotenv(root / ".env.local", override=False)
    load_dotenv(root / ".env", override=False)


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
    configured_writable = _parse_allowed_roots(env.get(WRITABLE_ROOTS_ENV), working_directory)
    configured_default = _resolve_configured_path(env.get(PROJECT_ROOT_ENV), working_directory)

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
    if not value or not value.strip():
        return ()

    roots: list[Path] = []
    for item in value.split(os.pathsep):
        resolved = _resolve_configured_path(item, cwd)
        if resolved is not None and resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _resolve_configured_path(value: str | None, cwd: Path) -> Path | None:
    if not value or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        path = cwd / path
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _is_filesystem_anchor(path: Path) -> bool:
    return path == Path(path.anchor)
