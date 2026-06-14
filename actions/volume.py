from __future__ import annotations

import subprocess

from parameter_extraction import get_int_param


def preview_volume_action(action: str, params: dict[str, object] | None = None) -> tuple[bool, str, str | None]:
    command = _build_volume_command(action, params)
    if command is None:
        return False, "Volume action is not supported.", None

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
    return None
