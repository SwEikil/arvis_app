from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

DoctorStatus = Literal["ok", "warn", "fail", "info"]


@dataclass(frozen=True)
class DoctorCheck:
    status: DoctorStatus
    category: str
    title: str
    details: str = ""
    fix: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class DoctorOptions:
    json_output: bool = False
    verbose: bool = False
    strict: bool = False
    fix: bool = False
    no_color: bool = False


IMPORTANT_FILES = ("main.py", "config.py", "requirements.txt", "README.md")
SAFE_LOCAL_DIRS = ("logs", ".cache", ".runtime")
REQUIRED_GITIGNORE_PATTERNS = (
    ".env",
    ".env.local",
    "__pycache__/",
    "*.pyc",
    ".venv/",
    "venv/",
    "*.db",
    "*.sqlite",
    "logs/",
    ".cache/",
    ".runtime/",
    "models/",
    "ollama-models/",
    "*.gguf",
    "*.safetensors",
    "*.bin",
    "*.pt",
    "*.pth",
)
CORE_MODULES = (
    "config",
    "ollama_client",
    "intent_parser",
    "intent_resolver",
    "command_router",
    "response_renderer",
)
KNOWN_ENV_KEYS = {
    "USER_NAME",
    "OLLAMA_HOST",
    "ARVIS_MODEL",
    "MUSIC_FOLDER",
    "DOWNLOADS_FOLDER",
    "STEAM_COMMAND",
    "SPOTIFY_COMMAND",
    "BRAVE_COMMAND",
    "DISCORD_COMMAND",
    "TELEGRAM_COMMAND",
    "MINECRAFT_SERVER_ENABLED",
    "MINECRAFT_SERVER_KEY",
    "MINECRAFT_SERVER_NAME",
    "MINECRAFT_SERVER_CWD",
    "MINECRAFT_SERVER_COMMAND",
}
SECRET_NAME_RE = re.compile(
    r"(secret|token|password|passwd|credential|api[_-]?key|private[_-]?key|auth|authorization|cookie|session)",
    re.IGNORECASE,
)
SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]{8,}|Basic\s+[A-Za-z0-9+/=]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)
KEY_VALUE_SECRET_RE = re.compile(
    r"\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|API_KEY|PRIVATE_KEY|AUTH|COOKIE|SESSION)[A-Z0-9_]*)=([^\s;]+)",
    re.IGNORECASE,
)
AUTH_HEADER_RE = re.compile(r"\b(Authorization|Cookie|Set-Cookie):\s*([^\n\r]+)", re.IGNORECASE)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LOCAL_MODEL_FILE_SUFFIXES = (".gguf", ".safetensors", ".bin", ".pt", ".pth")


def parse_doctor_args(argv: list[str]) -> DoctorOptions:
    parser = argparse.ArgumentParser(prog="python main.py doctor", description="Run Arvis health checks.")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print machine-readable JSON.")
    parser.add_argument("--verbose", action="store_true", help="Show extra diagnostic info.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as a failing doctor result.")
    parser.add_argument("--fix", action="store_true", help="Create only safe local runtime directories when missing.")
    parser.add_argument("--no-color", action="store_true", help="Disable colored text output.")
    args = parser.parse_args(argv)
    return DoctorOptions(
        json_output=args.json_output,
        verbose=args.verbose,
        strict=args.strict,
        fix=args.fix,
        no_color=args.no_color,
    )


def doctor_exit_code(checks: list[DoctorCheck], options: DoctorOptions) -> int:
    if any(check.status == "fail" for check in checks):
        return 1
    if options.strict and any(check.status == "warn" for check in checks):
        return 1
    return 0


def run_doctor(options: DoctorOptions | None = None, project_root: Path | None = None) -> list[DoctorCheck]:
    options = options or DoctorOptions()
    root = resolve_project_root(project_root)
    env_file_values = _read_env_file(root / ".env")
    env = {**env_file_values, **{key: value for key, value in os.environ.items() if key in KNOWN_ENV_KEYS}}

    checks: list[DoctorCheck] = []
    checks.extend(check_project_runtime(root, options))
    checks.extend(check_local_config(root, env, options))
    checks.extend(check_privacy_safety(root, options))
    checks.extend(check_ollama_backend(env, options))
    checks.extend(check_voice_audio(options))
    checks.extend(check_action_readiness(env, options))
    checks.extend(check_storage(root, options))
    checks.extend(check_git_safety(root))
    return checks


def render_text_report(checks: list[DoctorCheck], options: DoctorOptions | None = None) -> str:
    options = options or DoctorOptions()
    lines: list[str] = []
    for check in sanitize_checks(checks):
        lines.append(f"[{check.status.upper()}] {check.category}: {check.title}")
        if check.details and (options.verbose or check.status in {"warn", "fail"}):
            lines.append(f"Details: {check.details}")
        if check.fix and check.status in {"warn", "fail"}:
            lines.append(f"Fix: {check.fix}")

    summary = summarize_checks(checks)
    lines.extend(
        [
            "",
            "Doctor summary:",
            f"- OK: {summary['ok']}",
            f"- Warnings: {summary['warn']}",
            f"- Failures: {summary['fail']}",
            f"- Info: {summary['info']}",
        ]
    )
    if options.strict and summary["warn"] > 0:
        lines.append("- Strict: warnings make the doctor command fail")
    return "\n".join(lines)


def render_json_report(checks: list[DoctorCheck], options: DoctorOptions | None = None) -> str:
    options = options or DoctorOptions()
    payload = {
        "summary": summarize_checks(checks),
        "strict": options.strict,
        "checks": [check.to_dict() for check in sanitize_checks(checks)],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def resolve_project_root(project_root: Path | None = None) -> Path:
    if project_root is not None:
        return project_root.resolve()

    cwd = Path.cwd().resolve()
    candidates = [
        cwd,
        cwd / "arvis_app",
        Path(__file__).resolve().parent,
    ]
    for candidate in candidates:
        if all((candidate / name).exists() for name in IMPORTANT_FILES):
            return candidate
    return cwd


def summarize_checks(checks: list[DoctorCheck]) -> dict[str, int]:
    summary = {"ok": 0, "warn": 0, "fail": 0, "info": 0}
    for check in checks:
        summary[check.status] += 1
    return summary


def sanitize_checks(checks: list[DoctorCheck]) -> list[DoctorCheck]:
    return [
        DoctorCheck(
            check.status,
            sanitize_text(check.category),
            sanitize_text(check.title),
            sanitize_text(check.details),
            sanitize_text(check.fix),
        )
        for check in checks
    ]


def sanitize_text(value: str) -> str:
    if not value:
        return value

    text = ANSI_ESCAPE_RE.sub("", value)
    text = AUTH_HEADER_RE.sub(lambda match: f"{match.group(1)}: [set]", text)
    text = KEY_VALUE_SECRET_RE.sub(lambda match: f"{match.group(1)}={redact_value(match.group(1), match.group(2))}", text)
    text = SECRET_VALUE_RE.sub("[set]", text)
    return _redact_personal_paths(text)


def check_project_runtime(root: Path, options: DoctorOptions) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    version = sys.version_info
    if version >= (3, 11):
        checks.append(DoctorCheck("ok", "Runtime", f"Python {version.major}.{version.minor} found"))
    else:
        checks.append(
            DoctorCheck(
                "fail",
                "Runtime",
                f"Python {version.major}.{version.minor} found",
                fix="Use Python 3.11 or newer for this project.",
            )
        )

    missing_files = [name for name in IMPORTANT_FILES if not (root / name).exists()]
    if missing_files:
        checks.append(
            DoctorCheck(
                "fail",
                "Project",
                "Project root does not look complete",
                details=f"Missing files: {', '.join(missing_files)}",
                fix="Run doctor from the arvis_app project root.",
            )
        )
    else:
        checks.append(DoctorCheck("ok", "Project", "Project root files found"))

    checks.extend(_check_requirements(root / "requirements.txt"))
    checks.extend(_check_core_imports(options))
    return checks


def check_local_config(root: Path, env: dict[str, str], options: DoctorOptions) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    env_path = root / ".env"
    if env_path.exists():
        checks.append(DoctorCheck("ok", "Config", ".env found"))
    else:
        checks.append(
            DoctorCheck(
                "info",
                "Config",
                ".env not found; safe defaults will be used",
                fix="Copy .env.example to .env if you want local overrides.",
            )
        )

    example_path = root / ".env.example"
    if example_path.exists():
        checks.append(DoctorCheck("ok", "Config", ".env.example found"))
    else:
        status: DoctorStatus = "ok" if options.fix and _write_safe_env_example(example_path) else "warn"
        checks.append(
            DoctorCheck(
                status,
                "Config",
                ".env.example is missing" if status == "warn" else ".env.example created",
                fix="Create .env.example with placeholder values only.",
            )
        )

    unknown_keys = sorted(set(env) - KNOWN_ENV_KEYS)
    if unknown_keys and options.verbose:
        checks.append(
            DoctorCheck(
                "info",
                "Config",
                "Unknown local env keys are present",
                details=", ".join(redact_value(key, key) for key in unknown_keys),
            )
        )

    ollama_host = env.get("OLLAMA_HOST", _default_ollama_host())
    parsed_host = urlparse(ollama_host)
    if parsed_host.scheme in {"http", "https"} and parsed_host.netloc:
        checks.append(DoctorCheck("ok", "Config", "OLLAMA_HOST is a valid URL", details=_redacted_url(ollama_host)))
    else:
        checks.append(
            DoctorCheck(
                "fail",
                "Config",
                "OLLAMA_HOST is invalid",
                details="OLLAMA_HOST is set but is not an HTTP URL.",
                fix="Set OLLAMA_HOST to a value like http://127.0.0.1:11434.",
            )
        )

    model = env.get("ARVIS_MODEL", _default_arvis_model()).strip()
    if model:
        checks.append(DoctorCheck("ok", "Config", "ARVIS_MODEL is set", details=redact_value("ARVIS_MODEL", model)))
    else:
        checks.append(
            DoctorCheck(
                "fail",
                "Config",
                "ARVIS_MODEL is empty",
                fix="Set ARVIS_MODEL in .env or remove it to use the default model name.",
            )
        )

    for key in ("MUSIC_FOLDER", "DOWNLOADS_FOLDER"):
        value = env.get(key, "").strip()
        if not value:
            checks.append(DoctorCheck("info", "Config", f"{key} is optional and not configured"))
            continue
        path = Path(value).expanduser()
        if path.exists():
            checks.append(DoctorCheck("ok", "Config", f"{key} exists", details=f"{key}={display_path(path)}"))
        else:
            checks.append(
                DoctorCheck(
                    "warn",
                    "Config",
                    f"{key} is set but path does not exist",
                    details=f"{key}={display_path(path)}",
                    fix=f"Update {key} in .env or create the directory.",
                )
            )

    checks.extend(_check_minecraft_config(env))
    return checks


def check_privacy_safety(root: Path, options: DoctorOptions) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    gitignore = root / ".gitignore"
    gitignore_text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if not gitignore.exists():
        checks.append(
            DoctorCheck(
                "warn",
                "Privacy",
                ".gitignore is missing",
                fix="Create .gitignore and ignore local config, logs, caches, runtime state, and venv files.",
            )
        )
    else:
        missing = [pattern for pattern in REQUIRED_GITIGNORE_PATTERNS if pattern not in gitignore_text]
        if missing:
            checks.append(
                DoctorCheck(
                    "warn",
                    "Privacy",
                    ".gitignore is missing local/private patterns",
                    details=f"Missing patterns: {', '.join(missing)}",
                    fix="Add the missing patterns to .gitignore.",
                )
            )
        else:
            checks.append(DoctorCheck("ok", "Privacy", ".gitignore covers local config and runtime files"))

    example = root / ".env.example"
    if example.exists():
        example_text = example.read_text(encoding="utf-8", errors="replace")
        if _text_has_secret_like_value(example_text):
            checks.append(
                DoctorCheck(
                    "fail",
                    "Privacy",
                    ".env.example appears to contain a real secret",
                    fix="Replace real values in .env.example with safe placeholders.",
                )
            )
        elif _text_has_absolute_home_path(example_text):
            checks.append(
                DoctorCheck(
                    "warn",
                    "Privacy",
                    ".env.example appears to contain a personal home path",
                    fix="Use placeholders such as /path/to/music in .env.example.",
                )
            )
        else:
            checks.append(DoctorCheck("ok", "Privacy", ".env.example uses safe placeholder-style values"))

    return checks


def check_ollama_backend(env: dict[str, str], options: DoctorOptions) -> list[DoctorCheck]:
    try:
        import requests
    except ImportError:
        return [
            DoctorCheck(
                "fail",
                "Ollama",
                "requests package is missing",
                fix="Install dependencies with `.venv/bin/python -m pip install -r requirements.txt`.",
            )
        ]

    host = env.get("OLLAMA_HOST", _default_ollama_host()).rstrip("/")
    model = env.get("ARVIS_MODEL", _default_arvis_model()).strip() or _default_arvis_model()
    parsed = urlparse(host)
    hostname = parsed.hostname or ""
    if not _is_local_or_private_host(hostname):
        return [
            DoctorCheck(
                "warn",
                "Ollama",
                "OLLAMA_HOST is not localhost or a private network host",
                details=_redacted_url(host),
            fix=f"Use a local Ollama URL such as {_default_ollama_host()} for offline-first operation.",
            )
        ]

    try:
        response = requests.get(f"{host}/api/tags", timeout=1.5)
    except requests.RequestException:
        return [
            DoctorCheck(
                "warn",
                "Ollama",
                "Ollama backend is offline or unreachable",
                details=_redacted_url(host),
                fix="Start Ollama or update OLLAMA_HOST in .env. Internet access is not required.",
            ),
            DoctorCheck("info", "Offline mode", "Internet access is not required for local mode"),
        ]

    if response.status_code >= 400:
        return [
            DoctorCheck(
                "warn",
                "Ollama",
                f"Ollama returned HTTP {response.status_code}",
                details=_redacted_url(host),
                fix="Check that Ollama is running and serving /api/tags.",
            )
        ]

    try:
        data = response.json()
    except ValueError:
        return [
            DoctorCheck(
                "warn",
                "Ollama",
                "Ollama /api/tags did not return JSON",
                fix="Check the configured OLLAMA_HOST.",
            )
        ]

    names = {item.get("name", "").split(":")[0] for item in data.get("models", []) if isinstance(item, dict)}
    full_names = {item.get("name", "") for item in data.get("models", []) if isinstance(item, dict)}
    if model in names or model in full_names:
        return [DoctorCheck("ok", "Ollama", "Ollama is reachable and configured model is available")]
    return [
        DoctorCheck(
            "fail",
            "Ollama",
            "Ollama is reachable but configured model was not found",
            details=f"ARVIS_MODEL={redact_value('ARVIS_MODEL', model)}",
            fix=f"Run `ollama list`, set ARVIS_MODEL to an installed model, or create it with `ollama create {model} -f Modelfile`.",
        )
    ]


def check_voice_audio(options: DoctorOptions) -> list[DoctorCheck]:
    checks = [
        DoctorCheck("info", "Voice", "STT backend is optional and not configured"),
        DoctorCheck("info", "Voice", "TTS backend is optional and not configured"),
    ]
    for command, title in (("playerctl", "playerctl"), ("wpctl", "wpctl")):
        if shutil.which(command):
            checks.append(DoctorCheck("ok", "Desktop", f"{title} found", details=command))
        else:
            checks.append(
                DoctorCheck(
                    "warn",
                    "Desktop",
                    f"{title} not found",
                    details=command,
                    fix=f"Install or enable `{command}` if you want the related audio/media actions.",
                )
            )
    if shutil.which("flatpak"):
        checks.append(DoctorCheck("ok", "Desktop", "flatpak found", details="flatpak"))
    else:
        checks.append(
            DoctorCheck(
                "warn",
                "Desktop",
                "flatpak not found, Flatpak app fallbacks may fail",
                fix="Install Flatpak if you use Flatpak app fallback commands.",
            )
        )
    return checks


def check_action_readiness(env: dict[str, str], options: DoctorOptions) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    try:
        from actions.apps import APP_WHITELIST
    except Exception as exc:
        checks.append(
            DoctorCheck(
                "fail",
                "Actions",
                "app whitelist failed to import",
                details=f"{type(exc).__name__}: {exc}",
                fix="Fix actions.apps import before using app launch actions.",
            )
        )
    else:
        if APP_WHITELIST:
            checks.append(DoctorCheck("ok", "Actions", f"app whitelist has {len(APP_WHITELIST)} targets"))
        else:
            checks.append(DoctorCheck("fail", "Actions", "app whitelist is empty", fix="Add safe app targets to APP_COMMANDS."))

    for module_name in ("command_router", "actions.apps", "actions.media", "actions.volume", "actions.minecraft_server"):
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            checks.append(
                DoctorCheck(
                    "fail",
                    "Actions",
                    f"{module_name} failed to import",
                    details=f"{type(exc).__name__}: {exc}",
                    fix="Fix the import error before using local actions.",
                )
            )
        else:
            checks.append(DoctorCheck("ok", "Actions", f"{module_name} imports"))

    app_commands = _app_commands_from_env(env)
    for app_name, command_info in app_commands.items():
        if command_info["parse_error"]:
            checks.append(
                DoctorCheck(
                    "warn",
                    "Actions",
                    f"{app_name} command from .env is not parseable",
                    fix=f"Fix {app_name.upper()}_COMMAND in .env so shlex can parse it as an argv list.",
                )
            )
        elif command_info["commands_parseable"]:
            checks.append(DoctorCheck("ok", "Actions", f"{app_name} fallback/configured commands are parseable argv lists"))

    for app_name, command_info in app_commands.items():
        commands = command_info["commands"]
        explicit = command_info["explicit"]
        if any(_command_available(command) for command in commands):
            checks.append(DoctorCheck("ok", "Apps", f"{app_name} launch command appears available"))
        elif explicit:
            checks.append(
                DoctorCheck(
                    "warn",
                    "Apps",
                    f"{app_name} launch command is explicitly configured but was not found",
                    details=f"Checked {len(commands)} configured/fallback command(s).",
                    fix=f"Install {app_name} or update {app_name.upper()}_COMMAND in .env.",
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    "info",
                    "Apps",
                    f"{app_name} launch command is optional and was not found",
                    details=f"Checked {len(commands)} configured/fallback command(s).",
                )
            )

    if _env_bool(env.get("MINECRAFT_SERVER_ENABLED"), default=False):
        cwd = env.get("MINECRAFT_SERVER_CWD", "").strip()
        command = _parse_command(env.get("MINECRAFT_SERVER_COMMAND", ""))
        if not cwd or not command:
            checks.append(
                DoctorCheck(
                    "fail",
                    "Minecraft",
                    "Minecraft server is enabled but not fully configured",
                    fix="Set MINECRAFT_SERVER_CWD and MINECRAFT_SERVER_COMMAND in .env.",
                )
            )
        elif not Path(cwd).expanduser().exists():
            checks.append(
                DoctorCheck(
                    "fail",
                    "Minecraft",
                    "Minecraft server cwd does not exist",
                    details=f"MINECRAFT_SERVER_CWD={display_path(Path(cwd).expanduser())}",
                    fix="Update MINECRAFT_SERVER_CWD in .env or create the server directory.",
                )
            )
        else:
            checks.append(DoctorCheck("ok", "Minecraft", "Minecraft server config is present"))
    else:
        checks.append(DoctorCheck("info", "Minecraft", "Minecraft server integration is optional and disabled"))
    return checks


def check_storage(root: Path, options: DoctorOptions) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for dirname in SAFE_LOCAL_DIRS:
        path = root / dirname
        if not path.exists() and options.fix:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                checks.append(
                    DoctorCheck(
                        "fail",
                        "Storage",
                        f"{dirname}/ could not be created",
                        details=str(exc),
                        fix=f"Create {dirname}/ manually and ensure it is writable.",
                    )
                )
                continue
        if path.exists():
            if path.is_dir() and os.access(path, os.W_OK):
                checks.append(DoctorCheck("ok", "Storage", f"{dirname}/ is writable"))
            else:
                checks.append(
                    DoctorCheck(
                        "fail",
                        "Storage",
                        f"{dirname}/ is not writable",
                        fix=f"Fix permissions for {dirname}/.",
                    )
                )
        else:
            checks.append(
                DoctorCheck(
                    "warn",
                    "Storage",
                    f"{dirname}/ is missing",
                    fix=f"Run `python main.py doctor --fix` to create {dirname}/.",
                )
            )
    return checks


def check_git_safety(root: Path) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    tracked = _git_tracked_files(root)
    if tracked is None:
        return [DoctorCheck("info", "Git", "Git repository was not detected")]

    tracked_set = set(tracked)
    secret_files = sorted(
        path
        for path in tracked_set
        if path in {".env", ".env.local"} or SECRET_NAME_RE.search(Path(path).name)
    )
    model_files = sorted(path for path in tracked_set if path.lower().endswith(LOCAL_MODEL_FILE_SUFFIXES))

    if secret_files:
        checks.append(
            DoctorCheck(
                "fail",
                "Git",
                "Secret-like local files are tracked",
                details=", ".join(secret_files),
                fix="Remove local secret files from git tracking and keep placeholders only.",
            )
        )
    else:
        checks.append(DoctorCheck("ok", "Git", "No tracked .env or secret-like files detected"))

    if model_files:
        checks.append(
            DoctorCheck(
                "warn",
                "Git",
                "Large local model files appear tracked",
                details=", ".join(model_files[:5]),
                fix="Move model files outside the repo or ignore/remove them from git tracking.",
            )
        )
    else:
        checks.append(DoctorCheck("ok", "Git", "No tracked local model files detected"))
    return checks


def run_cli(argv: list[str] | None = None, project_root: Path | None = None) -> int:
    options = parse_doctor_args(argv or [])
    try:
        checks = run_doctor(options, project_root=project_root)
        output = render_json_report(checks, options) if options.json_output else render_text_report(checks, options)
        print(output)
        return doctor_exit_code(checks, options)
    except Exception as exc:
        if options.json_output:
            print(json.dumps({"error": f"Doctor failed: {type(exc).__name__}"}, ensure_ascii=False))
        else:
            print(f"[FAIL] Doctor: unexpected internal error\nDetails: {type(exc).__name__}: {exc}")
        return 2


def redact_value(name: str, value: str) -> str:
    if not value:
        return ""
    if SECRET_NAME_RE.search(name) or SECRET_VALUE_RE.search(value):
        if len(value) <= 8:
            return "[set]"
        return f"{value[:3]}...{value[-4:]}"
    return value


def _redact_personal_paths(value: str) -> str:
    home = str(Path.home())
    redacted = value
    if home and home != "/":
        redacted = redacted.replace(home, "~")
    redacted = re.sub(r"/home/[^/\s:;]+", "~", redacted)
    redacted = re.sub(r"/Users/[^/\s:;]+", "~", redacted)
    return redacted


def _default_ollama_host() -> str:
    try:
        from config import DEFAULT_OLLAMA_HOST
    except Exception:
        return "http://127.0.0.1:11434"
    return DEFAULT_OLLAMA_HOST


def _default_arvis_model() -> str:
    try:
        from config import DEFAULT_ARVIS_MODEL
    except Exception:
        return "local-model"
    return DEFAULT_ARVIS_MODEL


def display_path(path: Path) -> str:
    try:
        resolved = path.expanduser()
        home = Path.home()
        return "~" + str(resolved).removeprefix(str(home)) if resolved.is_absolute() and str(resolved).startswith(str(home)) else str(resolved)
    except RuntimeError:
        return "[path set]"


def _check_requirements(requirements_path: Path) -> list[DoctorCheck]:
    if not requirements_path.exists():
        return [
            DoctorCheck(
                "fail",
                "Dependencies",
                "requirements.txt is missing",
                fix="Restore requirements.txt and install dependencies in the local venv.",
            )
        ]

    checks: list[DoctorCheck] = []
    requirements = [
        line.strip()
        for line in requirements_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    import_names = {"python-dotenv": "dotenv"}
    missing: list[str] = []
    for requirement in requirements:
        package = re.split(r"[<>=!~]", requirement, maxsplit=1)[0].strip()
        import_name = import_names.get(package, package.replace("-", "_"))
        if importlib.util.find_spec(import_name) is None:
            missing.append(package)
    if missing:
        checks.append(
            DoctorCheck(
                "fail",
                "Dependencies",
                "Required Python packages are missing",
                details=", ".join(missing),
                fix="Install dependencies with `.venv/bin/python -m pip install -r requirements.txt`.",
            )
        )
    else:
        checks.append(DoctorCheck("ok", "Dependencies", "Required Python packages import"))
    return checks


def _check_core_imports(options: DoctorOptions) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for module_name in CORE_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            checks.append(
                DoctorCheck(
                    "fail",
                    "Imports",
                    f"{module_name} failed to import",
                    details=f"{type(exc).__name__}: {exc}",
                    fix="Fix the import error before running Arvis.",
                )
            )
        else:
            checks.append(DoctorCheck("ok", "Imports", f"{module_name} imports"))
    return checks


def _check_minecraft_config(env: dict[str, str]) -> list[DoctorCheck]:
    if not _env_bool(env.get("MINECRAFT_SERVER_ENABLED"), default=False):
        return []
    checks: list[DoctorCheck] = []
    cwd = env.get("MINECRAFT_SERVER_CWD", "").strip()
    command = _parse_command(env.get("MINECRAFT_SERVER_COMMAND", ""))
    if not cwd:
        checks.append(
            DoctorCheck(
                "fail",
                "Config",
                "MINECRAFT_SERVER_CWD is required when Minecraft is enabled",
                fix="Set MINECRAFT_SERVER_CWD in .env.",
            )
        )
    if not command:
        checks.append(
            DoctorCheck(
                "fail",
                "Config",
                "MINECRAFT_SERVER_COMMAND is required when Minecraft is enabled",
                fix="Set MINECRAFT_SERVER_COMMAND in .env.",
            )
        )
    return checks


def _app_commands_from_env(env: dict[str, str]) -> dict[str, dict[str, object]]:
    return {
        "steam": _command_info(env.get("STEAM_COMMAND"), [["steam"]]),
        "spotify": _command_info(env.get("SPOTIFY_COMMAND"), [["flatpak", "run", "com.spotify.Client"], ["spotify"]]),
        "brave": _command_info(env.get("BRAVE_COMMAND"), [["brave-browser"], ["brave"]]),
        "discord": _command_info(env.get("DISCORD_COMMAND"), [["flatpak", "run", "com.discordapp.Discord"], ["discord"]]),
        "telegram": _command_info(env.get("TELEGRAM_COMMAND"), [["flatpak", "run", "org.telegram.desktop"], ["telegram-desktop"]]),
    }


def _command_info(env_value: str | None, fallbacks: list[list[str]]) -> dict[str, object]:
    explicit = bool(env_value and env_value.strip())
    parsed = _parse_command(env_value or "")
    return {
        "commands": _command_options(env_value, fallbacks),
        "explicit": explicit,
        "parse_error": explicit and not parsed,
        "commands_parseable": all(_is_argv_list(command) for command in fallbacks) and (not explicit or bool(parsed)),
    }


def _command_options(env_value: str | None, fallbacks: list[list[str]]) -> list[list[str]]:
    parsed = _parse_command(env_value or "")
    commands = [parsed] if parsed else []
    commands.extend(fallbacks)
    deduped: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for command in commands:
        key = tuple(command)
        if command and key not in seen:
            seen.add(key)
            deduped.append(command)
    return deduped


def _command_available(command: list[str]) -> bool:
    if not command:
        return False
    executable = command[0]
    return shutil.which(executable) is not None


def _is_argv_list(command: object) -> bool:
    return isinstance(command, list) and bool(command) and all(isinstance(part, str) and part for part in command)


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _parse_command(value: str) -> list[str]:
    import shlex

    try:
        return shlex.split(value)
    except ValueError:
        return []


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _git_tracked_files(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            shell=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _write_safe_env_example(path: Path) -> bool:
    if path.exists():
        return False
    try:
        path.write_text(
            "\n".join(
                [
                    "USER_NAME=your_name",
                    "OLLAMA_HOST=http://127.0.0.1:11434",
                    f"ARVIS_MODEL={_default_arvis_model()}",
                    "",
                    "MUSIC_FOLDER=/path/to/music",
                    "DOWNLOADS_FOLDER=/path/to/downloads",
                    "",
                    "MINECRAFT_SERVER_ENABLED=false",
                    "MINECRAFT_SERVER_CWD=/absolute/path/to/server",
                    "MINECRAFT_SERVER_COMMAND=./start.sh",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    except OSError:
        return False
    return True


def _text_has_secret_like_value(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if SECRET_VALUE_RE.search(value):
            return True
        if SECRET_NAME_RE.search(key) and value and value.lower() not in {"placeholder", "your_token", "your_key", "changeme"}:
            return True
    return False


def _text_has_absolute_home_path(text: str) -> bool:
    return bool(re.search(r"(/home/[^/\s]+|/Users/[^/\s]+)", text))


def _is_local_or_private_host(hostname: str) -> bool:
    if hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True
    if hostname.startswith("127.") or hostname.startswith("10.") or hostname.startswith("192.168."):
        return True
    parts = hostname.split(".")
    if len(parts) == 4 and parts[0] == "172":
        try:
            return 16 <= int(parts[1]) <= 31
        except ValueError:
            return False
    return False


def _redacted_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.username or parsed.password:
        netloc = parsed.hostname or ""
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return parsed._replace(netloc=netloc).geturl()
    return value
