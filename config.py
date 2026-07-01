from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_ARVIS_MODEL = "arvis"
DEFAULT_MINECRAFT_SERVER_KEY = "default"
DEFAULT_MINECRAFT_SERVER_NAME = "Minecraft server"


USER_NAME = os.getenv("USER_NAME", "user")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
ARVIS_MODEL = os.getenv("ARVIS_MODEL", DEFAULT_ARVIS_MODEL)

MUSIC_FOLDER = os.getenv("MUSIC_FOLDER", "")
DOWNLOADS_FOLDER = os.getenv("DOWNLOADS_FOLDER", "")


@dataclass(frozen=True)
class MinecraftServerConfig:
    key: str
    name: str
    cwd: Path
    command: list[str]
    tmux_session: str


def command_options_from_env(env_name: str, fallbacks: list[list[str]]) -> list[list[str]]:
    env_command = parse_command(os.getenv(env_name, ""))
    commands = [env_command] if env_command else []
    commands.extend(fallbacks)
    return _dedupe_commands(commands)


def parse_command(value: str | None) -> list[str]:
    if not value or not value.strip():
        return []
    try:
        return shlex.split(value)
    except ValueError:
        return []


def get_minecraft_server_config() -> MinecraftServerConfig | None:
    if not _env_bool("MINECRAFT_SERVER_ENABLED", default=False):
        return None

    key = _normalize_key(os.getenv("MINECRAFT_SERVER_KEY") or DEFAULT_MINECRAFT_SERVER_KEY)
    name = (os.getenv("MINECRAFT_SERVER_NAME") or DEFAULT_MINECRAFT_SERVER_NAME).strip()
    cwd = (os.getenv("MINECRAFT_SERVER_CWD") or "").strip()
    command = parse_command(os.getenv("MINECRAFT_SERVER_COMMAND", ""))

    if not key or not cwd or not command:
        return None

    return MinecraftServerConfig(
        key=key,
        name=name or DEFAULT_MINECRAFT_SERVER_NAME,
        cwd=Path(cwd).expanduser(),
        command=command,
        tmux_session=f"arvis_minecraft_{key}",
    )


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_key(value: str) -> str:
    return "_".join(value.strip().lower().replace("-", " ").split())


def _dedupe_commands(commands: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    deduped: list[list[str]] = []
    for command in commands:
        key = tuple(command)
        if not command or key in seen:
            continue
        seen.add(key)
        deduped.append(command)
    return deduped


APP_COMMANDS = {
    "steam": command_options_from_env("STEAM_COMMAND", [["steam"]]),
    "spotify": command_options_from_env(
        "SPOTIFY_COMMAND",
        [["flatpak", "run", "com.spotify.Client"], ["spotify"]],
    ),
    "brave": command_options_from_env("BRAVE_COMMAND", [["brave-browser"], ["brave"]]),
    "discord": command_options_from_env(
        "DISCORD_COMMAND",
        [["flatpak", "run", "com.discordapp.Discord"], ["discord"]],
    ),
    "telegram": command_options_from_env(
        "TELEGRAM_COMMAND",
        [["flatpak", "run", "org.telegram.desktop"], ["telegram-desktop"]],
    ),
    "youtube": command_options_from_env("YOUTUBE_COMMAND", [["xdg-open", "https://www.youtube.com/"]]),
    "google": command_options_from_env("GOOGLE_COMMAND", [["xdg-open", "https://www.google.com/"]]),
    "github": command_options_from_env("GITHUB_COMMAND", [["xdg-open", "https://github.com/"]]),
    "chatgpt": command_options_from_env("CHATGPT_COMMAND", [["xdg-open", "https://chatgpt.com/"]]),
}
