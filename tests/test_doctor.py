from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
