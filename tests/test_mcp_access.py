from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_access import PROFILE_CHATGPT, PROFILE_CODEX, load_mcp_access_config


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
        "binary_exists",
        "package_installed",
        "package_info",
        "package_search",
        "plasma_info",
        "qml_module_available",
    }

    def _load_server(self, profile: str, root: Path):
        env = {
            "ARVIS_MCP_PROFILE": profile,
            "ARVIS_MCP_PROJECT_ROOT": str(root),
            "ARVIS_MCP_ALLOWED_ROOTS": str(root),
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
            | self.READ_ONLY_SYSTEM_TOOLS,
        )
        self.assertEqual(len(tools), 14)
        self.assertTrue(
            all(any("а" <= char.casefold() <= "я" or char.casefold() in "іїєґ" for char in tool.description) for tool in tools.values())
        )
        for name, tool in tools.items():
            annotations = tool.annotations
            self.assertIsNotNone(annotations)
            self.assertEqual(annotations.openWorldHint, False)
            self.assertEqual(annotations.destructiveHint, False)
            if name == "memory_append":
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
            | self.READ_ONLY_SYSTEM_TOOLS,
        )
        self.assertEqual(len(tools), 13)
        self.assertNotIn("memory_append", tools)

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

    def test_protocol_boundary_hides_unexpected_error_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            module = self._load_server("codex", Path(tmpdir))

        with self.assertLogs("arvis.mcp", level="ERROR"):
            result = module._safe_call(lambda: (_ for _ in ()).throw(OSError("private detail")))

        self.assertEqual(result, {"ok": False, "error": "Внутрішня помилка інструменту MCP."})
        self.assertNotIn("private detail", repr(result))


if __name__ == "__main__":
    unittest.main()
