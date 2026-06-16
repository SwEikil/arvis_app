from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from voice_config import VoiceConfig
from voice_ducking import DUCK_WARNING
from voice_ducking import VoiceDucking


def _config(duck_percent: int = 15, ducking_enabled: bool = True, duck_restore: bool = True) -> VoiceConfig:
    return VoiceConfig(
        enabled=True,
        stt_backend="faster_whisper",
        stt_model="small",
        stt_device="auto",
        stt_compute_type="auto",
        mic_device="",
        record_seconds=1,
        language="auto",
        ducking_enabled=ducking_enabled,
        duck_percent=duck_percent,
        duck_restore=duck_restore,
    )


class VoiceDuckingTests(unittest.TestCase):
    def test_ducking_stores_and_restores_previous_volume(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[1] == "get-volume":
                return subprocess.CompletedProcess(command, 0, "Volume: 0.42\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("voice_ducking.subprocess.run", side_effect=fake_run):
            ducking = VoiceDucking(_config(duck_percent=15))
            with ducking:
                self.assertTrue(ducking.applied)

        self.assertTrue(ducking.restored)
        self.assertEqual(
            calls,
            [
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "15%"],
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "42%"],
            ],
        )

    def test_ducking_does_not_increase_lower_volume(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "Volume: 0.10\n", "")

        with patch("voice_ducking.subprocess.run", side_effect=fake_run):
            ducking = VoiceDucking(_config(duck_percent=15))
            with ducking:
                self.assertFalse(ducking.applied)

        self.assertEqual(calls, [["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]])

    def test_ducking_does_not_unmute_if_previously_muted(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "Volume: 0.80 [MUTED]\n", "")

        with patch("voice_ducking.subprocess.run", side_effect=fake_run):
            ducking = VoiceDucking(_config(duck_percent=15))
            with ducking:
                self.assertFalse(ducking.applied)

        self.assertEqual(calls, [["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]])

    def test_restore_happens_when_recording_raises(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            if command[1] == "get-volume":
                return subprocess.CompletedProcess(command, 0, "Volume: 0.50\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("voice_ducking.subprocess.run", side_effect=fake_run):
            ducking = VoiceDucking(_config(duck_percent=15))
            with self.assertRaises(RuntimeError):
                with ducking:
                    raise RuntimeError("recording failed")

        self.assertTrue(ducking.restored)
        self.assertIn(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "50%"], calls)

    def test_missing_wpctl_does_not_crash(self) -> None:
        warnings: list[str] = []

        with patch("voice_ducking.subprocess.run", side_effect=FileNotFoundError):
            ducking = VoiceDucking(_config(), warn=warnings.append)
            with ducking:
                pass

        self.assertTrue(ducking.duck_failed)
        self.assertEqual(warnings, [DUCK_WARNING])


if __name__ == "__main__":
    unittest.main()
