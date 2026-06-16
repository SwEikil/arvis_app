from __future__ import annotations

import unittest
from unittest.mock import Mock
from unittest.mock import patch

import doctor
import main
from voice_config import VoiceConfig
from voice_input import VoiceTranscriptionResult


class FakeVoiceDucking:
    instances: list[FakeVoiceDucking] = []

    def __init__(self, config: VoiceConfig, warn=None) -> None:
        self.config = config
        self.warn = warn
        self.applied = True
        self.restored = False
        self.entered = False
        self.exited = False
        self.instances.append(self)

    def __enter__(self) -> FakeVoiceDucking:
        self.entered = True
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self.exited = True
        self.restored = True
        return False


class MainReloadCommandTests(unittest.TestCase):
    def test_reload_command_is_recognized(self) -> None:
        router = Mock()
        router.dry_run = False

        with patch("main.save_reload_state", return_value=True) as save_state, patch(
            "main.restart_current_process",
        ) as restart:
            result = main.handle_command(
                "/reload",
                [{"role": "user", "content": "hello"}],
                "summary",
                True,
                router,
                [{"counter": 1}],
                1,
            )

        self.assertTrue(result.handled)
        self.assertFalse(result.exit_requested)
        save_state.assert_called_once()
        restart.assert_called_once()

    def test_restart_command_is_recognized(self) -> None:
        router = Mock()
        router.dry_run = True

        with patch("main.save_reload_state", return_value=True), patch("main.restart_current_process") as restart:
            result = main.handle_command("/restart", [], "", False, router, [], 0)

        self.assertTrue(result.handled)
        restart.assert_called_once()

    def test_reload_does_not_call_router(self) -> None:
        router = Mock()
        router.dry_run = True

        with patch("main.save_reload_state", return_value=True), patch("main.restart_current_process"):
            main.handle_command("/reload", [], "", False, router, [], 0)

        router.route.assert_not_called()

    def test_reload_does_not_touch_minecraft_server(self) -> None:
        router = Mock()
        router.dry_run = True

        with patch("main.save_reload_state", return_value=True), patch("main.restart_current_process"), patch(
            "command_router.execute_minecraft_server_action",
        ) as minecraft_action:
            main.handle_command("/reload", [], "", False, router, [], 0)

        minecraft_action.assert_not_called()

    def test_reload_restart_error_is_reported_without_crashing(self) -> None:
        router = Mock()
        router.dry_run = True

        with patch("main.save_reload_state", return_value=True), patch(
            "main.restart_current_process",
            side_effect=OSError("exec failed"),
        ):
            result = main.handle_command("/reload", [], "summary", True, router, [], 0)

        self.assertTrue(result.handled)
        self.assertFalse(result.exit_requested)
        self.assertEqual(result.session_summary, "summary")
        self.assertTrue(result.debug)

    def test_restore_runtime_state_restores_minimal_fields(self) -> None:
        router = Mock()
        router.dry_run = True
        active_history: list[dict[str, str]] = []
        command_history: list[dict[str, object]] = []

        session_summary, debug, command_counter, restored = main.restore_runtime_state(
            {
                "dry_run": False,
                "debug": True,
                "session_summary": "summary",
                "active_history": [{"role": "user", "content": "hello"}],
                "command_history": [{"counter": 3, "normalized_action": "volume_up"}],
            },
            active_history,
            command_history,
            router,
        )

        self.assertTrue(restored)
        self.assertFalse(router.dry_run)
        self.assertTrue(debug)
        self.assertEqual(session_summary, "summary")
        self.assertEqual(active_history, [{"role": "user", "content": "hello"}])
        self.assertEqual(command_history, [{"counter": 3, "normalized_action": "volume_up"}])
        self.assertEqual(command_counter, 3)

    def test_doctor_repl_command_is_recognized_without_router(self) -> None:
        router = Mock()
        router.dry_run = True
        checks = [doctor.DoctorCheck("ok", "Runtime", "Python found")]

        with patch("main.run_doctor", return_value=checks), patch("main.render_text_report", return_value="doctor report"):
            result = main.handle_command("/doctor", [], "summary", False, router, [], 0)

        self.assertTrue(result.handled)
        self.assertFalse(result.exit_requested)
        router.route.assert_not_called()

    def test_doctor_cli_command_uses_doctor_runner(self) -> None:
        with patch("main.run_doctor_cli", return_value=0) as run_doctor_cli:
            exit_code = main.cli(["doctor", "--json"])

        self.assertEqual(exit_code, 0)
        run_doctor_cli.assert_called_once_with(["--json"])

    def test_actions_repl_command_is_recognized_without_router(self) -> None:
        router = Mock()
        router.dry_run = True

        with patch("main.show_actions") as show_actions:
            result = main.handle_command("/actions", [], "summary", False, router, [], 0)

        self.assertTrue(result.handled)
        self.assertFalse(result.exit_requested)
        show_actions.assert_called_once()
        router.route.assert_not_called()

    def test_voice_status_command_is_recognized(self) -> None:
        router = Mock()
        router.dry_run = True

        with patch("main.show_voice_status") as show_voice_status:
            result = main.handle_command("/voice status", [], "summary", False, router, [], 7)

        self.assertTrue(result.handled)
        show_voice_status.assert_called_once()
        router.route.assert_not_called()

    def test_voice_test_disabled_does_not_route(self) -> None:
        router = Mock()
        router.dry_run = True
        process_text = Mock()

        with patch("main.load_voice_config", return_value=self._voice_config(enabled=False)):
            result = main.handle_command("/voice test", [], "summary", False, router, [], 7, process_text)

        self.assertTrue(result.handled)
        process_text.assert_not_called()
        router.route.assert_not_called()

    def test_voice_once_disabled_does_not_route(self) -> None:
        router = Mock()
        router.dry_run = True
        process_text = Mock()

        with patch("main.load_voice_config", return_value=self._voice_config(enabled=False)):
            result = main.handle_command("/voice once", [], "summary", False, router, [], 7, process_text)

        self.assertTrue(result.handled)
        process_text.assert_not_called()
        router.route.assert_not_called()

    def test_voice_test_recognizes_but_does_not_execute_pipeline(self) -> None:
        router = Mock()
        router.dry_run = True
        process_text = Mock()
        FakeVoiceDucking.instances.clear()

        with patch("main.load_voice_config", return_value=self._voice_config(enabled=True)), patch(
            "main.transcribe_once",
            return_value=VoiceTranscriptionResult(True, text="постав звук на 30"),
        ), patch("main.VoiceDucking", FakeVoiceDucking):
            result = main.handle_command("/voice test", [], "summary", False, router, [], 7, process_text)

        self.assertTrue(result.handled)
        process_text.assert_not_called()
        router.route.assert_not_called()
        self.assertEqual(len(FakeVoiceDucking.instances), 1)
        self.assertTrue(FakeVoiceDucking.instances[0].entered)
        self.assertTrue(FakeVoiceDucking.instances[0].exited)

    def test_voice_once_routes_recognized_text_through_pipeline(self) -> None:
        router = Mock()
        router.dry_run = True
        process_text = Mock(return_value=("updated", 8))
        FakeVoiceDucking.instances.clear()

        with patch("main.load_voice_config", return_value=self._voice_config(enabled=True)), patch(
            "main.transcribe_once",
            return_value=VoiceTranscriptionResult(True, text="постав звук на 30"),
        ), patch("main.VoiceDucking", FakeVoiceDucking):
            result = main.handle_command("/voice once", [], "summary", False, router, [], 7, process_text)

        self.assertTrue(result.handled)
        self.assertEqual(result.session_summary, "updated")
        self.assertEqual(result.command_counter, 8)
        process_text.assert_called_once_with("постав звук на 30")
        self.assertEqual(len(FakeVoiceDucking.instances), 1)
        self.assertTrue(FakeVoiceDucking.instances[0].entered)
        self.assertTrue(FakeVoiceDucking.instances[0].exited)

    def _voice_config(self, enabled: bool) -> VoiceConfig:
        return VoiceConfig(
            enabled=enabled,
            stt_backend="faster_whisper",
            stt_model="small",
            stt_device="auto",
            stt_compute_type="auto",
            mic_device="",
            record_seconds=1,
            language="auto",
        )


if __name__ == "__main__":
    unittest.main()
