from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec("mcp"), "mcp SDK is not installed")
class SafeCommandMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        self.helper = self.root / "safe-command-test-helper"
        self.helper.write_text(
            f"#!{sys.executable}\n"
            "import os\n"
            "import sys\n"
            "print('|'.join(sys.argv[1:]))\n"
            "print(os.getcwd())\n",
            encoding="utf-8",
        )
        self.helper.chmod(0o700)
        self.config_path = self.root / "safe-commands.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _recipe(
        self,
        *,
        access: str = "read_only",
        cwd_mode: str = "project_root",
    ) -> dict[str, object]:
        return {
            "description": "Generic integration test recipe",
            "executable": str(self.helper),
            "argv": ["{value}"],
            "parameters": {
                "value": {"choices": ["alpha"], "max_length": 5},
            },
            "access": access,
            "timeout_seconds": 5,
            "output_limits": {"stdout_chars": 1_000, "stderr_chars": 500},
            "cwd_mode": cwd_mode,
        }

    def _write_config(self, recipe: dict[str, object] | None = None) -> Path:
        self.config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "commands": {"sample": recipe or self._recipe()},
                }
            ),
            encoding="utf-8",
        )
        return self.config_path

    def _environment(
        self,
        *,
        profile: str = "chatgpt",
        enabled: bool = True,
        host_control_enabled: bool = False,
        config_path: Path | None = None,
        allowed_root: Path | None = None,
        writable_roots: str | None = None,
    ) -> dict[str, str]:
        root = allowed_root or self.allowed
        environment = {
            "ARVIS_MCP_PROFILE": profile,
            "ARVIS_MCP_PROJECT_ROOT": str(root),
            "ARVIS_MCP_ALLOWED_ROOTS": str(root),
            "ARVIS_MCP_WRITABLE_ROOTS": writable_roots or str(root),
            "ARVIS_CODEX_AGENT_CONTROL_ENABLED": "false",
            "ARVIS_SAFE_GIT_CONTROL_ENABLED": "false",
            "ARVIS_SAFE_COMMAND_CONTROL_ENABLED": "true" if enabled else "false",
            "ARVIS_SAFE_COMMAND_CONFIG": str(config_path or self.config_path),
            "ARVIS_SAFE_COMMAND_HOST_CONTROL_ENABLED": (
                "true" if host_control_enabled else "false"
            ),
            "PATH": os.environ.get("PATH", ""),
        }
        return environment

    def _load_server(self, env: dict[str, str]):
        with patch.dict(os.environ, env, clear=True):
            import arvis_mcp_server

            return importlib.reload(arvis_mcp_server)

    def test_tool_is_absent_when_control_is_disabled_even_with_config(self) -> None:
        config_path = self._write_config()
        for profile, expected_count in (("chatgpt", 24), ("codex", 25)):
            with self.subTest(profile=profile):
                module = self._load_server(
                    self._environment(
                        profile=profile,
                        enabled=False,
                        config_path=config_path,
                    )
                )
                tools = {tool.name for tool in asyncio.run(module.mcp.list_tools())}

                self.assertNotIn("safe_command_run", tools)
                self.assertEqual(len(tools), expected_count)

    def test_explicit_enable_publishes_one_narrow_control_tool(self) -> None:
        module = self._load_server(
            self._environment(config_path=self._write_config())
        )
        tools = {tool.name: tool for tool in asyncio.run(module.mcp.list_tools())}

        self.assertEqual(
            {name for name in tools if name.startswith("safe_command_")},
            {"safe_command_run"},
        )
        tool = tools["safe_command_run"]
        self.assertEqual(
            set(tool.inputSchema["properties"]),
            {"recipe_name", "params", "project_root"},
        )
        self.assertEqual(set(tool.inputSchema["required"]), {"recipe_name", "params"})
        self.assertEqual(tool.inputSchema["properties"]["params"]["type"], "object")
        self.assertFalse(tool.annotations.readOnlyHint)
        self.assertTrue(tool.annotations.destructiveHint)
        self.assertFalse(tool.annotations.idempotentHint)
        self.assertTrue(tool.annotations.openWorldHint)

        forbidden = {
            "config_path",
            "executable",
            "argv",
            "cwd",
            "access",
            "timeout",
            "output_limits",
            "writable_authorization",
            "host_control",
            "env",
            "shell",
            "command_text",
        }
        self.assertTrue(forbidden.isdisjoint(tool.inputSchema["properties"]))

    def test_missing_or_invalid_config_fails_closed_without_server_crash(self) -> None:
        missing = self.root / "private-missing-policy.json"
        invalid = self.root / "private-invalid-policy.json"
        invalid.write_text("{invalid", encoding="utf-8")

        for config_path in (missing, invalid):
            with self.subTest(config_path=config_path.name):
                module = self._load_server(self._environment(config_path=config_path))
                tools = {tool.name for tool in asyncio.run(module.mcp.list_tools())}

                self.assertNotIn("safe_command_run", tools)
                self.assertFalse(module.SAFE_COMMAND_CONTROLLER.available)
                self.assertNotIn(str(config_path), repr(module.SAFE_COMMAND_CONTROLLER))

    def test_valid_policy_with_no_recipes_registers_no_tool(self) -> None:
        self.config_path.write_text(
            json.dumps({"version": 1, "commands": {}}),
            encoding="utf-8",
        )

        module = self._load_server(self._environment(config_path=self.config_path))
        tools = {tool.name for tool in asyncio.run(module.mcp.list_tools())}

        self.assertNotIn("safe_command_run", tools)
        self.assertFalse(module.SAFE_COMMAND_CONTROLLER.available)

    def test_read_only_recipe_executes_only_through_an_allowed_root(self) -> None:
        module = self._load_server(
            self._environment(config_path=self._write_config())
        )

        result = module.safe_command_run("sample", {"value": "alpha"}, str(self.allowed))

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["recipe_name"], "sample")
        self.assertEqual(result["access"], "read_only")
        self.assertIn("alpha", result["stdout"])
        self.assertIn("<PROJECT_ROOT>", result["stdout"])
        self.assertNotIn(str(self.config_path), repr(result))

        denied = self.root / "denied"
        denied.mkdir()
        denied_result = module.safe_command_run("sample", {"value": "alpha"}, str(denied))
        self.assertEqual(denied_result["ok"], False)

    def test_supplied_root_is_validated_even_when_recipe_needs_no_project_cwd(self) -> None:
        config_path = self._write_config(self._recipe(cwd_mode="none"))
        module = self._load_server(self._environment(config_path=config_path))
        denied = self.root / "denied"
        denied.mkdir()

        result = module.safe_command_run("sample", {"value": "alpha"}, str(denied))

        self.assertEqual(result["ok"], False)

    def test_workspace_write_recipe_requires_writable_root(self) -> None:
        writable = self.allowed / "writable"
        read_only = self.allowed / "read-only"
        writable.mkdir()
        read_only.mkdir()
        config_path = self._write_config(self._recipe(access="workspace_write"))
        module = self._load_server(
            self._environment(
                config_path=config_path,
                allowed_root=self.allowed,
                writable_roots=str(writable),
            )
        )

        denied = module.safe_command_run("sample", {"value": "alpha"}, str(read_only))
        allowed = module.safe_command_run("sample", {"value": "alpha"}, str(writable))

        self.assertEqual(denied["ok"], False)
        self.assertEqual(allowed["ok"], True)
        self.assertEqual(allowed["access"], "workspace_write")

    def test_host_control_requires_second_local_opt_in(self) -> None:
        config_path = self._write_config(
            self._recipe(access="host_control", cwd_mode="none")
        )
        disabled_module = self._load_server(
            self._environment(config_path=config_path, host_control_enabled=False)
        )
        denied = disabled_module.safe_command_run("sample", {"value": "alpha"}, None)

        enabled_module = self._load_server(
            self._environment(config_path=config_path, host_control_enabled=True)
        )
        allowed = enabled_module.safe_command_run("sample", {"value": "alpha"}, None)

        self.assertEqual(denied["ok"], False)
        self.assertIn("disabled by caller policy", denied["error"])
        self.assertEqual(allowed["ok"], True)
        self.assertEqual(allowed["access"], "host_control")

    def test_chatgpt_and_codex_obey_the_same_local_recipe_policy(self) -> None:
        config_path = self._write_config()
        results: dict[str, dict[str, object]] = {}
        for profile in ("chatgpt", "codex"):
            module = self._load_server(
                self._environment(profile=profile, config_path=config_path)
            )
            results[profile] = module.safe_command_run(
                "sample",
                {"value": "alpha"},
                str(self.allowed),
            )

        for result in results.values():
            self.assertEqual(result["ok"], True)
            self.assertEqual(result["recipe_name"], "sample")
            self.assertEqual(result["access"], "read_only")


if __name__ == "__main__":
    unittest.main()
