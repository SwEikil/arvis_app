from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_config import VoiceConfig
from voice_config import is_unsafe_audio_device
from voice_config import load_voice_config
from voice_input import VoiceDependencyStatus
from voice_input import transcribe_audio_file
from voice_input import transcribe_once
from voice_input import _MODEL_CACHE


def _config(enabled: bool = True, mic_device: str = "") -> VoiceConfig:
    return VoiceConfig(
        enabled=enabled,
        stt_backend="faster_whisper",
        stt_model="small",
        stt_device="auto",
        stt_compute_type="auto",
        mic_device=mic_device,
        record_seconds=1,
        language="auto",
    )


class VoiceInputTests(unittest.TestCase):
    def test_unsafe_audio_device_names_are_rejected(self) -> None:
        for name in ["Monitor of Speakers", "loopback", "desktop audio"]:
            with self.subTest(name=name):
                self.assertTrue(is_unsafe_audio_device(name))

    def test_invalid_duck_percent_uses_safe_default(self) -> None:
        with patch.dict("os.environ", {"ARVIS_VOICE_DUCK_PERCENT": "invalid"}, clear=False):
            config = load_voice_config()

        self.assertEqual(config.duck_percent, 15)

    def test_transcribe_once_rejects_unsafe_mic_device(self) -> None:
        result = transcribe_once(_config(mic_device="Monitor of Speakers"))

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "unsafe_audio_device")

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

        with patch(
            "voice_input.get_voice_dependency_status",
            return_value=VoiceDependencyStatus(True, True, True),
        ), patch("voice_input.record_microphone_to_temp_wav", return_value=path), patch(
            "voice_input.transcribe_audio_file",
            return_value="тест",
        ):
            result = transcribe_once(_config())

        self.assertTrue(result.ok)
        self.assertEqual(result.text, "тест")
        self.assertFalse(path.exists())

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
