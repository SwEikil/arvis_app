from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import system_context
from system_context import CommandResult, SystemContextError, SystemInspector


class FakeRunner:
    def __init__(self, handler=None) -> None:
        self.handler = handler or (lambda argv: CommandResult(returncode=0))
        self.calls: list[list[str]] = []

    def __call__(self, argv, timeout, stdout_limit, stderr_limit) -> CommandResult:
        self.calls.append(list(argv))
        return self.handler(list(argv))


class SystemContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.os_release = self.root / "os-release"
        self.os_release.write_text(
            'NAME="Test Linux"\nID=testlinux\nVERSION_ID="44"\nVARIANT="Atomic Desktop"\n'
            'HOSTNAME=private-host\nHOME=/private/home\n',
            encoding="utf-8",
        )
        self.ostree_marker = self.root / "ostree-booted"
        self.ostree_marker.touch()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    @staticmethod
    def _which(*names: str):
        allowed = set(names)
        return lambda name: f"/usr/bin/{name}" if name in allowed else None

    def _inspector(self, runner: FakeRunner, *, which_names=("rpm", "rpm-ostree", "qtpaths6"), **kwargs):
        return SystemInspector(
            runner=runner,
            which=self._which(*which_names),
            environ=kwargs.pop("environ", {}),
            os_release_path=self.os_release,
            ostree_booted_path=kwargs.pop("ostree_booted_path", self.ostree_marker),
            **kwargs,
        )

    def test_system_info_parses_os_and_omits_private_identity_fields(self) -> None:
        def handler(argv):
            if Path(argv[0]).name == "qtpaths6":
                return CommandResult(0, "6.11.1\n")
            package = argv[-1]
            versions = {"plasma-workspace": "6.7.3-1.fc44", "kf6-kcoreaddons": "6.28.0-1.fc44"}
            if package in versions:
                return CommandResult(0, f"{package}\t{versions[package]}\tx86_64\tSummary\n")
            return CommandResult(1, stderr=f"package {package} is not installed")

        with patch("system_context.platform.system", return_value="Linux"), patch(
            "system_context.platform.machine", return_value="x86_64"
        ), patch("system_context.platform.release", return_value="6.12-test"):
            result = self._inspector(
                FakeRunner(handler), environ={"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"}
            ).system_info()

        self.assertEqual(result["os_name"], "Test Linux")
        self.assertEqual(result["distribution_id"], "testlinux")
        self.assertEqual(result["plasma_version"], "6.7.3")
        self.assertEqual(result["qt_version"], "6.11.1")
        self.assertTrue(result["atomic"])
        serialized = repr(result).casefold()
        for forbidden in ("private-host", "/private/home", "hostname':", "username':"):
            self.assertNotIn(forbidden, serialized)

    def test_system_info_tolerates_unavailable_optional_components(self) -> None:
        missing_marker = self.root / "not-ostree"
        result = self._inspector(
            FakeRunner(), which_names=(), ostree_booted_path=missing_marker
        ).system_info()

        self.assertIsNone(result["plasma_version"])
        self.assertIsNone(result["qt_version"])
        self.assertEqual(result["package_backends"], [])
        self.assertFalse(result["atomic"])

    def test_binary_exists_reports_system_path_without_executing_it(self) -> None:
        runner = FakeRunner()
        result = self._inspector(runner, which_names=("qtpaths6",)).binary_exists("qtpaths6")

        self.assertTrue(result["exists"])
        self.assertEqual(result["path"], "/usr/bin/qtpaths6")
        self.assertEqual(runner.calls, [])

    def test_binary_exists_hides_non_system_path_and_handles_missing(self) -> None:
        inspector = SystemInspector(which=lambda name: "/private/home/bin/tool" if name == "tool" else None)

        self.assertEqual(inspector.binary_exists("tool")["path_scope"], "non_system_hidden")
        self.assertIsNone(inspector.binary_exists("tool")["path"])
        self.assertFalse(inspector.binary_exists("missing")["exists"])

    def test_binary_exists_does_not_trust_local_or_opt_paths(self) -> None:
        for executable_path in ("/opt/private/tool", "/usr/local/bin/tool"):
            with self.subTest(executable_path=executable_path):
                inspector = SystemInspector(which=lambda name, value=executable_path: value)

                result = inspector.binary_exists("tool")

                self.assertTrue(result["exists"])
                self.assertEqual(result["path_scope"], "non_system_hidden")
                self.assertIsNone(result["path"])

    def test_binary_exists_rejects_command_composition(self) -> None:
        inspector = self._inspector(FakeRunner(), which_names=())
        for value in ("sh -c id", "bash;id", "../bash", "/bin/bash", "$(id)", "--help"):
            with self.subTest(value=value), self.assertRaises(SystemContextError) as caught:
                inspector.binary_exists(value)
            self.assertEqual(caught.exception.code, "invalid_input")

    def test_package_installed_parses_installed_record_and_fixed_argv(self) -> None:
        runner = FakeRunner(lambda argv: CommandResult(0, "bash\t5.3.9-3.fc44\tx86_64\tGNU shell\n"))
        result = self._inspector(runner).package_installed("bash")

        self.assertTrue(result["installed"])
        self.assertEqual(result["version"], "5.3.9-3.fc44")
        self.assertEqual(runner.calls[0], ["/usr/bin/rpm", "--query", "--queryformat", system_context._RPM_QUERY_FORMAT, "--", "bash"])

    def test_package_installed_reports_not_installed(self) -> None:
        runner = FakeRunner(lambda argv: CommandResult(1, stderr="package missing is not installed"))
        result = self._inspector(runner).package_installed("missing")

        self.assertFalse(result["installed"])
        self.assertIsNone(result["version"])

    def test_package_installed_backend_unavailable_timeout_and_malformed(self) -> None:
        cases = (
            (self._inspector(FakeRunner(), which_names=()), "backend_unavailable"),
            (self._inspector(FakeRunner(lambda argv: CommandResult(None, timed_out=True))), "timeout"),
            (self._inspector(FakeRunner(lambda argv: CommandResult(0, "malformed\n"))), "parser_failure"),
        )
        for inspector, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(SystemContextError) as caught:
                inspector.package_installed("bash")
            self.assertEqual(caught.exception.code, expected)

    def test_package_name_rejects_cli_flags_and_shell_syntax_before_runner(self) -> None:
        runner = FakeRunner()
        inspector = self._inspector(runner)
        for value in ("--install", "bash;id", "bash $(id)", "../bash", "bash/x"):
            with self.subTest(value=value), self.assertRaises(SystemContextError) as caught:
                inspector.package_installed(value)
            self.assertEqual(caught.exception.code, "invalid_input")
        self.assertEqual(runner.calls, [])

    def test_package_info_combines_installed_and_repository_data(self) -> None:
        def handler(argv):
            if Path(argv[0]).name == "rpm":
                return CommandResult(0, "bash\t5.3.9-3.fc44\tx86_64\tGNU shell\n")
            return CommandResult(0, "===== Name Matched =====\nbash : The GNU Bourne Again shell\n")

        result = self._inspector(FakeRunner(handler)).package_info("bash")

        self.assertTrue(result["installed"])
        self.assertTrue(result["available"])
        self.assertEqual(result["repository_backend"], "rpm-ostree-cache")
        self.assertIsNone(result["available_version"])

    def test_package_info_returns_partial_data_when_repository_backend_is_unavailable(self) -> None:
        runner = FakeRunner(lambda argv: CommandResult(0, "bash\t5.3.9\tx86_64\tGNU shell\n"))
        missing_marker = self.root / "not-ostree"
        result = self._inspector(runner, ostree_booted_path=missing_marker).package_info("bash")

        self.assertTrue(result["installed"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["repository_error"]["code"], "backend_unavailable")

    def test_package_info_unavailable_package_has_controlled_error(self) -> None:
        def handler(argv):
            if Path(argv[0]).name == "rpm":
                return CommandResult(1, stderr="package absent is not installed")
            return CommandResult(0, "No matches found.\n")

        with self.assertRaises(SystemContextError) as caught:
            self._inspector(FakeRunner(handler)).package_info("absent")

        self.assertEqual(caught.exception.code, "package_not_found")

    def test_package_search_parses_deduplicates_and_limits_results(self) -> None:
        output = """===== Name Matched =====
alpha : First package
beta : Second package
alpha : First package
gamma : Third package
"""
        runner = FakeRunner(lambda argv: CommandResult(0, output))
        result = self._inspector(runner).package_search("package", limit=2)

        self.assertEqual([item["name"] for item in result["results"]], ["alpha", "beta"])
        self.assertEqual(result["result_count"], 2)
        self.assertTrue(result["truncated"])
        self.assertFalse(result["network_used"])
        self.assertEqual(runner.calls[0], ["/usr/bin/rpm-ostree", "search", "--cache-only", "package"])

    def test_package_search_validates_empty_oversized_and_injection_queries(self) -> None:
        runner = FakeRunner()
        inspector = self._inspector(runner)
        values = (
            "",
            "x" * (system_context.MAX_PACKAGE_QUERY_CHARS + 1),
            "--install bash",
            "bash; id",
            "bash $(id)",
            "../bash",
        )
        for value in values:
            with self.subTest(value=value[:20]), self.assertRaises(SystemContextError) as caught:
                inspector.package_search(value)
            self.assertEqual(caught.exception.code, "invalid_input")
        self.assertEqual(runner.calls, [])

    def test_package_search_timeout_and_output_truncation(self) -> None:
        with self.assertRaises(SystemContextError) as caught:
            self._inspector(FakeRunner(lambda argv: CommandResult(None, timed_out=True))).package_search("bash")
        self.assertEqual(caught.exception.code, "timeout")

        truncated = self._inspector(
            FakeRunner(lambda argv: CommandResult(-9, "bash : GNU shell\n", truncated=True))
        ).package_search("bash")
        self.assertTrue(truncated["truncated"])
        self.assertEqual(truncated["results"][0]["name"], "bash")

    def test_package_search_reports_unavailable_cached_metadata(self) -> None:
        result = CommandResult(1, stderr="Cache-only enabled but no cache for repository")
        with self.assertRaises(SystemContextError) as caught:
            self._inspector(FakeRunner(lambda argv: result)).package_search("bash")
        self.assertEqual(caught.exception.code, "repository_metadata_unavailable")

    def test_all_rpm_ostree_searches_are_cache_only(self) -> None:
        def handler(argv):
            if Path(argv[0]).name == "rpm":
                return CommandResult(0, "bash\t5.3.9\tx86_64\tGNU shell\n")
            return CommandResult(0, "bash : GNU shell\n")

        runner = FakeRunner(handler)
        inspector = self._inspector(runner)
        inspector.package_search("bash")
        inspector.package_info("bash")

        repository_calls = [
            argv for argv in runner.calls if Path(argv[0]).name == "rpm-ostree"
        ]
        self.assertEqual(len(repository_calls), 2)
        self.assertTrue(
            all(argv[1:3] == ["search", "--cache-only"] for argv in repository_calls)
        )

    def test_plasma_info_parses_versions_and_wayland_session(self) -> None:
        def handler(argv):
            if Path(argv[0]).name == "qtpaths6":
                return CommandResult(0, "6.11.1\n")
            versions = {"plasma-workspace": "6.7.3-1.fc44", "kf6-kcoreaddons": "6.28.0-1.fc44"}
            name = argv[-1]
            return CommandResult(0, f"{name}\t{versions[name]}\tx86_64\tSummary\n")

        result = self._inspector(
            FakeRunner(handler), environ={"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"}
        ).plasma_info()

        self.assertEqual(result["plasma_version"], "6.7.3")
        self.assertEqual(result["kde_frameworks_version"], "6.28.0")
        self.assertEqual(result["qt_version"], "6.11.1")
        self.assertEqual(result["display_protocol"], "wayland")

    def test_plasma_info_handles_absence_x11_and_malformed_versions(self) -> None:
        def handler(argv):
            if Path(argv[0]).name == "qtpaths6":
                return CommandResult(0, "not a version\n")
            if argv[-1] == "plasma-workspace":
                return CommandResult(1, stderr="package plasma-workspace is not installed")
            return CommandResult(0, "kf6-kcoreaddons\tmalformed\tx86_64\tSummary\n")

        result = self._inspector(FakeRunner(handler), environ={"XDG_SESSION_TYPE": "x11"}).plasma_info()

        self.assertIsNone(result["plasma_version"])
        self.assertIsNone(result["kde_frameworks_version"])
        self.assertIsNone(result["qt_version"])
        self.assertEqual(result["display_protocol"], "x11")

    def test_qml_module_available_reads_qmldir_and_provider(self) -> None:
        qml_root = self.root / "qml"
        module_dir = qml_root / "org" / "kde" / "kirigami"
        module_dir.mkdir(parents=True)
        qmldir = module_dir / "qmldir"
        qmldir.write_text("module org.kde.kirigami\nAboutPage 2.6 AboutPage.qml\n", encoding="utf-8")

        def handler(argv):
            if Path(argv[0]).name == "qtpaths6":
                return CommandResult(0, "6.11.1\n")
            if Path(argv[0]).name == "rpm" and "--file" in argv:
                return CommandResult(0, "kf6-kirigami\t6.28.0-1.fc44\tx86_64\tKirigami\n")
            return CommandResult(1, stderr="not installed")

        result = self._inspector(FakeRunner(handler), qml_roots=(qml_root,)).qml_module_available(
            "org.kde.kirigami"
        )

        self.assertTrue(result["available"])
        self.assertEqual(result["qt_major"], 6)
        self.assertEqual(result["declared_type_versions"], ["2.6"])
        self.assertEqual(result["provider"]["name"], "kf6-kirigami")
        self.assertIsNone(result["location"])

    def test_qml_module_available_reports_missing_module(self) -> None:
        qml_root = self.root / "qml"
        qml_root.mkdir()
        result = self._inspector(FakeRunner(), qml_roots=(qml_root,)).qml_module_available(
            "org.kde.fake"
        )

        self.assertFalse(result["available"])
        self.assertIsNone(result["provider"])
        self.assertFalse(
            self._inspector(FakeRunner(), qml_roots=(qml_root,))
            .qml_module_available("QtQuick")["available"]
        )

    def test_qml_module_does_not_follow_qmldir_symlink_outside_root(self) -> None:
        qml_root = self.root / "qml"
        module_dir = qml_root / "org" / "kde" / "private"
        module_dir.mkdir(parents=True)
        outside = self.root / "outside-qmldir"
        outside.write_text(
            "module org.kde.private\nSecretType 1.0 Secret.qml\n",
            encoding="utf-8",
        )
        (module_dir / "qmldir").symlink_to(outside)

        result = self._inspector(
            FakeRunner(), qml_roots=(qml_root,)
        ).qml_module_available("org.kde.private")

        self.assertFalse(result["available"])
        self.assertEqual(result["declared_type_versions"], [])

    def test_qml_module_rejects_paths_traversal_and_invalid_uri(self) -> None:
        runner = FakeRunner()
        inspector = self._inspector(runner, qml_roots=())
        for value in ("../org.kde", "org/kde/kirigami", "/usr/lib/qml", "org..kde", "--help"):
            with self.subTest(value=value), self.assertRaises(SystemContextError) as caught:
                inspector.qml_module_available(value)
            self.assertEqual(caught.exception.code, "invalid_input")
        self.assertEqual(runner.calls, [])

    def test_qml_module_reports_inspection_utility_unavailable(self) -> None:
        inspector = self._inspector(FakeRunner(), which_names=(), qml_roots=())
        with self.assertRaises(SystemContextError) as caught:
            inspector.qml_module_available("org.kde.kirigami")
        self.assertEqual(caught.exception.code, "executable_unavailable")

    def test_qml_module_malformed_metadata_is_controlled(self) -> None:
        qml_root = self.root / "qml"
        module_dir = qml_root / "org" / "kde" / "broken"
        module_dir.mkdir(parents=True)
        (module_dir / "qmldir").write_text("module org.kde.other\n", encoding="utf-8")

        with self.assertRaises(SystemContextError) as caught:
            self._inspector(FakeRunner(), qml_roots=(qml_root,)).qml_module_available("org.kde.broken")
        self.assertEqual(caught.exception.code, "parser_failure")

    def test_system_service_never_constructs_mutating_package_commands(self) -> None:
        def handler(argv):
            if Path(argv[0]).name == "rpm":
                return CommandResult(1, stderr=f"package {argv[-1]} is not installed")
            return CommandResult(0, "No matches found.\n")

        runner = FakeRunner(handler)
        inspector = self._inspector(runner)
        inspector.package_installed("safe-name")
        inspector.package_search("safe query")
        with self.assertRaises(SystemContextError):
            inspector.package_info("missing")

        flattened = " ".join(part for argv in runner.calls for part in argv)
        for forbidden in (" install ", " remove ", " update ", " upgrade ", " refresh ", " sudo "):
            self.assertNotIn(forbidden, f" {flattened} ")
        self.assertTrue(all(Path(argv[0]).name in {"rpm", "rpm-ostree"} for argv in runner.calls))

    def test_fixed_command_disables_shell_and_stdin(self) -> None:
        with patch("system_context.subprocess.Popen", side_effect=OSError) as popen:
            result = system_context.run_fixed_command(["/usr/bin/rpm", "--query"], timeout=3)

        self.assertTrue(result.executable_unavailable)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["/usr/bin/rpm", "--query"])
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["stdin"], system_context.subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], system_context.subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], system_context.subprocess.PIPE)


if __name__ == "__main__":
    unittest.main()
