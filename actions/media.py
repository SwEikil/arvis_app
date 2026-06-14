from __future__ import annotations

import subprocess

from parameter_extraction import get_int_param


MEDIA_ACTIONS = {
    "music_play_pause": "play-pause",
    "music_next": "next",
    "music_previous": "previous",
    "music_play": "play",
    "music_pause": "pause",
    "play_music_by_mood": "play",
}


def preview_media_action(action: str, params: dict[str, object] | None = None) -> tuple[bool, str, str | None]:
    if action == "music_like_current":
        return (
            False,
            "Liking current song requires Spotify API and is not configured yet.",
            "Spotify Web API/OAuth is required; no GUI automation will be used.",
        )

    command = _build_playerctl_action(action, "<selected-player>", params)
    if command is None:
        return False, "Media action is not supported.", None

    details = "Would select a player with priority: spotify, brave, first available."
    if action == "play_music_by_mood":
        return (
            False,
            "Dry-run: would start playback on an available media player. Mood playlist selection will be added later.",
            f"{_format_command(command)}. {details}",
        )

    return (
        False,
        f"Dry-run: would run media action `{action}`.",
        f"{_format_command(command)}. {details}",
    )


def execute_media_action(action: str, params: dict[str, object] | None = None) -> tuple[bool, str, str | None]:
    if action == "music_like_current":
        return (
            False,
            "Liking current song requires Spotify API and is not configured yet.",
            "Spotify Web API/OAuth is required; no GUI automation will be used.",
        )

    if action not in MEDIA_ACTIONS and action not in PARAM_MEDIA_ACTIONS:
        command = None
    else:
        command = []
    if command is None:
        return False, "Media action is not supported.", None

    player, player_details = _select_player()
    if player is None:
        return False, "Media player was not found.", player_details

    command = _build_playerctl_action(action, player, params)
    if command is None:
        return False, "Media action is not supported.", None

    result = _run_playerctl(command)
    details = f"player={player}; command={_format_command(command)}"
    if result.returncode != 0:
        error_text = (result.stderr or result.stdout or "").strip()
        if error_text:
            details = f"{details}; error={error_text}"
        return False, "Media action failed.", details

    if action == "play_music_by_mood":
        return (
            True,
            "Started playback on the selected media player. Mood playlist selection will be added later.",
            details,
        )

    return True, f"Executed media action `{action}`.", details


PARAM_MEDIA_ACTIONS = {
    "media_seek_forward",
    "media_seek_backward",
    "music_repeat_track",
    "music_repeat_playlist",
    "music_repeat_off",
    "music_shuffle_on",
    "music_shuffle_off",
    "music_shuffle_toggle",
}


def _build_playerctl_action(
    action: str,
    player: str,
    params: dict[str, object] | None,
) -> list[str] | None:
    if action in MEDIA_ACTIONS:
        return ["playerctl", "-p", player, MEDIA_ACTIONS[action]]
    if action == "media_seek_forward":
        seconds = get_int_param(params, "seconds", 5, 1, 300)
        return ["playerctl", "-p", player, "position", f"{seconds}+"]
    if action == "media_seek_backward":
        seconds = get_int_param(params, "seconds", 5, 1, 300)
        return ["playerctl", "-p", player, "position", f"{seconds}-"]
    if action == "music_repeat_track":
        return ["playerctl", "-p", player, "loop", "Track"]
    if action == "music_repeat_playlist":
        return ["playerctl", "-p", player, "loop", "Playlist"]
    if action == "music_repeat_off":
        return ["playerctl", "-p", player, "loop", "None"]
    if action == "music_shuffle_on":
        return ["playerctl", "-p", player, "shuffle", "On"]
    if action == "music_shuffle_off":
        return ["playerctl", "-p", player, "shuffle", "Off"]
    if action == "music_shuffle_toggle":
        return ["playerctl", "-p", player, "shuffle", "Toggle"]
    return None


def _select_player() -> tuple[str | None, str | None]:
    result = _run_playerctl(["playerctl", "-l"])
    if result.returncode != 0:
        error_text = (result.stderr or result.stdout or "").strip()
        details = f"playerctl -l failed: {error_text}" if error_text else "playerctl -l failed."
        return None, details

    players = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not players:
        return None, "playerctl did not report any available players."

    for player in players:
        if "spotify" in player.lower():
            return player, f"available players: {', '.join(players)}"

    for player in players:
        if "brave" in player.lower():
            return player, f"available players: {', '.join(players)}"

    return players[0], f"available players: {', '.join(players)}"


def _run_playerctl(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, 127, "", "playerctl was not found.")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", "playerctl timed out.")


def _format_command(command: list[str]) -> str:
    return " ".join(command)
