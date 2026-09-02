from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import doctor


class DoctorTests(unittest.TestCase):
    def test_text_renderer_includes_status_fix_and_summary(self) -> None:
        checks = [
            doctor.DoctorCheck("ok", "Runtime", "Python found"),
            doctor.DoctorCheck("warn", "Voice", "STT not configured", fix="Set STT backend if voice is needed."),
        ]

        report = doctor.render_text_report(checks)

        self.assertIn("[OK] Runtime: Python found", report)
        self.assertIn("[WARN] Voice: STT not configured", report)
        self.assertIn("Fix: Set STT backend", report)
        self.assertIn("Doctor summary:", report)
        self.assertIn("- OK: 1", report)
        self.assertIn("- Warnings: 1", report)

    def test_json_renderer_outputs_structured_json(self) -> None:
        checks = [doctor.DoctorCheck("fail", "Config", "Bad token", details=doctor.redact_value("OPENAI_API_KEY", "sk-1234567890abcd"))]

        payload = json.loads(doctor.render_json_report(checks))

        self.assertEqual(payload["summary"]["fail"], 1)
        self.assertEqual(payload["checks"][0]["status"], "fail")
        self.assertNotIn("1234567890", payload["checks"][0]["details"])

    def test_env_missing_is_info_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env.example").write_text("OLLAMA_HOST=http://127.0.0.1:11434\n", encoding="utf-8")

            checks = doctor.check_local_config(root, {}, doctor.DoctorOptions())

        env_check = next(check for check in checks if check.title.startswith(".env not found"))
        self.assertEqual(env_check.status, "info")

    def test_env_example_secret_like_content_fails_privacy_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".gitignore").write_text("\n".join(doctor.REQUIRED_GITIGNORE_PATTERNS), encoding="utf-8")
            (root / ".env.example").write_text("OPENAI_API_KEY=sk-1234567890abcd\n", encoding="utf-8")

            checks = doctor.check_privacy_safety(root, doctor.DoctorOptions())

        self.assertTrue(any(check.status == "fail" and ".env.example" in check.title for check in checks))

    def test_git_tracked_env_is_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch("doctor._git_tracked_files", return_value=[".env", "main.py"]):
                checks = doctor.check_git_safety(root)

        self.assertTrue(any(check.status == "fail" and "Secret-like" in check.title for check in checks))

    def test_ollama_offline_is_warning_not_failure(self) -> None:
        with patch("requests.get", side_effect=requests.ConnectionError):
            checks = doctor.check_ollama_backend(
                {"OLLAMA_HOST": "http://127.0.0.1:11434", "ARVIS_MODEL": "arvis"},
                doctor.DoctorOptions(),
            )

        self.assertEqual(checks[0].status, "warn")
        self.assertIn("offline", checks[0].title.lower())
        self.assertTrue(any(check.category == "Offline mode" for check in checks))

    def test_strict_treats_warning_as_failure_exit_code(self) -> None:
        checks = [doctor.DoctorCheck("warn", "Audio", "playerctl missing")]

        self.assertEqual(doctor.doctor_exit_code(checks, doctor.DoctorOptions(strict=True)), 1)
        self.assertEqual(doctor.doctor_exit_code(checks, doctor.DoctorOptions(strict=False)), 0)

    def test_fix_only_creates_safe_local_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            checks = doctor.check_storage(root, doctor.DoctorOptions(fix=True))

            self.assertTrue((root / "logs").is_dir())
            self.assertTrue((root / ".cache").is_dir())
            self.assertTrue((root / ".runtime").is_dir())

        self.assertTrue(all(check.status == "ok" for check in checks))

    def test_missing_logs_and_cache_are_warnings_without_fix(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            checks = doctor.check_storage(root, doctor.DoctorOptions())

        by_title = {check.title: check.status for check in checks}
        self.assertEqual(by_title["logs/ is missing"], "warn")
        self.assertEqual(by_title[".cache/ is missing"], "warn")

    def test_requirements_check_can_be_mocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            requirements = root / "requirements.txt"
            requirements.write_text("missing-package\n", encoding="utf-8")

            with patch("doctor.importlib.util.find_spec", return_value=None):
                checks = doctor._check_requirements(requirements)

        self.assertEqual(checks[0].status, "fail")
        self.assertIn("missing-package", checks[0].details)

    def test_brave_missing_fallback_is_info_not_warning(self) -> None:
        with patch("doctor.importlib.import_module"), patch("doctor.shutil.which", return_value=None):
            checks = doctor.check_action_readiness({}, doctor.DoctorOptions())

        brave_check = next(check for check in checks if check.category == "Apps" and "brave" in check.title)
        self.assertEqual(brave_check.status, "info")

    def test_explicit_brave_command_missing_is_warning(self) -> None:
        with patch("doctor.importlib.import_module"), patch("doctor.shutil.which", return_value=None):
            checks = doctor.check_action_readiness({"BRAVE_COMMAND": "/missing/brave"}, doctor.DoctorOptions())

        brave_check = next(check for check in checks if check.category == "Apps" and "brave" in check.title)
        self.assertEqual(brave_check.status, "warn")
        self.assertIn("explicitly configured", brave_check.title)

    def test_fix_does_not_overwrite_existing_env_example(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_example = root / ".env.example"
            env_example.write_text("KEEP=existing\n", encoding="utf-8")

            doctor.check_local_config(root, {}, doctor.DoctorOptions(fix=True))

            self.assertEqual(env_example.read_text(encoding="utf-8"), "KEEP=existing\n")

    def test_browser_observer_headful_is_known_env_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env.example").write_text("ARVIS_BROWSER_OBSERVER_HEADFUL=false\n", encoding="utf-8")

            checks = doctor.check_local_config(
                root,
                {"ARVIS_BROWSER_OBSERVER_HEADFUL": "true"},
                doctor.DoctorOptions(verbose=True),
            )

        self.assertFalse(any(check.title == "Unknown local env keys are present" for check in checks))

    def test_mcp_access_settings_are_known_env_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env.example").write_text("ARVIS_MCP_PROFILE=codex\n", encoding="utf-8")

            checks = doctor.check_local_config(
                root,
                {
                    "ARVIS_MCP_PROFILE": "codex",
                    "ARVIS_MCP_PROJECT_ROOT": "/path/to/arvis",
                    "ARVIS_MCP_ALLOWED_ROOTS": "/path/to/arvis",
                },
                doctor.DoctorOptions(verbose=True),
            )

        self.assertFalse(any(check.title == "Unknown local env keys are present" for check in checks))

    def test_safe_command_settings_are_known_env_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env.example").write_text(
                "# ARVIS_SAFE_COMMAND_CONTROL_ENABLED=false\n",
                encoding="utf-8",
            )

            checks = doctor.check_local_config(
                root,
                {
                    "ARVIS_SAFE_COMMAND_CONTROL_ENABLED": "false",
                    "ARVIS_SAFE_COMMAND_CONFIG": "/absolute/path/to/safe-commands.json",
                    "ARVIS_SAFE_COMMAND_HOST_CONTROL_ENABLED": "false",
                },
                doctor.DoctorOptions(verbose=True),
            )

        self.assertFalse(any(check.title == "Unknown local env keys are present" for check in checks))

    def test_safe_git_settings_are_known_and_valid_policy_values_stay_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env.example").write_text(
                "# ARVIS_SAFE_GIT_CONTROL_ENABLED=false\n",
                encoding="utf-8",
            )
            private_values = {
                "ARVIS_MCP_WRITABLE_ROOTS": str(root),
                "ARVIS_SAFE_GIT_CONTROL_ENABLED": "true",
                "ARVIS_SAFE_GIT_REMOTE_NAME": "private-remote",
                "ARVIS_SAFE_GIT_EXPECTED_REMOTE_URL": "https://private.example.invalid/team/project.git",
                "ARVIS_SAFE_GIT_PUBLIC_NAME": "Private Local Name",
                "ARVIS_SAFE_GIT_PUBLIC_EMAIL": "private@example.invalid",
                "ARVIS_SAFE_GIT_PUSH_ENABLED": "true",
                "ARVIS_SAFE_GIT_HISTORY_REWRITE_ENABLED": "false",
            }

            checks = doctor.check_local_config(
                root,
                private_values,
                doctor.DoctorOptions(verbose=True),
            )

        self.assertFalse(any(check.title == "Unknown local env keys are present" for check in checks))
        self.assertTrue(
            any(check.category == "Safe Git" and check.title.endswith("policy is valid") for check in checks)
        )
        rendered = repr(checks)
        for key in (
            "ARVIS_SAFE_GIT_REMOTE_NAME",
            "ARVIS_SAFE_GIT_EXPECTED_REMOTE_URL",
            "ARVIS_SAFE_GIT_PUBLIC_NAME",
            "ARVIS_SAFE_GIT_PUBLIC_EMAIL",
        ):
            self.assertNotIn(private_values[key], rendered)

    def test_safe_git_enabled_with_invalid_or_incomplete_policy_fails_closed(self) -> None:
        cases = (
            {"ARVIS_SAFE_GIT_CONTROL_ENABLED": "true"},
            {
                "ARVIS_SAFE_GIT_CONTROL_ENABLED": "true",
                "ARVIS_SAFE_GIT_REMOTE_NAME": "private-remote",
                "ARVIS_SAFE_GIT_EXPECTED_REMOTE_URL": "https://private.example.invalid/project.git",
                "ARVIS_SAFE_GIT_PUBLIC_NAME": "Private Local Name",
                "ARVIS_SAFE_GIT_PUBLIC_EMAIL": "private@example.invalid",
                "ARVIS_SAFE_GIT_PUSH_ENABLED": "sometimes",
            },
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env.example").write_text(
                "# ARVIS_SAFE_GIT_CONTROL_ENABLED=false\n",
                encoding="utf-8",
            )
            for env in cases:
                with self.subTest(keys=sorted(env)):
                    checks = doctor.check_local_config(root, env, doctor.DoctorOptions())
                    safe_git_checks = [check for check in checks if check.category == "Safe Git"]
                    self.assertEqual(len(safe_git_checks), 1)
                    self.assertEqual(safe_git_checks[0].status, "fail")
                    self.assertNotIn("private.example.invalid", repr(safe_git_checks))

    def test_safe_git_invalid_master_flag_warns_and_stays_disabled(self) -> None:
        checks = doctor._check_safe_git_config(
            {
                "ARVIS_SAFE_GIT_CONTROL_ENABLED": "yes",
                "ARVIS_SAFE_GIT_PUSH_ENABLED": "true",
                "ARVIS_SAFE_GIT_HISTORY_REWRITE_ENABLED": "true",
            }
        )

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].status, "warn")
        self.assertIn("remains disabled", checks[0].title)

    def test_system_metrics_storage_setting_is_known_and_path_stays_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            configured = root / "private-user-storage"
            configured.mkdir()
            (root / ".env.example").write_text(
                "# ARVIS_SYSTEM_METRICS_STORAGE_PATH=/path/to/filesystem\n",
                encoding="utf-8",
            )

            checks = doctor.check_local_config(
                root,
                {"ARVIS_SYSTEM_METRICS_STORAGE_PATH": str(configured)},
                doctor.DoctorOptions(verbose=True),
            )

        self.assertFalse(any(check.title == "Unknown local env keys are present" for check in checks))
        self.assertTrue(
            any(check.title == "System metrics storage target is available" for check in checks)
        )
        self.assertNotIn(str(configured), repr(checks))

    def test_doctor_environment_loads_ignored_env_local_with_expected_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                "ARVIS_SYSTEM_METRICS_STORAGE_PATH=/configured/from-env\n",
                encoding="utf-8",
            )
            (root / ".env.local").write_text(
                "ARVIS_SYSTEM_METRICS_STORAGE_PATH=/configured/from-env-local\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                env = doctor._load_doctor_environment(root)

        self.assertEqual(
            env["ARVIS_SYSTEM_METRICS_STORAGE_PATH"],
            "/configured/from-env-local",
        )

    def test_doctor_preserves_unknown_local_keys_without_reporting_host_only_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                "ARVIS_MODEL=arvis\nUNKNOWN_LOCAL_SETTING=present\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"PATH": os.environ.get("PATH", ""), "HOST_ONLY_UNKNOWN": "ignored"},
                clear=True,
            ):
                env = doctor._load_doctor_environment(root)
                checks = doctor.check_local_config(
                    root,
                    env,
                    doctor.DoctorOptions(verbose=True),
                )

        unknown = next(
            check for check in checks if check.title == "Unknown local env keys are present"
        )
        self.assertIn("UNKNOWN_LOCAL_SETTING", unknown.details)
        self.assertNotIn("HOST_ONLY_UNKNOWN", unknown.details)

    def test_doctor_uses_mcp_dotenv_export_quoting_and_interpolation_without_env_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                "\n".join(
                    (
                        "DOTENV_USER=public-contributor",
                        "export ARVIS_SAFE_GIT_CONTROL_ENABLED=\"true\"",
                        "ARVIS_SAFE_GIT_REMOTE_NAME='origin'",
                        "ARVIS_SAFE_GIT_EXPECTED_REMOTE_URL=\"https://github.com/example/arvis.git\"",
                        "ARVIS_SAFE_GIT_PUBLIC_NAME='Quoted Public Name'",
                        "ARVIS_SAFE_GIT_PUBLIC_EMAIL=${DOTENV_USER}@example.invalid",
                        "ARVIS_SAFE_GIT_PUSH_ENABLED=false",
                        "export ARVIS_SAFE_GIT_HISTORY_REWRITE_ENABLED='true'",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                before = dict(os.environ)
                env = doctor._load_doctor_environment(root)
                after = dict(os.environ)

        self.assertEqual(before, after)
        self.assertEqual(env["ARVIS_SAFE_GIT_CONTROL_ENABLED"], "true")
        self.assertEqual(env["ARVIS_SAFE_GIT_PUBLIC_NAME"], "Quoted Public Name")
        self.assertEqual(
            env["ARVIS_SAFE_GIT_PUBLIC_EMAIL"],
            "public-contributor@example.invalid",
        )
        checks = doctor._check_safe_git_config(env)
        self.assertTrue(any(check.status == "warn" and "rewrite" in check.title for check in checks))

    def test_doctor_dotenv_interpolation_does_not_read_unprovided_host_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                "ARVIS_MODEL=${PRIVATE_HOST_VALUE}\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"PRIVATE_HOST_VALUE": "must-not-leak", "UNCHANGED": "host"},
                clear=True,
            ):
                before = dict(os.environ)
                from mcp_access import read_local_mcp_environment

                effective = read_local_mcp_environment(root, environ={})
                after = dict(os.environ)

        self.assertEqual(before, after)
        self.assertEqual(effective["ARVIS_MODEL"], "")
        self.assertNotIn("PRIVATE_HOST_VALUE", effective)

    def test_fix_does_not_overwrite_existing_file_at_safe_dir_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "logs").write_text("not a dir", encoding="utf-8")

            checks = doctor.check_storage(root, doctor.DoctorOptions(fix=True))

            self.assertEqual((root / "logs").read_text(encoding="utf-8"), "not a dir")
            self.assertTrue(any(check.status == "fail" and "logs/" in check.title for check in checks))

    def test_json_output_has_no_ansi_escape_sequences(self) -> None:
        checks = [doctor.DoctorCheck("warn", "\x1b[31mConfig\x1b[0m", "Bad", details="\x1b[32msecret\x1b[0m")]

        report = doctor.render_json_report(checks)

        self.assertNotIn("\x1b", report)
        payload = json.loads(report)
        self.assertEqual(payload["checks"][0]["category"], "Config")

    def test_redaction_covers_auth_cookie_password_and_home_paths(self) -> None:
        text = (
            "Authorization: Bearer abcdefghijklmnop\n"
            "Cookie: sessionid=private-cookie\n"
            "PASSWORD=supersecretvalue\n"
            "/home/privateuser/project/file.txt"
        )

        redacted = doctor.sanitize_text(text)

        self.assertNotIn("abcdefghijklmnop", redacted)
        self.assertNotIn("private-cookie", redacted)
        self.assertNotIn("supersecretvalue", redacted)
        self.assertNotIn("/home/privateuser", redacted)

    def test_resolve_project_root_from_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            app_root = parent / "arvis_app"
            app_root.mkdir()
            for name in doctor.IMPORTANT_FILES:
                (app_root / name).write_text("", encoding="utf-8")

            with patch("doctor.Path.cwd", return_value=parent):
                resolved = doctor.resolve_project_root()

        self.assertEqual(resolved, app_root)

    def test_no_color_option_parses(self) -> None:
        options = doctor.parse_doctor_args(["--no-color"])

        self.assertTrue(options.no_color)

    def test_desktop_checks_use_safe_discovery(self) -> None:
        with patch("doctor.shutil.which", side_effect=lambda command: f"/usr/bin/{command}" if command in {"playerctl", "wpctl"} else None):
            checks = doctor.check_voice_audio(doctor.DoctorOptions())

        self.assertTrue(any(check.category == "Desktop" and check.title == "playerctl found" for check in checks))
        self.assertTrue(any(check.category == "Desktop" and check.title == "wpctl found" for check in checks))
        self.assertTrue(any(check.category == "Desktop" and "flatpak not found" in check.title for check in checks))

    def test_action_readiness_reports_whitelist_and_parseable_commands(self) -> None:
        with patch("doctor.importlib.import_module"), patch("doctor.shutil.which", return_value=None):
            checks = doctor.check_action_readiness({"SPOTIFY_COMMAND": "flatpak run com.spotify.Client"}, doctor.DoctorOptions())

        self.assertTrue(any(check.category == "Actions" and "app whitelist has" in check.title for check in checks))
        self.assertTrue(any(check.category == "Actions" and "spotify fallback/configured commands are parseable" in check.title for check in checks))

    def test_action_readiness_warns_on_unparseable_configured_command(self) -> None:
        with patch("doctor.importlib.import_module"), patch("doctor.shutil.which", return_value=None):
            checks = doctor.check_action_readiness({"SPOTIFY_COMMAND": '"unterminated'}, doctor.DoctorOptions())

        self.assertTrue(any(check.status == "warn" and "spotify command from .env is not parseable" in check.title for check in checks))

    def test_doctor_voice_disabled_info(self) -> None:
        with patch("doctor.importlib.util.find_spec", return_value=None), patch("doctor.shutil.which", return_value=None):
            checks = doctor.check_voice_audio(doctor.DoctorOptions(), {"ARVIS_VOICE_ENABLED": "false"})

        self.assertTrue(any(check.category == "Voice" and check.status == "info" and check.title == "disabled" for check in checks))

    def test_doctor_voice_unsafe_mic_device_fails(self) -> None:
        with patch("doctor.importlib.util.find_spec", return_value=None), patch("doctor.shutil.which", return_value=None):
            checks = doctor.check_voice_audio(
                doctor.DoctorOptions(),
                {"ARVIS_VOICE_ENABLED": "true", "ARVIS_MIC_DEVICE": "Monitor of Speakers"},
            )

        self.assertTrue(any(check.category == "Voice" and check.status == "fail" and "monitor/output" in check.title for check in checks))

    def test_doctor_voice_missing_dependency_warns(self) -> None:
        with patch("doctor.importlib.util.find_spec", return_value=None), patch("doctor.shutil.which", return_value=None):
            checks = doctor.check_voice_audio(doctor.DoctorOptions(), {"ARVIS_VOICE_ENABLED": "true"})

        self.assertTrue(any(check.category == "Voice" and check.status == "warn" and "faster-whisper" in check.title for check in checks))

    def test_doctor_voice_ducking_invalid_percent_warns(self) -> None:
        with patch("doctor.importlib.util.find_spec", return_value=None), patch("doctor.shutil.which", return_value="/usr/bin/wpctl"):
            checks = doctor.check_voice_audio(
                doctor.DoctorOptions(),
                {"ARVIS_VOICE_ENABLED": "true", "ARVIS_VOICE_DUCK_PERCENT": "999"},
            )

        self.assertTrue(any(check.category == "Voice" and check.status == "warn" and "duck percent is invalid" in check.title for check in checks))

    def test_doctor_voice_ducking_missing_wpctl_warns(self) -> None:
        with patch("doctor.importlib.util.find_spec", return_value=None), patch("doctor.shutil.which", return_value=None):
            checks = doctor.check_voice_audio(
                doctor.DoctorOptions(),
                {"ARVIS_VOICE_ENABLED": "true", "ARVIS_VOICE_DUCKING_ENABLED": "true"},
            )

        self.assertTrue(any(check.category == "Voice" and check.status == "warn" and "wpctl not found for audio ducking" in check.title for check in checks))

    def test_doctor_voice_ducking_parser_check(self) -> None:
        with patch("doctor.importlib.util.find_spec", return_value=None), patch("doctor.shutil.which", return_value="/usr/bin/wpctl"):
            checks = doctor.check_voice_audio(doctor.DoctorOptions(), {"ARVIS_VOICE_ENABLED": "true"})

        self.assertTrue(any(check.category == "Voice" and check.status == "ok" and "volume parser works" in check.title for check in checks))


if __name__ == "__main__":
    unittest.main()
