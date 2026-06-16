from __future__ import annotations

import re
import subprocess

from parameter_extraction import get_int_param


def preview_volume_action(action: str, params: dict[str, object] | None = None) -> tuple[bool, str, str | None]:
    command = _build_volume_command(action, params)
    if command is None:
        return False, "Volume action is not supported.", None

    if action == "volume_status":
        return False, "Dry-run: would check current system volume.", " ".join(command)
    if action == "volume_set":
        level_percent = get_int_param(params, "level_percent", 50, 0, 100)
        return False, f"Dry-run: would set system volume to {level_percent}%.", " ".join(command)

    return False, f"Dry-run: would run volume action `{action}`.", " ".join(command)


def execute_volume_action(action: str, params: dict[str, object] | None = None) -> tuple[bool, str, str | None]:
    command = _build_volume_command(action, params)
    if command is None:
        return False, "Volume action is not supported.", None

    try:
        result = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return False, "Volume command failed because `wpctl` was not found.", " ".join(command)
    except subprocess.TimeoutExpired:
        return False, "Volume command timed out.", " ".join(command)

    if result.returncode != 0:
        error_text = (result.stderr or result.stdout or "").strip()
        details = " ".join(command)
        if error_text:
            details = f"{details}; error={error_text}"
        return False, "Volume action failed.", details

    if action == "volume_status":
        raw_output = result.stdout.strip()
        parsed = _parse_wpctl_volume(raw_output)
        if parsed is None:
            return False, "Volume status parse failed.", f"raw: {raw_output}"
        volume_percent, muted = parsed
        return (
            True,
            "Volume status fetched.",
            f"volume_percent: {volume_percent}\nmuted: {muted}\nraw: {raw_output}",
        )

    if action == "volume_set":
        level_percent = get_int_param(params, "level_percent", 50, 0, 100)
        return True, f"Set system volume to {level_percent}%.", f"level_percent: {level_percent}\ncommand: {' '.join(command)}"

    return True, f"Executed volume action `{action}`.", " ".join(command)


def _build_volume_command(action: str, params: dict[str, object] | None) -> list[str] | None:
    if action == "volume_up":
        step_percent = get_int_param(params, "step_percent", 5, 1, 50)
        return ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{step_percent}%+"]
    if action == "volume_down":
        step_percent = get_int_param(params, "step_percent", 5, 1, 50)
        return ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{step_percent}%-"]
    if action == "volume_mute":
        return ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"]
    if action == "volume_unmute":
        return ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"]
    if action == "volume_status":
        return ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]
    if action == "volume_set":
        level_percent = get_int_param(params, "level_percent", 50, 0, 100)
        return ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{level_percent}%"]
    return None


def _parse_wpctl_volume(output: str) -> tuple[int, bool] | None:
    match = re.search(r"Volume:\s*([0-9]+(?:\.[0-9]+)?)", output)
    if match is None:
        return None
    value = float(match.group(1))
    volume_percent = max(0, min(100, round(value * 100)))
    muted = "[MUTED]" in output.upper()
    return volume_percent, muted
