from __future__ import annotations

import os
from dataclasses import dataclass


UNSAFE_AUDIO_DEVICE_MARKERS = ("monitor", "output", "loopback", "desktop")


@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool
    stt_backend: str
    stt_model: str
    stt_device: str
    stt_compute_type: str
    mic_device: str
    record_seconds: int
    language: str
    allowed_languages: tuple[str, ...] = ("uk", "ru", "en", "no")
    min_rms: float = 0.008
    min_peak: float = 0.03
    debug_save_last: bool = False
    ducking_enabled: bool = True
    duck_percent: int = 15
    duck_restore: bool = True


def load_voice_config() -> VoiceConfig:
    return VoiceConfig(
        enabled=_env_bool("ARVIS_VOICE_ENABLED", default=False),
        stt_backend=os.getenv("ARVIS_STT_BACKEND", "faster_whisper").strip() or "faster_whisper",
        stt_model=os.getenv("ARVIS_STT_MODEL", "small").strip() or "small",
        stt_device=os.getenv("ARVIS_STT_DEVICE", "auto").strip() or "auto",
        stt_compute_type=os.getenv("ARVIS_STT_COMPUTE_TYPE", "auto").strip() or "auto",
        mic_device=os.getenv("ARVIS_MIC_DEVICE", "").strip(),
        record_seconds=_env_int("ARVIS_VOICE_RECORD_SECONDS", default=6, min_value=1, max_value=30),
        language=os.getenv("ARVIS_VOICE_LANGUAGE", "uk").strip() or "uk",
        allowed_languages=_env_csv("ARVIS_VOICE_ALLOWED_LANGUAGES", ("uk", "ru", "en", "no")),
        min_rms=_env_float("ARVIS_VOICE_MIN_RMS", default=0.008, min_value=0.0, max_value=1.0),
        min_peak=_env_float("ARVIS_VOICE_MIN_PEAK", default=0.03, min_value=0.0, max_value=1.0),
        debug_save_last=_env_bool("ARVIS_VOICE_DEBUG_SAVE_LAST", default=False),
        ducking_enabled=_env_bool("ARVIS_VOICE_DUCKING_ENABLED", default=True),
        duck_percent=_env_int("ARVIS_VOICE_DUCK_PERCENT", default=15, min_value=0, max_value=100),
        duck_restore=_env_bool("ARVIS_VOICE_DUCK_RESTORE", default=True),
    )


def is_unsafe_audio_device(device_name: str | None) -> bool:
    normalized = (device_name or "").strip().lower()
    return bool(normalized) and any(marker in normalized for marker in UNSAFE_AUDIO_DEVICE_MARKERS)


def voice_disabled_message() -> str:
    return "Голосовий режим вимкнений, сер. Увімкни ARVIS_VOICE_ENABLED=true у локальному .env."


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    parsed = parse_bool(value)
    return default if parsed is None else parsed


def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    value = os.getenv(name, "")
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(min_value, min(parsed, max_value))


def _env_float(name: str, default: float, min_value: float, max_value: float) -> float:
    value = os.getenv(name, "")
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(min_value, min(parsed, max_value))


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name, "")
    if not value.strip():
        return default
    parsed = tuple(item.strip().lower() for item in value.split(",") if item.strip())
    return parsed or default


def parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None
