from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from actions.volume import parse_wpctl_volume
from voice_config import VoiceConfig


DUCK_WARNING = "Не зміг приглушити звук, сер. Продовжую запис."
RESTORE_WARNING = "Не зміг повернути попередню гучність, сер. Перевір звук вручну."


@dataclass(frozen=True)
class VoiceVolumeState:
    volume_percent: int
    muted: bool
    raw: str


class VoiceDucking:
    def __init__(self, config: VoiceConfig, warn: Callable[[str], None] | None = None) -> None:
        self.config = config
        self.warn = warn
        self.previous_state: VoiceVolumeState | None = None
        self.applied = False
        self.restored = False
        self.duck_failed = False
        self.restore_failed = False

    def __enter__(self) -> VoiceDucking:
        if not self.config.ducking_enabled:
            return self

        state = self._read_volume()
        if state is None:
            self._warn_duck_failed()
            return self

        self.previous_state = state
        if state.muted:
            return self
        if state.volume_percent <= self.config.duck_percent:
            return self

        if self._set_volume(self.config.duck_percent):
            self.applied = True
        else:
            self._warn_duck_failed()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if self.applied and self.config.duck_restore and self.previous_state is not None:
            if self._set_volume(self.previous_state.volume_percent):
                self.restored = True
            else:
                self.restore_failed = True
                if self.warn is not None:
                    self.warn(RESTORE_WARNING)
        return False

    def _read_volume(self) -> VoiceVolumeState | None:
        try:
            result = subprocess.run(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                shell=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None
        if result.returncode != 0:
            return None

        raw_output = result.stdout.strip()
        parsed = parse_wpctl_volume(raw_output)
        if parsed is None:
            return None
        volume_percent, muted = parsed
        return VoiceVolumeState(volume_percent=volume_percent, muted=muted, raw=raw_output)

    def _set_volume(self, percent: int) -> bool:
        clamped = max(0, min(100, percent))
        try:
            result = subprocess.run(
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{clamped}%"],
                shell=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        return result.returncode == 0

    def _warn_duck_failed(self) -> None:
        self.duck_failed = True
        if self.warn is not None:
            self.warn(DUCK_WARNING)
