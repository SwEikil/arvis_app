from __future__ import annotations

import importlib.util
import math
import shutil
import struct
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
    debug_audio_path: str = ""


@dataclass(frozen=True)
class VoiceAudioStats:
    duration_seconds: float
    rms: float
    peak: float


def get_voice_dependency_status() -> VoiceDependencyStatus:
    return VoiceDependencyStatus(
        faster_whisper_available=importlib.util.find_spec("faster_whisper") is not None,
        sounddevice_available=importlib.util.find_spec("sounddevice") is not None,
        numpy_available=importlib.util.find_spec("numpy") is not None,
    )


def preflight_voice_capture(config: VoiceConfig) -> VoiceTranscriptionResult | None:
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
    return None


def transcribe_once(config: VoiceConfig) -> VoiceTranscriptionResult:
    preflight = preflight_voice_capture(config)
    if preflight is not None:
        return preflight

    temp_path: Path | None = None
    try:
        ensure_stt_model_loaded(config)
        temp_path = record_microphone_to_temp_wav(config)
        return transcribe_recorded_audio(temp_path, config)
    except Exception as error:
        return VoiceTranscriptionResult(False, error=safe_error(error))
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


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
    text, _info = _run_model_transcribe(path, config)
    return text


def transcribe_recorded_audio(path: Path, config: VoiceConfig) -> VoiceTranscriptionResult:
    debug_path = save_debug_audio(path) if config.debug_save_last else ""
    stats = analyze_wav_audio(path)
    if stats.rms < config.min_rms and stats.peak < config.min_peak:
        return VoiceTranscriptionResult(False, no_speech=True, debug_audio_path=debug_path)

    try:
        text, info = _run_model_transcribe(path, config)
    except Exception as error:
        return VoiceTranscriptionResult(False, error=safe_error(error), debug_audio_path=debug_path)

    stripped = text.strip()
    if not stripped:
        return VoiceTranscriptionResult(False, no_speech=True, debug_audio_path=debug_path)
    if not looks_like_valid_voice_transcript(stripped, info, config):
        return VoiceTranscriptionResult(False, error="unclear_voice", debug_audio_path=debug_path)
    return VoiceTranscriptionResult(True, text=stripped, debug_audio_path=debug_path)


def analyze_wav_audio(path: Path) -> VoiceAudioStats:
    with wave.open(str(path), "rb") as wav_file:
        channels = max(1, wav_file.getnchannels())
        sample_width = wav_file.getsampwidth()
        frame_count = wav_file.getnframes()
        framerate = wav_file.getframerate() or 1
        frames = wav_file.readframes(frame_count)

    if not frames or frame_count <= 0:
        return VoiceAudioStats(duration_seconds=0.0, rms=0.0, peak=0.0)

    samples = _decode_pcm_samples(frames, sample_width)
    if not samples:
        return VoiceAudioStats(duration_seconds=frame_count / framerate, rms=0.0, peak=0.0)

    squared = sum(sample * sample for sample in samples)
    rms = math.sqrt(squared / len(samples))
    peak = max(abs(sample) for sample in samples)
    return VoiceAudioStats(duration_seconds=frame_count / framerate, rms=rms, peak=peak)


def looks_like_valid_voice_transcript(text: str, info: object | None, config: VoiceConfig) -> bool:
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return False
    if normalized in {"you", "uh", "um", "а", ".", ",", "..."}:
        return False

    detected_language = getattr(info, "language", None)
    if isinstance(detected_language, str) and config.allowed_languages:
        if detected_language.strip().lower() not in config.allowed_languages:
            return False

    return True


def save_debug_audio(path: Path) -> str:
    target = Path(".runtime") / "voice_debug" / "last_voice.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
    return str(target)


def ensure_stt_model_loaded(config: VoiceConfig) -> object:
    return _load_model(config)


def _run_model_transcribe(path: Path, config: VoiceConfig) -> tuple[str, object | None]:
    model = ensure_stt_model_loaded(config)
    kwargs: dict[str, Any] = {}
    if config.language != "auto":
        kwargs["language"] = config.language
    segments, info = model.transcribe(
        str(path),
        **kwargs,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
        beam_size=5,
    )
    text = " ".join(segment.text.strip() for segment in segments if getattr(segment, "text", "").strip()).strip()
    return text, info


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


def _decode_pcm_samples(frames: bytes, sample_width: int) -> list[float]:
    if sample_width == 1:
        return [(sample - 128) / 128.0 for sample in frames]
    if sample_width == 2:
        count = len(frames) // 2
        values = struct.unpack(f"<{count}h", frames[: count * 2])
        return [value / 32768.0 for value in values]
    if sample_width == 4:
        count = len(frames) // 4
        values = struct.unpack(f"<{count}i", frames[: count * 4])
        return [value / 2147483648.0 for value in values]
    return []


def _sounddevice_device(value: str) -> str | int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return value
