from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import minecraft_server
from actions.minecraft_server import MinecraftServerState
from actions.minecraft_server import ProcessCandidate
from actions.minecraft_server import ProcessScanResult
from command_router import CommandRouter
from config import MinecraftServerConfig
from intent_resolver import IntentResolver
from intent_resolver import should_pass_to_router
from schemas import ActionIntent


class MinecraftServerManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.default_server_cwd = "/tmp/arvis-test-minecraft-server"
        self._config_patcher = patch(
            "actions.minecraft_server.get_minecraft_server_config",
            side_effect=lambda: self._server_config(self.default_server_cwd),
        )
        self._config_patcher.start()

    def tearDown(self) -> None:
        self._config_patcher.stop()

    def test_server_not_configured_when_env_config_missing(self) -> None:
        with patch("actions.minecraft_server.get_minecraft_server_config", return_value=None), patch(
            "actions.minecraft_server._scan_process_candidates",
        ) as scan:
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_status", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.reason_code, "minecraft_server_not_configured")
        scan.assert_not_called()

    def test_server_status_when_not_running(self) -> None:
        with patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
            "actions.minecraft_server._scan_process_candidates",
            return_value=self._scan(),
        ):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_status", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.reason_code, "minecraft_server_not_running")
        self.assertIn("running: False", result.details or "")

    def test_status_unmanaged_running_explains_control_unavailable(self) -> None:
        with patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
            "actions.minecraft_server._scan_process_candidates",
            return_value=self._scan(self._candidate("strong", comm="java", cmdline="java -jar neoforge-server.jar")),
        ):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_status", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.reason_code, "minecraft_server_running")
        self.assertIn("not managed by Arvis/tmux", result.message)
        self.assertIn("Arvis will not start a duplicate", result.message)
        self.assertIn("running: True", result.details or "")
        self.assertIn("managed_by_tmux: False", result.details or "")
        self.assertIn("unmanaged_java_process_found: True", result.details or "")
        self.assertIn("control_available: False", result.details or "")
        self.assertIn("Server must be stopped manually once", result.details or "")

    def test_process_with_cwd_bash_is_weak_candidate_not_running(self) -> None:
        candidate = minecraft_server._classify_process_candidate(
            111,
            "bash",
            str(Path(self.default_server_cwd)),
            "bash",
            "bash",
        )

        self.assertEqual(candidate.match_strength, "weak")
        self.assertEqual(candidate.classification, "weak_cwd")
        self.assertIn("ignored_process_name:bash", candidate.match_reasons)

        with patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
            "actions.minecraft_server._scan_process_candidates",
            return_value=self._scan(candidate),
        ):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_status", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.reason_code, "minecraft_process_detection_ambiguous")
        self.assertIn("none look like a running Minecraft Java server", result.message)
        self.assertIn("possible_unmanaged_process_found: True", result.details or "")
        self.assertIn("unmanaged_java_process_found: False", result.details or "")

    def test_process_with_cwd_python_is_weak_candidate_not_running(self) -> None:
        candidate = minecraft_server._classify_process_candidate(
            112,
            "python3",
            str(Path(self.default_server_cwd)),
            "python3 script.py",
            "python3 script.py",
        )

        self.assertEqual(candidate.match_strength, "weak")
        self.assertEqual(candidate.classification, "weak_cwd")
        self.assertIn("ignored_process_name:python3", candidate.match_reasons)

    def test_java_without_minecraft_markers_is_weak_candidate(self) -> None:
        candidate = minecraft_server._classify_process_candidate(
            113,
            "java",
            str(Path(self.default_server_cwd)),
            "java -version",
            "java -version",
        )

        self.assertEqual(candidate.match_strength, "weak")
        self.assertEqual(candidate.classification, "weak_cwd")
        self.assertIn("java_process", candidate.match_reasons)

    def test_java_with_minecraft_markers_is_strong_candidate(self) -> None:
        candidate = minecraft_server._classify_process_candidate(
            114,
            "java",
            str(Path(self.default_server_cwd)),
            "java @user_jvm_args.txt -jar neoforge-server.jar",
            "java @user_jvm_args.txt -jar neoforge-server.jar",
        )

        self.assertEqual(candidate.match_strength, "strong")
        self.assertEqual(candidate.classification, "unmanaged_server")
        self.assertIn("java_process", candidate.match_reasons)
        self.assertIn("marker:neoforge", candidate.match_reasons)

    def test_tmux_exists_strong_server_candidate_is_managed_server(self) -> None:
        candidate = minecraft_server._classify_process_candidate(
            115,
            "java",
            str(Path(self.default_server_cwd)),
            "java -jar server.jar",
            "java -jar server.jar",
            tmux_exists=True,
        )

        self.assertEqual(candidate.match_strength, "strong")
        self.assertEqual(candidate.classification, "managed_server")

    def test_prismlauncher_client_is_ignored_client(self) -> None:
        candidate = minecraft_server._classify_process_candidate(
            116,
            "java",
            "/tmp/arvis-test-user",
            "java -cp NewLaunch.jar org.prismlauncher.EntryPoint",
            "java -cp NewLaunch.jar org.prismlauncher.EntryPoint",
            cwd_inside_server_dir=False,
        )

        self.assertEqual(candidate.classification, "ignored_client")
        self.assertEqual(candidate.match_strength, "weak")

    def test_start_when_tmux_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "start-server.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            with self._server_cwd(tmpdir), patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
                "actions.minecraft_server._scan_process_candidates",
                return_value=self._scan(),
            ), patch("actions.minecraft_server.shutil.which", return_value=None):
                result = minecraft_server.execute_minecraft_server_action("minecraft_server_start", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.reason_code, "tmux_missing")
        self.assertIn("tmux is required", result.message)

    def test_start_when_start_script_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self._server_cwd(tmpdir), patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
                "actions.minecraft_server._scan_process_candidates",
                return_value=self._scan(),
            ):
                result = minecraft_server.execute_minecraft_server_action("minecraft_server_start", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.reason_code, "minecraft_start_script_missing")

    def test_start_when_already_running_via_tmux(self) -> None:
        with patch("actions.minecraft_server._tmux_session_exists", return_value=True), patch(
            "actions.minecraft_server._scan_process_candidates",
            return_value=self._scan(),
        ):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_start", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "already_running")
        self.assertEqual(result.reason_code, "minecraft_server_already_running")

    def test_start_when_already_running_via_cwd_process(self) -> None:
        with patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
            "actions.minecraft_server._scan_process_candidates",
            return_value=self._scan(self._candidate("strong", comm="java", cmdline="java -jar server.jar")),
        ):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_start", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "already_running")
        self.assertEqual(result.reason_code, "minecraft_server_already_running_unmanaged")
        self.assertIn("outside Arvis/tmux", result.message)
        self.assertIn("avoid duplicate server process", result.message)
        self.assertIn("unmanaged_java_process_found: True", result.details or "")
        self.assertIn("unmanaged_server_process_found: True", result.details or "")

    def test_start_with_only_weak_candidate_does_not_return_already_running(self) -> None:
        weak_candidate = self._candidate("weak", comm="bash", cmdline="bash")
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "start-server.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            with self._server_cwd(tmpdir), patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
                "actions.minecraft_server._scan_process_candidates",
                return_value=self._scan(weak_candidate),
            ), patch("actions.minecraft_server.shutil.which", return_value="/usr/bin/tmux"):
                result = minecraft_server.execute_minecraft_server_action(
                    "minecraft_server_start",
                    "default",
                    dry_run=True,
                )

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(result.reason_code, "minecraft_server_start_dry_run")
        self.assertIn("weak process candidates", result.details or "")
        self.assertIn("match_strength: weak", result.details or "")

    def test_diagnostics_returns_process_candidates(self) -> None:
        weak_candidate = self._candidate("weak", comm="bash", cmdline="bash")
        strong_candidate = self._candidate("strong", comm="java", cmdline="java -jar server.jar")
        with patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
            "actions.minecraft_server._scan_process_candidates",
            return_value=self._scan(weak_candidate, strong_candidate),
        ):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_diagnostics", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.reason_code, "minecraft_server_diagnostics")
        self.assertIn("process_candidates:", result.details or "")
        self.assertIn("match_strength: weak", result.details or "")
        self.assertIn("match_strength: strong", result.details or "")
        self.assertIn("classification: weak_cwd", result.details or "")
        self.assertIn("classification: unmanaged_server", result.details or "")
        self.assertIn("match_reasons:", result.details or "")

    def test_tmux_session_takes_priority_as_managed_running(self) -> None:
        weak_candidate = self._candidate("weak", comm="bash", cmdline="bash")
        with patch("actions.minecraft_server._tmux_session_exists", return_value=True), patch(
            "actions.minecraft_server._scan_process_candidates",
            return_value=self._scan(weak_candidate),
        ):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_status", "default")

        self.assertEqual(result.status, "executed")
        self.assertEqual(result.reason_code, "minecraft_server_running")
        self.assertIn("managed_by_tmux: True", result.details or "")
        self.assertIn("control_available: True", result.details or "")

    def test_status_tmux_with_strong_candidate_does_not_show_unmanaged_true(self) -> None:
        candidate = self._candidate("strong", comm="java", cmdline="java -jar server.jar", classification="managed_server")
        with patch("actions.minecraft_server._tmux_session_exists", return_value=True), patch(
            "actions.minecraft_server._scan_process_candidates",
            return_value=self._scan(candidate),
        ):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_status", "default")

        self.assertEqual(result.status, "executed")
        self.assertIn("managed_by_tmux: True", result.details or "")
        self.assertIn("managed_server_process_found: True", result.details or "")
        self.assertIn("unmanaged_server_process_found: False", result.details or "")
        self.assertIn("strong_unmanaged_process_found: False", result.details or "")
        self.assertIn("classification: managed_server", result.details or "")

    def test_duplicate_server_processes_detected(self) -> None:
        first = self._candidate("strong", pid=123, comm="java", cmdline="java -jar server.jar")
        second = self._candidate("strong", pid=456, comm="java", cmdline="java -jar neoforge-server.jar")
        with patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
            "actions.minecraft_server._scan_process_candidates",
            return_value=self._scan(first, second),
        ):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_status", "default")

        self.assertIn("duplicate_server_processes_detected: True", result.details or "")
        self.assertIn("Multiple Minecraft server Java processes detected", result.details or "")

    def test_metrics_action_returns_memory_and_cpu_fields(self) -> None:
        candidate = self._candidate(
            "strong",
            pid=321,
            comm="java",
            cmdline="java -jar server.jar",
            cpu_percent=12.5,
            memory_rss_kb=2097152,
            uptime_seconds=33.0,
        )
        client = self._candidate(
            "weak",
            pid=654,
            comm="java",
            cmdline="java -cp NewLaunch.jar org.prismlauncher.EntryPoint",
            classification="ignored_client",
        )
        with patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
            "actions.minecraft_server._scan_process_candidates",
            return_value=self._scan(candidate, client),
        ):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_metrics", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.reason_code, "minecraft_server_metrics")
        self.assertIn("server_pids: 321", result.details or "")
        self.assertIn("cpu_percent: 12.5", result.details or "")
        self.assertIn("memory_rss_mb: 2048.0", result.details or "")
        self.assertIn("memory_rss_gb: 2.0", result.details or "")
        self.assertIn("client_processes_detected: 1", result.details or "")

    def test_start_launches_tmux_with_safe_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "start-server.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            with self._server_cwd(tmpdir), patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
                "actions.minecraft_server._scan_process_candidates",
                return_value=self._scan(),
            ), patch("actions.minecraft_server.shutil.which", return_value="/usr/bin/tmux"), patch(
                "actions.minecraft_server._run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ) as run:
                result = minecraft_server.execute_minecraft_server_action("minecraft_server_start", "default")

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.reason_code, "minecraft_server_started")
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:6], ["tmux", "new-session", "-d", "-s", "arvis_minecraft_default", "-c"])
        self.assertEqual(command[-2:], ["bash", "./start-server.sh"])

    def test_stop_when_managed_tmux_exists_sends_stop(self) -> None:
        state = self._state(running=True, managed=True, tmux=True, unmanaged=False)
        with patch("actions.minecraft_server._get_state", return_value=state), patch(
            "actions.minecraft_server._run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as run, patch("actions.minecraft_server._tmux_session_exists", return_value=False):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_stop", "default")

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.reason_code, "minecraft_server_stopped")
        run.assert_called_once_with(["tmux", "send-keys", "-t", "arvis_minecraft_default", "stop", "Enter"])

    def test_stop_unmanaged_process_is_unsupported(self) -> None:
        state = self._state(running=True, managed=False, tmux=False, unmanaged=True)
        with patch("actions.minecraft_server._get_state", return_value=state):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_stop", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.reason_code, "minecraft_server_unmanaged")
        self.assertIn("cannot safely send the stop command", result.message)
        self.assertIn("Stop it manually", result.message)

    def test_restart_managed_server_stops_then_starts(self) -> None:
        state = self._state(running=True, managed=True, tmux=True, unmanaged=False)
        with patch("actions.minecraft_server._get_state", return_value=state), patch(
            "actions.minecraft_server._stop",
            return_value=minecraft_server.MinecraftServerActionResult(
                executed=True,
                status="executed",
                reason_code="minecraft_server_stopped",
                message="stopped",
            ),
        ) as stop, patch(
            "actions.minecraft_server._start",
            return_value=minecraft_server.MinecraftServerActionResult(
                executed=True,
                status="executed",
                reason_code="minecraft_server_started",
                message="started",
            ),
        ) as start:
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_restart", "default")

        self.assertTrue(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.reason_code, "minecraft_server_restarted")
        stop.assert_called_once_with(dry_run=False)
        start.assert_called_once_with(dry_run=False)

    def test_restart_unmanaged_server_is_unsupported(self) -> None:
        state = self._state(running=True, managed=False, tmux=False, unmanaged=True)
        with patch("actions.minecraft_server._get_state", return_value=state):
            result = minecraft_server.execute_minecraft_server_action("minecraft_server_restart", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.reason_code, "minecraft_server_unmanaged_restart")
        self.assertIn("Cannot restart unmanaged server safely", result.message)

    def test_logs_still_work_when_server_is_unmanaged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"
            log_dir.mkdir()
            (log_dir / "latest.log").write_text("one\ntwo\n", encoding="utf-8")
            with self._server_cwd(tmpdir), patch("actions.minecraft_server._tmux_session_exists", return_value=False), patch(
                "actions.minecraft_server._scan_process_candidates",
                return_value=self._scan(self._candidate("strong", comm="java", cmdline="java -jar server.jar")),
            ):
                result = minecraft_server.execute_minecraft_server_action("minecraft_server_logs", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.reason_code, "minecraft_logs_read")
        self.assertIn("two", result.details or "")

    def test_logs_latest_log_exists_returns_last_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"
            log_dir.mkdir()
            (log_dir / "latest.log").write_text("\n".join(f"line {index}" for index in range(50)), encoding="utf-8")
            with self._server_cwd(tmpdir):
                result = minecraft_server.execute_minecraft_server_action("minecraft_server_logs", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "executed")
        self.assertEqual(result.reason_code, "minecraft_logs_read")
        self.assertNotIn("line 0", result.details or "")
        self.assertIn("line 49", result.details or "")

    def test_logs_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self._server_cwd(tmpdir):
                result = minecraft_server.execute_minecraft_server_action("minecraft_server_logs", "default")

        self.assertFalse(result.executed)
        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.reason_code, "minecraft_log_not_found")

    def test_router_legacy_start_minecraft_server_aliases_to_start(self) -> None:
        with patch(
            "command_router.execute_minecraft_server_action",
            return_value=minecraft_server.MinecraftServerActionResult(
                executed=False,
                status="dry_run",
                reason_code="minecraft_server_start_dry_run",
                message="dry run",
            ),
        ) as execute:
            result = CommandRouter(dry_run=True).route(
                ActionIntent(
                    action="start_minecraft_server",
                    target="minecraft_server",
                    risk="safe",
                    need_confirmation=False,
                ),
                user_text="запусти майн сервер",
            )

        self.assertEqual(result.normalized_action, "minecraft_server_start")
        self.assertEqual(result.normalized_target, "default")
        self.assertEqual(result.status, "dry_run")
        execute.assert_called_once_with("minecraft_server_start", "default", dry_run=True)

    def test_resolver_start_phrase(self) -> None:
        resolved = IntentResolver().resolve("запусти майн сервер", use_llm=False)

        self.assertEqual(resolved.action, "minecraft_server_start")
        self.assertEqual(resolved.target, "default")
        self.assertTrue(should_pass_to_router(resolved))

    def test_resolver_status_phrase(self) -> None:
        resolved = IntentResolver().resolve("статус майн сервера", use_llm=False)

        self.assertEqual(resolved.action, "minecraft_server_status")
        self.assertEqual(resolved.target, "default")
        self.assertIn("local Minecraft server manager", resolved.reason)
        self.assertTrue(should_pass_to_router(resolved))

    def test_resolver_local_status_phrases_do_not_need_ip_domain(self) -> None:
        phrases = [
            "статус сервера",
            "статус сервера",
            "перевір майн сервер",
            "чи працює майн сервер",
        ]

        for phrase in phrases:
            with self.subTest(phrase=phrase):
                resolved = IntentResolver().resolve(phrase, use_llm=False)

                self.assertEqual(resolved.action, "minecraft_server_status")
                self.assertEqual(resolved.target, "default")
                self.assertGreaterEqual(resolved.confidence, 0.65)
                self.assertIn("local Minecraft server manager", resolved.reason)
                self.assertNotIn("ip", resolved.reason.lower())
                self.assertNotIn("domain", resolved.reason.lower())
                self.assertTrue(should_pass_to_router(resolved))

    def test_resolver_restart_phrase(self) -> None:
        resolved = IntentResolver().resolve("перезапусти майн сервер", use_llm=False)

        self.assertEqual(resolved.action, "minecraft_server_restart")
        self.assertEqual(resolved.target, "default")
        self.assertTrue(should_pass_to_router(resolved))

    def test_resolver_diagnostics_phrase(self) -> None:
        resolved = IntentResolver().resolve("покажи процеси майн сервера", use_llm=False)

        self.assertEqual(resolved.action, "minecraft_server_diagnostics")
        self.assertEqual(resolved.target, "default")
        self.assertTrue(should_pass_to_router(resolved))

    def test_resolver_metrics_phrases(self) -> None:
        phrases = ["скільки пам'яті хаває сервер", "навантаження майн сервера"]

        for phrase in phrases:
            with self.subTest(phrase=phrase):
                resolved = IntentResolver().resolve(phrase, use_llm=False)

                self.assertEqual(resolved.action, "minecraft_server_metrics")
                self.assertEqual(resolved.target, "default")
                self.assertTrue(should_pass_to_router(resolved))

    def test_dangerous_shell_still_blocked(self) -> None:
        resolved = IntentResolver().resolve("виконай bash rm -rf /", use_llm=False)

        self.assertIsNone(resolved.action)
        self.assertEqual(resolved.risk, "dangerous")
        self.assertFalse(should_pass_to_router(resolved))

    def _state(self, running: bool, managed: bool, tmux: bool, unmanaged: bool) -> MinecraftServerState:
        candidates = []
        if unmanaged:
            candidates.append(self._candidate("strong", comm="java", cmdline="java -jar server.jar"))
        return MinecraftServerState(
            running=running,
            managed_by_tmux=managed,
            tmux_session_exists=tmux,
            unmanaged_java_process_found=unmanaged,
            strong_unmanaged_process_found=unmanaged,
            managed_server_process_found=managed,
            unmanaged_server_process_found=unmanaged,
            duplicate_server_processes_detected=False,
            weak_process_candidates_found=False,
            ignored_client_processes_found=False,
            ignored_client_processes_count=0,
            possible_unmanaged_process_found=False,
            control_available=managed,
            cwd=str(Path(self.default_server_cwd)),
            start_script_exists=True,
            start_command_configured=True,
            process_candidates=candidates,
        )

    def _server_config(self, cwd: str, command: list[str] | None = None) -> MinecraftServerConfig:
        return MinecraftServerConfig(
            key="default",
            name="Minecraft server",
            cwd=Path(cwd),
            command=command or ["bash", "./start-server.sh"],
            tmux_session="arvis_minecraft_default",
        )

    def _server_cwd(self, cwd: str):
        return patch("actions.minecraft_server.get_minecraft_server_config", return_value=self._server_config(cwd))

    def _scan(self, *candidates: ProcessCandidate) -> ProcessScanResult:
        return ProcessScanResult(list(candidates))

    def _candidate(
        self,
        strength: str,
        pid: int = 123,
        comm: str = "bash",
        cmdline: str = "bash",
        classification: str | None = None,
        cpu_percent: float | None = None,
        memory_rss_kb: int | None = None,
        uptime_seconds: float | None = None,
    ) -> ProcessCandidate:
        classification = classification or ("unmanaged_server" if strength == "strong" else "weak_cwd")
        reasons = ["cwd_inside_server_dir"]
        if "java" in comm.lower() or "java" in cmdline.lower():
            reasons.append("java_process")
        if "server.jar" in cmdline.lower():
            reasons.append("marker:server.jar")
        if "neoforge" in cmdline.lower():
            reasons.append("marker:neoforge")
        if comm.lower() in minecraft_server.IGNORED_NON_SERVER_PROCESSES:
            reasons.append(f"ignored_process_name:{comm.lower()}")
        memory_rss_mb = round(memory_rss_kb / 1024, 2) if memory_rss_kb is not None else None
        memory_rss_gb = round(memory_rss_mb / 1024, 3) if memory_rss_mb is not None else None
        return ProcessCandidate(
            pid=pid,
            comm=comm,
            cwd=str(Path(self.default_server_cwd)),
            cmdline_short=cmdline,
            match_strength=strength,
            match_reasons=reasons,
            classification=classification,
            ppid=1,
            cpu_percent=cpu_percent,
            memory_rss_kb=memory_rss_kb,
            memory_rss_mb=memory_rss_mb,
            memory_rss_gb=memory_rss_gb,
            uptime_seconds=uptime_seconds,
        )


if __name__ == "__main__":
    unittest.main()
