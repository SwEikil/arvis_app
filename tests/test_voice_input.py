from __future__ import annotations

import sys
import tempfile
import types
import unittest
import wave
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from voice_config import VoiceConfig
from voice_config import is_unsafe_audio_device
from voice_config import load_voice_config
from voice_input import VoiceDependencyStatus
from voice_input import analyze_wav_audio
from voice_input import looks_like_valid_voice_transcript
from voice_input import preflight_voice_capture
from voice_input import transcribe_audio_file
from voice_input import transcribe_recorded_audio
from voice_input import transcribe_once
from voice_input import _MODEL_CACHE


def _config(
    enabled: bool = True,
    mic_device: str = "",
    stt_backend: str = "faster_whisper",
    language: str = "uk",
    debug_save_last: bool = False,
) -> VoiceConfig:
    return VoiceConfig(
        enabled=enabled,
        stt_backend=stt_backend,
        stt_model="small",
        stt_device="auto",
        stt_compute_type="auto",
        mic_device=mic_device,
        record_seconds=1,
        language=language,
        debug_save_last=debug_save_last,
    )


def _write_wav(path: Path, samples: list[int], samplerate: int = 16000) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(samplerate)
        wav_file.writeframes(b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples))


@contextmanager
def _temporary_cwd(path: Path):
    import os

    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class VoiceInputTests(unittest.TestCase):
    def test_unsafe_audio_device_names_are_rejected(self) -> None:
        for name in ["Monitor of Speakers", "loopback", "desktop audio"]:
            with self.subTest(name=name):
                self.assertTrue(is_unsafe_audio_device(name))

    def test_invalid_duck_percent_uses_safe_default(self) -> None:
        with patch.dict("os.environ", {"ARVIS_VOICE_DUCK_PERCENT": "invalid"}, clear=False):
            config = load_voice_config()

        self.assertEqual(config.duck_percent, 15)

    def test_voice_language_defaults_to_uk(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            config = load_voice_config()

        self.assertEqual(config.language, "uk")

    def test_transcribe_once_rejects_unsafe_mic_device(self) -> None:
        result = transcribe_once(_config(mic_device="Monitor of Speakers"))

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "unsafe_audio_device")

    def test_preflight_returns_none_when_ready(self) -> None:
        with patch(
            "voice_input.get_voice_dependency_status",
            return_value=VoiceDependencyStatus(True, True, True),
        ):
            result = preflight_voice_capture(_config())

        self.assertIsNone(result)

    def test_preflight_missing_dependencies_returns_error(self) -> None:
        with patch(
            "voice_input.get_voice_dependency_status",
            return_value=VoiceDependencyStatus(False, True, False),
        ):
            result = preflight_voice_capture(_config())

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.ok)
        self.assertIn("faster-whisper", result.error)
        self.assertIn("numpy", result.error)

    def test_preflight_rejects_unsafe_mic_device(self) -> None:
        result = preflight_voice_capture(_config(mic_device="loopback"))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.error, "unsafe_audio_device")

    def test_preflight_rejects_unsupported_backend(self) -> None:
        result = preflight_voice_capture(_config(stt_backend="other"))

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("unsupported STT backend", result.error)

    def test_missing_dependencies_are_handled_gracefully(self) -> None:
        with patch(
            "voice_input.get_voice_dependency_status",
            return_value=VoiceDependencyStatus(False, False, False),
        ):
            result = transcribe_once(_config())

        self.assertFalse(result.ok)
        self.assertIn("missing optional voice dependencies", result.error)

    def test_temp_audio_file_is_removed_after_transcription(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            path = Path(tmp.name)
        _write_wav(path, [6000] * 1600)

        with patch(
            "voice_input.get_voice_dependency_status",
            return_value=VoiceDependencyStatus(True, True, True),
        ), patch("voice_input.record_microphone_to_temp_wav", return_value=path), patch(
            "voice_input._run_model_transcribe",
            return_value=("тест", types.SimpleNamespace(language="uk")),
        ):
            result = transcribe_once(_config())

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "тест")
        self.assertFalse(path.exists())

    def test_silence_audio_does_not_call_stt(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            path = Path(tmp.name)
        _write_wav(path, [0] * 1600)

        with patch("voice_input._run_model_transcribe") as transcribe:
            result = transcribe_recorded_audio(path, _config())

        self.assertFalse(result.ok)
        self.assertTrue(result.no_speech)
        transcribe.assert_not_called()
        path.unlink(missing_ok=True)

    def test_low_rms_low_peak_returns_no_speech(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            path = Path(tmp.name)
        _write_wav(path, [10, -10] * 800)

        stats = analyze_wav_audio(path)
        result = transcribe_recorded_audio(path, _config())

        self.assertLess(stats.rms, 0.008)
        self.assertLess(stats.peak, 0.03)
        self.assertTrue(result.no_speech)
        path.unlink(missing_ok=True)

    def test_transcript_you_is_rejected_as_hallucination(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            path = Path(tmp.name)
        _write_wav(path, [8000] * 1600)

        with patch("voice_input._run_model_transcribe", return_value=("you", types.SimpleNamespace(language="en"))):
            result = transcribe_recorded_audio(path, _config())

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "unclear_voice")
        path.unlink(missing_ok=True)

    def test_detected_language_not_allowed_is_rejected(self) -> None:
        info = types.SimpleNamespace(language="tr")

        self.assertFalse(looks_like_valid_voice_transcript("Harvestim bana çır", info, _config()))

    def test_transcribe_passes_language_and_vad_options(self) -> None:
        calls: list[dict[str, object]] = []

        class FakeSegment:
            text = "Арвіс"

        class FakeWhisperModel:
            def __init__(self, model_name: str, **kwargs: str) -> None:
                pass

            def transcribe(self, path: str, **kwargs: object) -> tuple[list[FakeSegment], object]:
                calls.append(kwargs)
                return [FakeSegment()], types.SimpleNamespace(language="uk")

        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = FakeWhisperModel
        _MODEL_CACHE.clear()

        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp, patch.dict(sys.modules, {"faster_whisper": fake_module}):
            self.assertEqual(transcribe_audio_file(Path(tmp.name), _config(language="uk")), "Арвіс")

        self.assertEqual(calls[0]["language"], "uk")
        self.assertTrue(calls[0]["vad_filter"])
        self.assertEqual(calls[0]["vad_parameters"], {"min_silence_duration_ms": 500})
        self.assertFalse(calls[0]["condition_on_previous_text"])
        self.assertEqual(calls[0]["beam_size"], 5)

    def test_debug_save_writes_last_voice_only_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source.wav"
            _write_wav(source, [8000] * 1600)

            with _temporary_cwd(root), patch(
                "voice_input._run_model_transcribe",
                return_value=("Арвіс, ти мене чуєш?", types.SimpleNamespace(language="uk")),
            ):
                disabled = transcribe_recorded_audio(source, _config(debug_save_last=False))
                self.assertFalse((root / ".runtime" / "voice_debug" / "last_voice.wav").exists())

                enabled = transcribe_recorded_audio(source, _config(debug_save_last=True))

            self.assertTrue(disabled.ok)
            self.assertTrue(enabled.ok)
            self.assertEqual(enabled.debug_audio_path, ".runtime/voice_debug/last_voice.wav")
            self.assertTrue((root / ".runtime" / "voice_debug" / "last_voice.wav").exists())

    def test_model_load_is_lazy_and_cached(self) -> None:
        _MODEL_CACHE.clear()
        calls: list[str] = []

        class FakeSegment:
            text = "hello"

        class FakeWhisperModel:
            def __init__(self, model_name: str, **kwargs: str) -> None:
                calls.append(model_name)

            def transcribe(self, path: str, **kwargs: str) -> tuple[list[FakeSegment], object]:
                return [FakeSegment()], object()

        fake_module = types.ModuleType("faster_whisper")
        fake_module.WhisperModel = FakeWhisperModel

        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp, patch.dict(sys.modules, {"faster_whisper": fake_module}):
            config = _config()
            self.assertEqual(transcribe_audio_file(Path(tmp.name), config), "hello")
            self.assertEqual(transcribe_audio_file(Path(tmp.name), config), "hello")

        self.assertEqual(calls, ["small"])


if __name__ == "__main__":
    unittest.main()
