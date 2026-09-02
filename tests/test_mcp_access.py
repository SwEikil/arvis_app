from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_access import (
    PROFILE_CHATGPT,
    PROFILE_CODEX,
    load_mcp_access_config,
    read_local_mcp_environment,
)


class McpAccessConfigTests(unittest.TestCase):
    def test_public_config_import_needs_no_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = load_mcp_access_config(environ={}, cwd=root)

        self.assertEqual(config.profile, PROFILE_CODEX)
        self.assertEqual(config.allowed_roots, (root.resolve(),))
        self.assertIsNone(config.configuration_error)

    def test_codex_profile_keeps_memory_write_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = load_mcp_access_config(
                environ={
                    "ARVIS_MCP_PROFILE": "codex",
                    "ARVIS_MCP_ALLOWED_ROOTS": str(root),
                },
                cwd=root,
            )

        self.assertTrue(config.memory_writes_allowed)
        self.assertEqual(config.writable_roots, (root.resolve(),))

    def test_explicit_empty_or_malformed_writable_roots_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            missing = root / "missing"
            not_directory = root / "file"
            not_directory.write_text("not a directory\n", encoding="utf-8")
            for value in (
                "",
                "   ",
                os.pathsep,
                f"{root}{os.pathsep}",
                "bad\0path",
                1,
                str(missing),
                str(not_directory),
                f"{root}{os.pathsep}{missing}",
            ):
                with self.subTest(value=repr(value)):
                    config = load_mcp_access_config(
                        environ={
                            "ARVIS_MCP_PROFILE": "codex",
                            "ARVIS_MCP_ALLOWED_ROOTS": str(root),
                            "ARVIS_MCP_WRITABLE_ROOTS": value,
                        },
                        cwd=root,
                    )

                    self.assertEqual(config.writable_roots, ())
                    self.assertIsNotNone(config.configuration_error)
                    self.assertFalse(config.memory_writes_allowed)

    def test_absent_writable_roots_keeps_codex_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = load_mcp_access_config(
                environ={
                    "ARVIS_MCP_PROFILE": "codex",
                    "ARVIS_MCP_ALLOWED_ROOTS": str(root),
                },
                cwd=root,
            )

        self.assertEqual(config.writable_roots, (root.resolve(),))
        self.assertIsNone(config.configuration_error)

    def test_bare_dotenv_writable_roots_is_explicit_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / ".env").write_text(
                "ARVIS_MCP_PROFILE=codex\n"
                f"ARVIS_MCP_ALLOWED_ROOTS={root}\n"
                "ARVIS_MCP_WRITABLE_ROOTS\n",
                encoding="utf-8",
            )

            environment = read_local_mcp_environment(root, environ={})
            config = load_mcp_access_config(environ=environment, cwd=root)

        self.assertIn("ARVIS_MCP_WRITABLE_ROOTS", environment)
        self.assertEqual(environment["ARVIS_MCP_WRITABLE_ROOTS"], "")
        self.assertEqual(config.writable_roots, ())
        self.assertIsNotNone(config.configuration_error)
        self.assertFalse(config.memory_writes_allowed)

    def test_chatgpt_profile_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = load_mcp_access_config(
                environ={
                    "ARVIS_MCP_PROFILE": "chatgpt",
                    "ARVIS_MCP_ALLOWED_ROOTS": str(root),
                },
                cwd=root,
            )

        self.assertEqual(config.profile, PROFILE_CHATGPT)
        self.assertFalse(config.memory_writes_allowed)
        self.assertEqual(config.writable_roots, ())

    def test_chatgpt_write_roots_are_explicit_and_must_stay_inside_read_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            readable = base / "readable"
            writable = readable / "writable"
            outside = base / "outside"
            writable.mkdir(parents=True)
            outside.mkdir()
            allowed = load_mcp_access_config(
                environ={
                    "ARVIS_MCP_PROFILE": "chatgpt",
                    "ARVIS_MCP_ALLOWED_ROOTS": str(readable),
                    "ARVIS_MCP_WRITABLE_ROOTS": str(writable),
                },
                cwd=base,
            )
            denied = load_mcp_access_config(
                environ={
                    "ARVIS_MCP_PROFILE": "chatgpt",
                    "ARVIS_MCP_ALLOWED_ROOTS": str(readable),
                    "ARVIS_MCP_WRITABLE_ROOTS": str(outside),
                },
                cwd=base,
            )

        self.assertEqual(allowed.writable_roots, (writable.resolve(),))
        self.assertIsNone(allowed.configuration_error)
        self.assertIsNotNone(denied.configuration_error)

    def test_multiple_allowed_roots_and_default_are_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            first = base / "first"
            second = base / "second"
            first.mkdir()
            second.mkdir()
            config = load_mcp_access_config(
                environ={
                    "ARVIS_MCP_PROFILE": "chatgpt",
                    "ARVIS_MCP_PROJECT_ROOT": str(second),
                    "ARVIS_MCP_ALLOWED_ROOTS": os.pathsep.join((str(first), str(second))),
                },
                cwd=base,
            )

        self.assertEqual(config.allowed_roots, (first.resolve(), second.resolve()))
        self.assertEqual(config.default_root, second.resolve())
        self.assertIsNone(config.configuration_error)


@unittest.skipUnless(importlib.util.find_spec("mcp"), "mcp SDK is not installed")
class McpToolMetadataTests(unittest.TestCase):
    READ_ONLY_SYSTEM_TOOLS = {
        "system_info",
        "system_metrics",
        "binary_exists",
        "package_installed",
        "package_info",
        "package_search",
        "plasma_info",
        "qml_module_available",
    }
    READ_ONLY_PROJECT_TOOLS = {
        "project_state",
        "git_diff",
        "git_inspect",
        "validate_manifest",
        "validate_mod_artifact",
        "stardew_environment",
        "smapi_log_excerpt",
        "smapi_mod_status",
    }
    CONTROL_PROJECT_TOOLS = {"build_project", "test_project"}
    AGENT_TOOLS = {
        "codex_agent_create",
        "codex_agent_status",
        "codex_agent_result",
        "codex_agent_close",
        "codex_agent_show",
    }

    def _load_server(self, profile: str, root: Path):
        env = {
            "ARVIS_MCP_PROFILE": profile,
            "ARVIS_MCP_PROJECT_ROOT": str(root),
            "ARVIS_MCP_ALLOWED_ROOTS": str(root),
            "ARVIS_MCP_WRITABLE_ROOTS": str(root),
            "ARVIS_CODEX_AGENT_CONTROL_ENABLED": "false",
            "ARVIS_SAFE_COMMAND_CONTROL_ENABLED": "false",
            "ARVIS_SAFE_GIT_CONTROL_ENABLED": "false",
        }
        with patch.dict(os.environ, env):
            import arvis_mcp_server

            return importlib.reload(arvis_mcp_server)

    def test_codex_publishes_all_tools_with_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            module = self._load_server("codex", Path(tmpdir))
            tools = {tool.name: tool for tool in asyncio.run(module.mcp.list_tools())}

        self.assertEqual(
            set(tools),
            {
                "project_map",
                "read_file_excerpt",
                "grep_project",
                "git_status_summary",
                "task_brief",
                "memory_read",
                "memory_append",
            }
            | self.READ_ONLY_SYSTEM_TOOLS
            | self.READ_ONLY_PROJECT_TOOLS
            | self.CONTROL_PROJECT_TOOLS,
        )
        self.assertEqual(len(tools), 25)
        self.assertTrue(
            all(any("а" <= char.casefold() <= "я" or char.casefold() in "іїєґ" for char in tool.description) for tool in tools.values())
        )
        for name, tool in tools.items():
            annotations = tool.annotations
            self.assertIsNotNone(annotations)
            self.assertEqual(annotations.openWorldHint, False)
            self.assertEqual(annotations.destructiveHint, False)
            if name == "memory_append" or name in self.CONTROL_PROJECT_TOOLS:
                self.assertEqual(annotations.readOnlyHint, False)
                self.assertEqual(annotations.idempotentHint, False)
            else:
                self.assertEqual(annotations.readOnlyHint, True)
                self.assertEqual(annotations.idempotentHint, True)

    def test_chatgpt_does_not_publish_memory_append(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            module = self._load_server("chatgpt", Path(tmpdir))
            tools = {tool.name for tool in asyncio.run(module.mcp.list_tools())}

        self.assertEqual(
            tools,
            {
                "project_map",
                "read_file_excerpt",
                "grep_project",
                "git_status_summary",
                "task_brief",
                "memory_read",
            }
            | self.READ_ONLY_SYSTEM_TOOLS
            | self.READ_ONLY_PROJECT_TOOLS
            | self.CONTROL_PROJECT_TOOLS,
        )
        self.assertEqual(len(tools), 24)
        self.assertNotIn("memory_append", tools)

    def test_agent_lifecycle_tools_are_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch.dict(
                os.environ,
                {
                    "ARVIS_CODEX_AGENT_CONTROL_ENABLED": "true",
                    "ARVIS_SAFE_GIT_CONTROL_ENABLED": "false",
                },
            ):
                import arvis_mcp_server
                module = importlib.reload(arvis_mcp_server)
                tools = {tool.name: tool for tool in asyncio.run(module.mcp.list_tools())}

        self.assertTrue(self.AGENT_TOOLS.issubset(tools))
        self.assertFalse(tools["codex_agent_create"].annotations.readOnlyHint)
        self.assertTrue(tools["codex_agent_status"].annotations.readOnlyHint)
        self.assertFalse(tools["codex_agent_show"].annotations.readOnlyHint)

    def test_exact_enabled_tool_counts_and_schemas_match_profiles(self) -> None:
        for profile, expected in (("chatgpt", 29), ("codex", 30)):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmpdir:
                root = Path(tmpdir)
                env = {
                    "ARVIS_MCP_PROFILE": profile,
                    "ARVIS_MCP_PROJECT_ROOT": str(root),
                    "ARVIS_MCP_ALLOWED_ROOTS": str(root),
                    "ARVIS_MCP_WRITABLE_ROOTS": str(root),
                    "ARVIS_CODEX_AGENT_CONTROL_ENABLED": "true",
                    "ARVIS_SAFE_COMMAND_CONTROL_ENABLED": "false",
                    "ARVIS_SAFE_GIT_CONTROL_ENABLED": "false",
                }
                with patch.dict(os.environ, env):
                    import arvis_mcp_server
                    module = importlib.reload(arvis_mcp_server)
                    tools = {tool.name: tool for tool in asyncio.run(module.mcp.list_tools())}

            self.assertEqual(len(tools), expected)
            self.assertEqual(set(tools) & self.AGENT_TOOLS, self.AGENT_TOOLS)
            self.assertTrue(all(tool.inputSchema.get("type") == "object" for tool in tools.values()))
            if profile == "chatgpt":
                self.assertNotIn("memory_append", tools)
            else:
                self.assertIn("memory_append", tools)

    def test_new_tool_schemas_are_narrow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env = {
                "ARVIS_MCP_PROFILE": "chatgpt",
                "ARVIS_MCP_PROJECT_ROOT": str(root),
                "ARVIS_MCP_ALLOWED_ROOTS": str(root),
                "ARVIS_MCP_WRITABLE_ROOTS": str(root),
                "ARVIS_CODEX_AGENT_CONTROL_ENABLED": "true",
                "ARVIS_SAFE_COMMAND_CONTROL_ENABLED": "false",
                "ARVIS_SAFE_GIT_CONTROL_ENABLED": "false",
            }
            with patch.dict(os.environ, env):
                import arvis_mcp_server
                module = importlib.reload(arvis_mcp_server)
                tools = {tool.name: tool for tool in asyncio.run(module.mcp.list_tools())}

        self.assertEqual(tools["codex_agent_create"].inputSchema["properties"]["visible"]["type"], "boolean")
        self.assertEqual(set(tools["codex_agent_show"].inputSchema["properties"]), {"agent_id", "project_root"})
        self.assertEqual(
            set(tools["git_inspect"].inputSchema["properties"]),
            {"project_root", "max_tracked_files", "max_commits", "max_history_paths"},
        )
        serialized = repr({name: tool.inputSchema for name, tool in tools.items()})
        self.assertNotIn("shell", serialized.casefold())
        self.assertNotIn("command", tools["codex_agent_show"].inputSchema["properties"])

    def test_system_tools_are_read_only_in_both_profiles(self) -> None:
        for profile in ("codex", "chatgpt"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmpdir:
                module = self._load_server(profile, Path(tmpdir))
                tools = {tool.name: tool for tool in asyncio.run(module.mcp.list_tools())}

            for name in self.READ_ONLY_SYSTEM_TOOLS:
                annotations = tools[name].annotations
                self.assertEqual(annotations.readOnlyHint, True)
                self.assertEqual(annotations.destructiveHint, False)
                self.assertEqual(annotations.idempotentHint, True)
                self.assertEqual(annotations.openWorldHint, False)

    def test_system_metrics_accepts_no_filesystem_path_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            module = self._load_server("chatgpt", Path(tmpdir))
            tools = {tool.name: tool for tool in asyncio.run(module.mcp.list_tools())}

        schema = tools["system_metrics"].inputSchema
        self.assertEqual(schema.get("properties"), {})
        self.assertFalse(schema.get("required"))

    def test_system_context_errors_keep_safe_error_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            module = self._load_server("codex", Path(tmpdir))

        result = module._safe_call(
            lambda: (_ for _ in ()).throw(module.SystemContextError("invalid_input", "Safe message."))
        )

        self.assertEqual(
            result,
            {"ok": False, "error_code": "invalid_input", "error": "Safe message."},
        )

    def test_system_metrics_wrapper_uses_safe_call_result_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            module = self._load_server("chatgpt", Path(tmpdir))

        with patch.object(
            module.system_context,
            "system_metrics",
            return_value={"cpu": {"usage_percent": 0.0}, "warnings": []},
        ):
            result = module.system_metrics()

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["cpu"]["usage_percent"], 0.0)

    def test_protocol_boundary_hides_unexpected_error_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            module = self._load_server("codex", Path(tmpdir))

        with self.assertLogs("arvis.mcp", level="ERROR"):
            result = module._safe_call(lambda: (_ for _ in ()).throw(OSError("private detail")))

        self.assertEqual(result, {"ok": False, "error": "Внутрішня помилка інструменту MCP."})
        self.assertNotIn("private detail", repr(result))


if __name__ == "__main__":
    unittest.main()
