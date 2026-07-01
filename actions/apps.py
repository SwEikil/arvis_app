from __future__ import annotations

import subprocess
from pathlib import Path

from config import APP_COMMANDS
from config import get_minecraft_server_config


APP_WHITELIST = APP_COMMANDS


TARGET_ALIASES = {
    "steam": "steam",
    "стім": "steam",
    "стим": "steam",
    "spotify": "spotify",
    "споті": "spotify",
    "споти": "spotify",
    "спотіфай": "spotify",
    "music": "music",
    "media": "media",
    "current media": "media",
    "current track": "media",
    "current song": "media",
    "active player": "media",
    "player": "media",
    "video": "media",
    "youtube": "youtube",
    "you tube": "youtube",
    "yt": "youtube",
    "ютуб": "youtube",
    "ютюб": "youtube",
    "ютьюб": "youtube",
    "google": "google",
    "гугл": "google",
    "ґугл": "google",
    "github": "github",
    "git hub": "github",
    "гітхаб": "github",
    "гитхаб": "github",
    "chatgpt": "chatgpt",
    "chat gpt": "chatgpt",
    "чатгпт": "chatgpt",
    "чат гпт": "chatgpt",
    "чатджпт": "chatgpt",
    "brave": "brave",
    "браузер": "brave",
    "браве": "brave",
    "брейв": "brave",
    "browser": "brave",
    "active tab": "brave",
    "active browser": "brave",
    "discord": "discord",
    "діскорд": "discord",
    "дискорд": "discord",
    "telegram": "telegram",
    "телега": "telegram",
    "телеграм": "telegram",
    "minecraft_server": "default",
    "minecraft server": "default",
    "майнкрафт сервер": "default",
    "сервер майнкрафт": "default",
    "майн сервер": "default",
    "mc server": "default",
}


def normalize_target(target: str | None) -> str:
    normalized = (target or "").strip().lower().replace("-", " ").replace("_", " ")
    normalized = " ".join(normalized.split())
    if normalized == "minecraft server":
        return "default"
    return TARGET_ALIASES.get(normalized, normalized.replace(" ", "_"))


def preview_app_action(action: str, target: str | None) -> tuple[bool, str, str | None]:
    normalized_target = normalize_target(target)

    if action == "start_minecraft_server":
        server_config, error = _get_minecraft_server_config(normalized_target)
        if error is not None:
            return False, error, f"target={normalized_target}"
        command = _format_command(_config_command(server_config))
        cwd = _config_cwd(server_config)
        return False, "Dry-run: would start Minecraft server.", f"cwd={cwd}; command={command}"

    if normalized_target not in APP_WHITELIST:
        return False, "App target is not in the whitelist.", f"target={normalized_target}"

    commands = [" ".join(command) for command in APP_WHITELIST[normalized_target]]
    return (
        False,
        f"Dry-run: would launch `{normalized_target}`.",
        f"commands tried in order: {'; '.join(commands)}",
    )


def execute_app_action(action: str, target: str | None) -> tuple[bool, str, str | None]:
    normalized_target = normalize_target(target)

    if action == "start_minecraft_server":
        return _start_minecraft_server(normalized_target)

    if normalized_target not in APP_WHITELIST:
        return False, "App target is not in the whitelist.", f"target={normalized_target}"

    failures: list[str] = []
    for command in APP_WHITELIST[normalized_target]:
        try:
            process = subprocess.Popen(
                command,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            failures.append(f"{_format_command(command)}: command not found")
            continue
        except OSError as exc:
            failures.append(f"{_format_command(command)}: {exc}")
            continue

        try:
            stdout, stderr = process.communicate(timeout=0.3)
        except subprocess.TimeoutExpired:
            return True, f"Launched `{normalized_target}`.", _format_command(command)

        if process.returncode == 0:
            return True, f"Launched `{normalized_target}`.", _format_command(command)

        error_text = (stderr or stdout or "").strip()
        if error_text:
            failures.append(f"{_format_command(command)}: {error_text}")
        else:
            failures.append(f"{_format_command(command)}: exited with code {process.returncode}")
        continue

    return False, f"Could not launch `{normalized_target}`.", "; ".join(failures) or None


def _start_minecraft_server(target: str) -> tuple[bool, str, str | None]:
    server_config, error = _get_minecraft_server_config(target)
    if error is not None:
        return False, error, f"target={target}"

    command = _config_command(server_config)
    cwd = _config_cwd(server_config)
    try:
        subprocess.Popen(command, cwd=cwd, shell=False)
    except FileNotFoundError:
        return False, "Minecraft server command was not found.", f"cwd={cwd}; command={_format_command(command)}"
    except OSError as exc:
        return False, "Minecraft server failed to start.", f"cwd={cwd}; command={_format_command(command)}; error={exc}"

    return True, "Minecraft server start command launched.", f"cwd={cwd}; command={_format_command(command)}"


def _get_minecraft_server_config(target: str) -> tuple[dict[str, object] | None, str | None]:
    server_key = "default" if target == "minecraft_server" else target
    server_config = get_minecraft_server_config()
    if server_config is None:
        if target in {"minecraft_server", "default"}:
            return None, "Minecraft server is not configured yet."
        return None, "Minecraft server target is not in the whitelist."
    if server_key != server_config.key:
        return None, "Minecraft server target is not in the whitelist."

    command = _config_command(server_config)
    cwd = _config_cwd(server_config)
    if not command or not cwd:
        return None, "Minecraft server is not configured yet."
    return server_config, None


def _config_command(server_config: object | None) -> list[str]:
    if not server_config:
        return []
    command = getattr(server_config, "command", None)
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        return []
    return command


def _config_cwd(server_config: object | None) -> str:
    if not server_config:
        return ""
    cwd = getattr(server_config, "cwd", None)
    if cwd is None:
        return ""
    return str(Path(cwd).expanduser())


def _format_command(command: list[str]) -> str:
    return " ".join(command)
