from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from mcp_security import redact_sensitive_text


SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 1_000_000
MAX_COMMANDS = 128
MAX_PARAMETERS = 32
MAX_ARGV_TOKENS = 64
MAX_TOKEN_LENGTH = 4_096
MAX_PARAMETER_LENGTH = 1_024
MAX_REGEX_LENGTH = 512
MAX_TIMEOUT_SECONDS = 120
MAX_STDOUT_CHARS = 50_000
MAX_STDERR_CHARS = 16_000

_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]{0,63})\}")
_BLOCKED_EXECUTABLE_NAMES = {
    # Shells and privilege/command trampolines would turn a declarative recipe
    # into an indirect arbitrary-command interface.
    "ash",
    "bash",
    "csh",
    "dash",
    "doas",
    "env",
    "fish",
    "ksh",
    "pkexec",
    "sh",
    "su",
    "sudo",
    "tcsh",
    "zsh",
}
_INHERITED_ENV_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
}


class SafeCommandConfigError(ValueError):
    """Controlled error for an absent or unsafe command recipe configuration."""


class SafeCommandExecutionError(ValueError):
    """Controlled error for a denied or failed safe-command invocation."""


class AccessMode(str, Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    HOST_CONTROL = "host_control"


class CwdMode(str, Enum):
    NONE = "none"
    PROJECT_ROOT = "project_root"
    FIXED = "fixed"


@dataclass(frozen=True)
class ParameterDeclaration:
    name: str
    max_length: int
    choices: tuple[str, ...] | None = None
    regex: str | None = None
    _compiled_regex: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    def validate(self, value: str) -> None:
        if not isinstance(value, str):
            raise SafeCommandExecutionError(f"Parameter {self.name!r} must be a string.")
        _reject_control_characters(value, f"Parameter {self.name!r}", SafeCommandExecutionError)
        if len(value) > self.max_length:
            raise SafeCommandExecutionError(f"Parameter {self.name!r} exceeds its maximum length.")
        if self.choices is not None and value not in self.choices:
            raise SafeCommandExecutionError(f"Parameter {self.name!r} is not an allowed choice.")
        if self._compiled_regex is not None and self._compiled_regex.fullmatch(value) is None:
            raise SafeCommandExecutionError(f"Parameter {self.name!r} does not match its required format.")


@dataclass(frozen=True)
class SafeCommandRecipe:
    name: str
    description: str
    executable: Path
    argv_template: tuple[str, ...]
    parameters: Mapping[str, ParameterDeclaration]
    access: AccessMode
    timeout_seconds: int
    stdout_limit: int
    stderr_limit: int
    cwd_mode: CwdMode
    fixed_cwd: Path | None = None


@dataclass(frozen=True)
class SafeCommandResult:
    recipe_name: str
    access: AccessMode
    return_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float
    truncated: bool


def load_safe_commands(config_path: str | Path) -> Mapping[str, SafeCommandRecipe]:
    """Load a versioned recipe file from the caller's explicit local path."""

    try:
        path = Path(config_path)
    except (TypeError, ValueError) as exc:
        raise SafeCommandConfigError("Safe-command config path is invalid.") from exc
    try:
        if not path.is_file():
            raise SafeCommandConfigError("Safe-command config file does not exist.")
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise SafeCommandConfigError("Safe-command config file is too large.")
        raw = path.read_text(encoding="utf-8")
    except SafeCommandConfigError:
        raise
    except (OSError, UnicodeError) as exc:
        raise SafeCommandConfigError("Safe-command config file could not be read as UTF-8.") from exc

    try:
        document = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                SafeCommandConfigError(f"Invalid JSON constant: {value}.")
            ),
        )
    except SafeCommandConfigError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SafeCommandConfigError("Safe-command config is not valid JSON.") from exc

    root = _require_object(document, "Safe-command config")
    _require_exact_keys(root, required={"version", "commands"}, optional=set(), context="config")
    if type(root["version"]) is not int or root["version"] != SCHEMA_VERSION:
        raise SafeCommandConfigError(f"Safe-command config version must be {SCHEMA_VERSION}.")
    commands = _require_object(root["commands"], "commands")
    if len(commands) > MAX_COMMANDS:
        raise SafeCommandConfigError("Safe-command config contains too many recipes.")

    loaded: dict[str, SafeCommandRecipe] = {}
    for name, value in commands.items():
        if not isinstance(name, str) or _NAME_RE.fullmatch(name) is None:
            raise SafeCommandConfigError(f"Invalid recipe name: {name!r}.")
        loaded[name] = _load_recipe(name, value)
    return MappingProxyType(loaded)


def execute_safe_command(
    recipe: SafeCommandRecipe,
    params: Mapping[str, str],
    *,
    project_root: Path | None = None,
    writable_project_root: bool = False,
    host_control_enabled: bool = False,
) -> SafeCommandResult:
    """Validate parameters and execute one already-resolved declarative recipe."""

    if not isinstance(recipe, SafeCommandRecipe):
        raise SafeCommandExecutionError("A resolved safe-command recipe is required.")
    if not isinstance(params, Mapping):
        raise SafeCommandExecutionError("Command parameters must be a mapping.")
    if recipe.access is AccessMode.WORKSPACE_WRITE and writable_project_root is not True:
        raise SafeCommandExecutionError("Workspace-write command is not authorized for this project root.")
    if recipe.access is AccessMode.HOST_CONTROL and host_control_enabled is not True:
        raise SafeCommandExecutionError("Host-control commands are disabled by caller policy.")

    declared = set(recipe.parameters)
    supplied = set(params)
    if any(not isinstance(key, str) for key in params):
        raise SafeCommandExecutionError("Command parameter names must be strings.")
    missing = declared - supplied
    unknown = supplied - declared
    if missing:
        raise SafeCommandExecutionError(f"Missing command parameter: {sorted(missing)[0]}.")
    if unknown:
        raise SafeCommandExecutionError(f"Unknown command parameter: {sorted(unknown)[0]}.")
    for name, declaration in recipe.parameters.items():
        declaration.validate(params[name])

    cwd, path_redactions = _resolve_execution_cwd(recipe, project_root)
    argv = [str(recipe.executable)]
    for token in recipe.argv_template:
        placeholder = _PLACEHOLDER_RE.fullmatch(token)
        argv.append(params[placeholder.group(1)] if placeholder else token)

    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=min(recipe.timeout_seconds, MAX_TIMEOUT_SECONDS),
            check=False,
            env=_safe_subprocess_env(),
        )
        return_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        return_code = 124
        stdout = _timeout_text(exc.stdout)
        stderr = _timeout_text(exc.stderr)
        if stderr and not stderr.endswith("\n"):
            stderr += "\n"
        stderr += "Command timed out."
    except OSError as exc:
        raise SafeCommandExecutionError("Safe command could not be started.") from exc

    stdout = redact_sensitive_text(_redact_paths(stdout, path_redactions))
    stderr = redact_sensitive_text(_redact_paths(stderr, path_redactions))
    stdout, stdout_truncated = _truncate(stdout, min(recipe.stdout_limit, MAX_STDOUT_CHARS))
    stderr, stderr_truncated = _truncate(stderr, min(recipe.stderr_limit, MAX_STDERR_CHARS))
    return SafeCommandResult(
        recipe_name=recipe.name,
        access=recipe.access,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        duration_seconds=round(time.monotonic() - started, 3),
        truncated=stdout_truncated or stderr_truncated,
    )


def _load_recipe(name: str, value: Any) -> SafeCommandRecipe:
    item = _require_object(value, f"recipe {name!r}")
    _require_exact_keys(
        item,
        required={
            "description",
            "executable",
            "argv",
            "parameters",
            "access",
            "timeout_seconds",
            "output_limits",
            "cwd_mode",
        },
        optional={"fixed_cwd"},
        context=f"recipe {name!r}",
    )

    description = item["description"]
    if not isinstance(description, str) or not description.strip() or len(description) > 500:
        raise SafeCommandConfigError(f"Recipe {name!r} must have a non-empty human description.")
    _reject_control_characters(description, f"Recipe {name!r} description", SafeCommandConfigError)
    executable = _load_executable(name, item["executable"])
    parameters = _load_parameters(name, item["parameters"])
    argv = _load_argv(name, item["argv"], parameters)
    access = _load_enum(AccessMode, item["access"], f"Recipe {name!r} access")
    cwd_mode = _load_enum(CwdMode, item["cwd_mode"], f"Recipe {name!r} cwd_mode")
    fixed_cwd = _load_fixed_cwd(name, cwd_mode, item.get("fixed_cwd"))
    timeout = _bounded_positive_int(
        item["timeout_seconds"], MAX_TIMEOUT_SECONDS, f"Recipe {name!r} timeout_seconds"
    )
    stdout_limit, stderr_limit = _load_output_limits(name, item["output_limits"])
    return SafeCommandRecipe(
        name=name,
        description=description,
        executable=executable,
        argv_template=argv,
        parameters=MappingProxyType(parameters),
        access=access,
        timeout_seconds=timeout,
        stdout_limit=stdout_limit,
        stderr_limit=stderr_limit,
        cwd_mode=cwd_mode,
        fixed_cwd=fixed_cwd,
    )


def _load_executable(recipe_name: str, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise SafeCommandConfigError(f"Recipe {recipe_name!r} executable must be a path string.")
    _reject_control_characters(value, f"Recipe {recipe_name!r} executable", SafeCommandConfigError)
    path = Path(value)
    if not path.is_absolute():
        raise SafeCommandConfigError(f"Recipe {recipe_name!r} executable must be absolute.")
    try:
        resolved = path.resolve(strict=True)
        mode = resolved.stat().st_mode
    except OSError as exc:
        raise SafeCommandConfigError(f"Recipe {recipe_name!r} executable is unavailable.") from exc
    if (
        path.name.casefold() in _BLOCKED_EXECUTABLE_NAMES
        or resolved.name.casefold() in _BLOCKED_EXECUTABLE_NAMES
    ):
        raise SafeCommandConfigError(f"Recipe {recipe_name!r} uses a blocked command trampoline.")
    if not stat.S_ISREG(mode) or not os.access(resolved, os.X_OK):
        raise SafeCommandConfigError(f"Recipe {recipe_name!r} executable must be an executable regular file.")
    return resolved


def _load_parameters(recipe_name: str, value: Any) -> dict[str, ParameterDeclaration]:
    items = _require_object(value, f"Recipe {recipe_name!r} parameters")
    if len(items) > MAX_PARAMETERS:
        raise SafeCommandConfigError(f"Recipe {recipe_name!r} has too many parameters.")
    result: dict[str, ParameterDeclaration] = {}
    for name, raw in items.items():
        if not isinstance(name, str) or _NAME_RE.fullmatch(name) is None:
            raise SafeCommandConfigError(f"Recipe {recipe_name!r} has invalid parameter name {name!r}.")
        declaration = _require_object(raw, f"Parameter {name!r}")
        _require_exact_keys(
            declaration,
            required={"max_length"},
            optional={"choices", "regex"},
            context=f"parameter {name!r}",
        )
        max_length = _strict_positive_int(declaration["max_length"], f"Parameter {name!r} max_length")
        if max_length > MAX_PARAMETER_LENGTH:
            raise SafeCommandConfigError(f"Parameter {name!r} max_length exceeds the global cap.")
        choices = _load_choices(name, declaration.get("choices"), max_length)
        regex, compiled = _load_regex(name, declaration.get("regex"))
        if choices is None and regex is None:
            raise SafeCommandConfigError(f"Parameter {name!r} requires choices and/or regex.")
        result[name] = ParameterDeclaration(name, max_length, choices, regex, compiled)
    return result


def _load_choices(name: str, value: Any, max_length: int) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value or len(value) > 100:
        raise SafeCommandConfigError(f"Parameter {name!r} choices must be a non-empty JSON array.")
    if any(not isinstance(choice, str) for choice in value):
        raise SafeCommandConfigError(f"Parameter {name!r} choices must contain only strings.")
    choices = tuple(value)
    if len(set(choices)) != len(choices):
        raise SafeCommandConfigError(f"Parameter {name!r} choices must be unique.")
    for choice in choices:
        _reject_control_characters(choice, f"Parameter {name!r} choice", SafeCommandConfigError)
        if len(choice) > max_length:
            raise SafeCommandConfigError(f"Parameter {name!r} choice exceeds max_length.")
    return choices


def _load_regex(name: str, value: Any) -> tuple[str | None, re.Pattern[str] | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or not value or len(value) > MAX_REGEX_LENGTH:
        raise SafeCommandConfigError(f"Parameter {name!r} regex is invalid or too long.")
    _reject_control_characters(value, f"Parameter {name!r} regex", SafeCommandConfigError)
    _reject_risky_regex(name, value)
    try:
        return value, re.compile(value)
    except re.error as exc:
        raise SafeCommandConfigError(f"Parameter {name!r} regex is invalid.") from exc


def _reject_risky_regex(name: str, value: str) -> None:
    risky_shapes = (
        r"\\[1-9]",
        r"\((?:[^()]|\\.)*[+*?{][^()]*\)\s*[+*?{]",
        r"\((?:[^()]|\\.)*\|(?:[^()]|\\.)*\)\s*[+*?{]",
        r"(?:\.\*){2,}|(?:\.\+){2,}",
    )
    if any(re.search(shape, value) for shape in risky_shapes):
        raise SafeCommandConfigError(f"Parameter {name!r} regex uses a risky pattern shape.")


def _load_argv(
    recipe_name: str,
    value: Any,
    parameters: Mapping[str, ParameterDeclaration],
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_ARGV_TOKENS:
        raise SafeCommandConfigError(f"Recipe {recipe_name!r} argv must be a bounded JSON array.")
    used: list[str] = []
    tokens: list[str] = []
    for token in value:
        if not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH:
            raise SafeCommandConfigError(f"Recipe {recipe_name!r} argv tokens must be bounded strings.")
        _reject_control_characters(token, f"Recipe {recipe_name!r} argv token", SafeCommandConfigError)
        placeholder = _PLACEHOLDER_RE.fullmatch(token)
        if placeholder:
            parameter_name = placeholder.group(1)
            if parameter_name not in parameters:
                raise SafeCommandConfigError(
                    f"Recipe {recipe_name!r} placeholder {parameter_name!r} is not declared."
                )
            used.append(parameter_name)
        elif "{" in token or "}" in token:
            raise SafeCommandConfigError(
                f"Recipe {recipe_name!r} placeholders must occupy an entire argv token."
            )
        tokens.append(token)
    if len(set(used)) != len(used):
        raise SafeCommandConfigError(f"Recipe {recipe_name!r} uses a parameter more than once.")
    unused = set(parameters) - set(used)
    if unused:
        raise SafeCommandConfigError(f"Recipe {recipe_name!r} declares an unused parameter.")
    return tuple(tokens)


def _load_fixed_cwd(recipe_name: str, mode: CwdMode, value: Any) -> Path | None:
    if mode is CwdMode.FIXED:
        if not isinstance(value, str) or not value:
            raise SafeCommandConfigError(f"Recipe {recipe_name!r} fixed cwd requires fixed_cwd.")
        _reject_control_characters(value, f"Recipe {recipe_name!r} fixed_cwd", SafeCommandConfigError)
        path = Path(value)
        if not path.is_absolute():
            raise SafeCommandConfigError(f"Recipe {recipe_name!r} fixed_cwd must be absolute.")
        return path
    if value is not None:
        raise SafeCommandConfigError(f"Recipe {recipe_name!r} fixed_cwd is only valid with cwd_mode=fixed.")
    return None


def _load_output_limits(recipe_name: str, value: Any) -> tuple[int, int]:
    limits = _require_object(value, f"Recipe {recipe_name!r} output_limits")
    _require_exact_keys(
        limits,
        required={"stdout_chars", "stderr_chars"},
        optional=set(),
        context=f"recipe {recipe_name!r} output_limits",
    )
    return (
        _bounded_positive_int(
            limits["stdout_chars"], MAX_STDOUT_CHARS, f"Recipe {recipe_name!r} stdout_chars"
        ),
        _bounded_positive_int(
            limits["stderr_chars"], MAX_STDERR_CHARS, f"Recipe {recipe_name!r} stderr_chars"
        ),
    )


def _resolve_execution_cwd(
    recipe: SafeCommandRecipe, project_root: Path | None
) -> tuple[Path | None, tuple[tuple[str, str], ...]]:
    redactions: list[tuple[str, str]] = []
    if project_root is not None:
        if not isinstance(project_root, Path) or not project_root.is_absolute():
            raise SafeCommandExecutionError("Resolved project root must be an absolute Path.")
        try:
            resolved_project_root = project_root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SafeCommandExecutionError("Resolved project root is unavailable.") from exc
        if not resolved_project_root.is_dir():
            raise SafeCommandExecutionError("Resolved project root must be a directory.")
        redactions.extend(((str(project_root), "<PROJECT_ROOT>"), (str(resolved_project_root), "<PROJECT_ROOT>")))
    else:
        resolved_project_root = None

    if recipe.cwd_mode is CwdMode.NONE:
        return None, tuple(redactions)
    if recipe.cwd_mode is CwdMode.PROJECT_ROOT:
        if resolved_project_root is None:
            raise SafeCommandExecutionError("This command requires a resolved project root.")
        return resolved_project_root, tuple(redactions)

    fixed_cwd = recipe.fixed_cwd
    if fixed_cwd is None:
        raise SafeCommandExecutionError("Fixed command cwd is not configured.")
    try:
        resolved_fixed_cwd = fixed_cwd.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SafeCommandExecutionError("Configured fixed command cwd is unavailable.") from exc
    if not resolved_fixed_cwd.is_dir():
        raise SafeCommandExecutionError("Configured fixed command cwd must be a directory.")
    redactions.extend(((str(fixed_cwd), "<FIXED_CWD>"), (str(resolved_fixed_cwd), "<FIXED_CWD>")))
    return resolved_fixed_cwd, tuple(redactions)


def _safe_subprocess_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in _INHERITED_ENV_KEYS}


def _redact_paths(text: str, replacements: tuple[tuple[str, str], ...]) -> str:
    for path, replacement in sorted(set(replacements), key=lambda item: len(item[0]), reverse=True):
        if path:
            text = text.replace(path, replacement)
    return text


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    return value[:limit], len(value) > limit


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SafeCommandConfigError(f"Duplicate JSON object key: {key!r}.")
        result[key] = value
    return result


def _require_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SafeCommandConfigError(f"{context} must be a JSON object.")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], *, required: set[str], optional: set[str], context: str
) -> None:
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise SafeCommandConfigError(f"Missing key in {context}: {sorted(missing)[0]}.")
    if unknown:
        raise SafeCommandConfigError(f"Unknown key in {context}: {sorted(unknown)[0]}.")


def _load_enum(enum_type: type[AccessMode] | type[CwdMode], value: Any, context: str) -> Any:
    if not isinstance(value, str):
        raise SafeCommandConfigError(f"{context} must be a string enum value.")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise SafeCommandConfigError(f"{context} is unsupported.") from exc


def _strict_positive_int(value: Any, context: str) -> int:
    if type(value) is not int or value < 1:
        raise SafeCommandConfigError(f"{context} must be a positive integer.")
    return value


def _bounded_positive_int(value: Any, cap: int, context: str) -> int:
    return min(_strict_positive_int(value, context), cap)


def _reject_control_characters(value: str, context: str, error_type: type[ValueError]) -> None:
    if any(unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in value):
        raise error_type(f"{context} contains control characters.")
