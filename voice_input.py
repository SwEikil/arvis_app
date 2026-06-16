from __future__ import annotations

import importlib.util
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voice_config import VoiceConfig
from voice_config import is_unsafe_audio_device


_MODEL_CACHE: dict[tuple[str, str, str], object] = {}


@dataclass(frozen=True)
class VoiceDependencyStatus:
    faster_whisper_available: bool
    sounddevice_available: bool
    numpy_available: bool


@dataclass(frozen=True)
class VoiceTranscriptionResult:
    ok: bool
    text: str = ""
    error: str = ""
    no_speech: bool = False


def get_voice_dependency_status() -> VoiceDependencyStatus:
    return VoiceDependencyStatus(
        faster_whisper_available=importlib.util.find_spec("faster_whisper") is not None,
        sounddevice_available=importlib.util.find_spec("sounddevice") is not None,
        numpy_available=importlib.util.find_spec("numpy") is not None,
    )


def transcribe_once(config: VoiceConfig) -> VoiceTranscriptionResult:
    if not config.enabled:
        return VoiceTranscriptionResult(False, error="voice_disabled")
    if config.stt_backend != "faster_whisper":
        return VoiceTranscriptionResult(False, error=f"unsupported STT backend: {config.stt_backend}")
    if config.mic_device and is_unsafe_audio_device(config.mic_device):
        return VoiceTranscriptionResult(False, error="unsafe_audio_device")

    dependency_status = get_voice_dependency_status()
    missing = []
    if not dependency_status.faster_whisper_available:
        missing.append("faster-whisper")
    if not dependency_status.sounddevice_available:
        missing.append("sounddevice")
    if not dependency_status.numpy_available:
        missing.append("numpy")
    if missing:
        return VoiceTranscriptionResult(False, error=f"missing optional voice dependencies: {', '.join(missing)}")

    temp_path: Path | None = None
    try:
        temp_path = record_microphone_to_temp_wav(config)
        text = transcribe_audio_file(temp_path, config)
    except Exception as error:
        return VoiceTranscriptionResult(False, error=safe_error(error))
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    if not text.strip():
        return VoiceTranscriptionResult(False, no_speech=True)
    return VoiceTranscriptionResult(True, text=text.strip())


def record_microphone_to_temp_wav(config: VoiceConfig) -> Path:
    import numpy as np
    import sounddevice as sd

    samplerate = 16000
    channels = 1
    device = _sounddevice_device(config.mic_device)
    audio = sd.rec(
        int(config.record_seconds * samplerate),
        samplerate=samplerate,
        channels=channels,
        dtype="float32",
        device=device,
    )
    sd.wait()

    path = Path(tempfile.NamedTemporaryFile(prefix="arvis_voice_", suffix=".wav", delete=False).name)
    int_audio = np.clip(audio, -1.0, 1.0)
    int_audio = (int_audio * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(samplerate)
        wav_file.writeframes(int_audio.tobytes())
    return path


def transcribe_audio_file(path: Path, config: VoiceConfig) -> str:
    model = _load_model(config)
    kwargs: dict[str, Any] = {}
    if config.language != "auto":
        kwargs["language"] = config.language
    segments, _info = model.transcribe(str(path), **kwargs)
    return " ".join(segment.text.strip() for segment in segments if getattr(segment, "text", "").strip()).strip()


def safe_error(error: Exception) -> str:
    text = str(error).strip() or type(error).__name__
    home = str(Path.home())
    if home and home != "/":
        text = text.replace(home, "~")
    return text[:240]


def _load_model(config: VoiceConfig) -> object:
    from faster_whisper import WhisperModel

    key = (config.stt_model, config.stt_device, config.stt_compute_type)
    if key not in _MODEL_CACHE:
        kwargs: dict[str, str] = {}
        if config.stt_device != "auto":
            kwargs["device"] = config.stt_device
        if config.stt_compute_type != "auto":
            kwargs["compute_type"] = config.stt_compute_type
        _MODEL_CACHE[key] = WhisperModel(config.stt_model, **kwargs)
    return _MODEL_CACHE[key]


def _sounddevice_device(value: str) -> str | int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return value
