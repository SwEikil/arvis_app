from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from safe_commands import (
    AccessMode,
    MAX_STDERR_CHARS,
    MAX_STDOUT_CHARS,
    MAX_TIMEOUT_SECONDS,
    SafeCommandConfigError,
    SafeCommandExecutionError,
    execute_safe_command,
    load_safe_commands,
)


class SafeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.fixed = self.root / "fixed-private-path"
        self.fixed.mkdir()
        self.executable = self.root / "safe-command-test"
        self.executable.write_text(
            f"#!{sys.executable}\n"
            "import os\n"
            "import sys\n"
            "import time\n"
            "mode = sys.argv[1] if len(sys.argv) > 1 else 'args'\n"
            "if mode == 'args':\n"
            "    print('|'.join(sys.argv[2:]))\n"
            "elif mode == 'cwd':\n"
            "    print(os.getcwd())\n"
            "elif mode == 'sleep':\n"
            "    print('started', flush=True)\n"
            "    time.sleep(float(sys.argv[2]))\n"
            "elif mode == 'output':\n"
            "    print('X' * int(sys.argv[2]))\n"
            "    print('password=super-secret-value', file=sys.stderr)\n"
            "    print('Y' * int(sys.argv[2]), file=sys.stderr)\n",
            encoding="utf-8",
        )
        self.executable.chmod(0o700)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _recipe(self, **overrides: object) -> dict[str, object]:
        recipe: dict[str, object] = {
            "description": "Echo a constrained value",
            "executable": str(self.executable),
            "argv": ["args", "{value}"],
            "parameters": {
                "value": {"choices": ["alpha", "beta"], "max_length": 20},
            },
            "access": "read_only",
            "timeout_seconds": 5,
            "output_limits": {"stdout_chars": 1_000, "stderr_chars": 500},
            "cwd_mode": "none",
        }
        recipe.update(overrides)
        return recipe

    def _load(self, recipe: dict[str, object] | None = None):
        path = self.root / "safe-commands.json"
        path.write_text(
            json.dumps({"version": 1, "commands": {"sample": recipe or self._recipe()}}),
            encoding="utf-8",
        )
        return load_safe_commands(path)["sample"]

    def test_valid_read_only_run_and_parameter_choice(self) -> None:
        recipe = self._load()

        result = execute_safe_command(recipe, {"value": "alpha"})

        self.assertEqual(result.recipe_name, "sample")
        self.assertIs(result.access, AccessMode.READ_ONLY)
        self.assertEqual(result.return_code, 0)
        self.assertEqual(result.stdout, "alpha\n")
        self.assertFalse(result.timed_out)
        self.assertFalse(result.truncated)
        with self.assertRaises(SafeCommandExecutionError):
            execute_safe_command(recipe, {"value": "gamma"})

    def test_regex_uses_fullmatch(self) -> None:
        recipe = self._load(
            self._recipe(
                parameters={"value": {"regex": "[a-z]{2}[0-9]{2}", "max_length": 4}},
            )
        )
        self.assertEqual(execute_safe_command(recipe, {"value": "ab12"}).stdout, "ab12\n")
        for invalid in ("xab12", "ab12x", "AB12"):
            with self.subTest(invalid=invalid), self.assertRaises(SafeCommandExecutionError):
                execute_safe_command(recipe, {"value": invalid})

    def test_missing_and_extra_parameters_are_rejected(self) -> None:
        recipe = self._load()
        with self.assertRaises(SafeCommandExecutionError):
            execute_safe_command(recipe, {})
        with self.assertRaises(SafeCommandExecutionError):
            execute_safe_command(recipe, {"value": "alpha", "extra": "x"})

    def test_partial_and_duplicate_placeholders_are_rejected(self) -> None:
        for argv in (["args", "prefix-{value}"], ["args", "{value}", "{value}"]):
            with self.subTest(argv=argv), self.assertRaises(SafeCommandConfigError):
                self._load(self._recipe(argv=argv))

    def test_unused_declaration_and_unknown_recipe_shape_are_rejected(self) -> None:
        with self.assertRaises(SafeCommandConfigError):
            self._load(
                self._recipe(
                    parameters={
                        "value": {"choices": ["alpha"], "max_length": 20},
                        "unused": {"choices": ["x"], "max_length": 1},
                    }
                )
            )
        with self.assertRaises(SafeCommandConfigError):
            self._load(self._recipe(unreviewed_option=True))

    def test_executable_placeholder_is_rejected(self) -> None:
        with self.assertRaises(SafeCommandConfigError):
            self._load(self._recipe(executable="{value}"))

    def test_relative_and_non_executable_executables_are_rejected(self) -> None:
        not_executable = self.root / "not-executable"
        not_executable.write_text("data", encoding="utf-8")
        for executable in ("relative-tool", str(not_executable)):
            with self.subTest(executable=executable), self.assertRaises(SafeCommandConfigError):
                self._load(self._recipe(executable=executable))

    def test_shell_and_privilege_trampolines_are_rejected(self) -> None:
        for candidate in (Path("/bin/sh"), Path("/usr/bin/sudo")):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                with self.subTest(candidate=candidate), self.assertRaises(SafeCommandConfigError):
                    self._load(self._recipe(executable=str(candidate)))
        shell = Path("/bin/sh")
        if shell.is_file():
            disguised_shell = self.root / "apparently-safe-tool"
            disguised_shell.symlink_to(shell)
            with self.assertRaises(SafeCommandConfigError):
                self._load(self._recipe(executable=str(disguised_shell)))

    def test_malformed_config_duplicate_key_and_invalid_regex_fail_closed(self) -> None:
        missing = self.root / "missing.json"
        with self.assertRaises(SafeCommandConfigError):
            load_safe_commands(missing)

        malformed = self.root / "malformed.json"
        malformed.write_text("{not json", encoding="utf-8")
        with self.assertRaises(SafeCommandConfigError):
            load_safe_commands(malformed)

        duplicate = self.root / "duplicate.json"
        duplicate.write_text('{"version":1,"version":1,"commands":{}}', encoding="utf-8")
        with self.assertRaises(SafeCommandConfigError):
            load_safe_commands(duplicate)

        with self.assertRaises(SafeCommandConfigError):
            self._load(
                self._recipe(parameters={"value": {"regex": "([", "max_length": 20}})
            )
        with self.assertRaises(SafeCommandConfigError):
            self._load(
                self._recipe(parameters={"value": {"regex": "(a+)+", "max_length": 20}})
            )

    def test_parameter_requires_real_constraint_and_rejects_control_characters(self) -> None:
        with self.assertRaises(SafeCommandConfigError):
            self._load(self._recipe(parameters={"value": {"max_length": 20}}))
        recipe = self._load(
            self._recipe(parameters={"value": {"regex": ".+", "max_length": 20}})
        )
        with self.assertRaises(SafeCommandExecutionError):
            execute_safe_command(recipe, {"value": "safe\nunsafe"})
        with self.assertRaises(SafeCommandExecutionError):
            execute_safe_command(recipe, {"value": "safe\u0085unsafe"})

    def test_timeout_returns_controlled_bounded_result(self) -> None:
        recipe = self._load(
            self._recipe(
                argv=["sleep", "{value}"],
                parameters={"value": {"choices": ["2"], "max_length": 1}},
                timeout_seconds=1,
            )
        )

        result = execute_safe_command(recipe, {"value": "2"})

        self.assertEqual(result.return_code, 124)
        self.assertTrue(result.timed_out)
        self.assertIn("started", result.stdout)
        self.assertIn("timed out", result.stderr)
        self.assertLess(result.duration_seconds, 2.0)

    def test_configured_runtime_and_output_limits_are_globally_capped(self) -> None:
        recipe = self._load(
            self._recipe(
                timeout_seconds=MAX_TIMEOUT_SECONDS * 10,
                output_limits={
                    "stdout_chars": MAX_STDOUT_CHARS * 10,
                    "stderr_chars": MAX_STDERR_CHARS * 10,
                },
            )
        )
        self.assertEqual(recipe.timeout_seconds, MAX_TIMEOUT_SECONDS)
        self.assertEqual(recipe.stdout_limit, MAX_STDOUT_CHARS)
        self.assertEqual(recipe.stderr_limit, MAX_STDERR_CHARS)

    def test_output_is_truncated_and_secrets_are_redacted(self) -> None:
        recipe = self._load(
            self._recipe(
                argv=["output", "{value}"],
                parameters={"value": {"choices": ["100"], "max_length": 3}},
                output_limits={"stdout_chars": 20, "stderr_chars": 40},
            )
        )

        result = execute_safe_command(recipe, {"value": "100"})

        self.assertTrue(result.truncated)
        self.assertEqual(len(result.stdout), 20)
        self.assertLessEqual(len(result.stderr), 40)
        self.assertNotIn("super-secret-value", result.stderr)
        self.assertIn("[REDACTED]", result.stderr)

    def test_project_root_cwd_mode_and_required_root(self) -> None:
        recipe = self._load(
            self._recipe(argv=["cwd"], parameters={}, cwd_mode="project_root")
        )
        with self.assertRaises(SafeCommandExecutionError):
            execute_safe_command(recipe, {})

        result = execute_safe_command(recipe, {}, project_root=self.project)

        self.assertEqual(result.stdout, "<PROJECT_ROOT>\n")
        self.assertNotIn(str(self.project), result.stdout)

    def test_workspace_write_requires_caller_authorization(self) -> None:
        recipe = self._load(
            self._recipe(argv=["cwd"], parameters={}, access="workspace_write", cwd_mode="project_root")
        )
        with self.assertRaises(SafeCommandExecutionError):
            execute_safe_command(recipe, {}, project_root=self.project)

        result = execute_safe_command(
            recipe,
            {},
            project_root=self.project,
            writable_project_root=True,
        )
        self.assertEqual(result.return_code, 0)
        self.assertIs(result.access, AccessMode.WORKSPACE_WRITE)

    def test_host_control_requires_recipe_and_explicit_policy_opt_in(self) -> None:
        recipe = self._load(self._recipe(access="host_control"))
        with self.assertRaises(SafeCommandExecutionError):
            execute_safe_command(recipe, {"value": "alpha"})

        result = execute_safe_command(recipe, {"value": "alpha"}, host_control_enabled=True)
        self.assertEqual(result.return_code, 0)
        self.assertIs(result.access, AccessMode.HOST_CONTROL)

    def test_fixed_cwd_is_checked_at_execution_and_not_leaked(self) -> None:
        recipe = self._load(
            self._recipe(
                argv=["cwd"],
                parameters={},
                cwd_mode="fixed",
                fixed_cwd=str(self.fixed),
            )
        )
        result = execute_safe_command(recipe, {})
        self.assertEqual(result.stdout, "<FIXED_CWD>\n")
        self.assertNotIn(str(self.fixed), result.stdout)

        self.fixed.rmdir()
        with self.assertRaises(SafeCommandExecutionError):
            execute_safe_command(recipe, {})

    def test_shell_metacharacters_remain_one_argv_token(self) -> None:
        dangerous = "literal;touch injected && echo nope $(id)"
        recipe = self._load(
            self._recipe(
                parameters={"value": {"choices": [dangerous], "max_length": 80}},
                cwd_mode="fixed",
                fixed_cwd=str(self.root),
            )
        )

        result = execute_safe_command(recipe, {"value": dangerous})

        self.assertEqual(result.stdout, dangerous + "\n")
        self.assertFalse((self.root / "injected").exists())


if __name__ == "__main__":
    unittest.main()
