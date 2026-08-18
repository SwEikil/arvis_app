from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import project_operations
from mcp_access import load_mcp_access_config
from project_context import ProjectContextError


class ProjectOperationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.config = load_mcp_access_config(
            environ={
                "ARVIS_MCP_PROFILE": "codex",
                "ARVIS_MCP_PROJECT_ROOT": str(self.root),
                "ARVIS_MCP_ALLOWED_ROOTS": str(self.root),
            },
            cwd=self.root,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _manifest(self, **overrides: str) -> dict[str, str]:
        value = {
            "Name": "Example Mod",
            "Author": "Test",
            "Version": "1.0.0",
            "Description": "Test mod",
            "UniqueID": "Test.WhereYouFell",
            "EntryDll": "WhereYouFell.dll",
            "MinimumApiVersion": "4.5.2",
        }
        value.update(overrides)
        return value

    def test_git_diff_rejects_path_traversal(self) -> None:
        with self.assertRaises(ProjectContextError):
            project_operations.git_diff(str(self.root), path="../outside", access_config=self.config)

    def test_project_state_lists_changed_files(self) -> None:
        (self.root / "file.cs").write_text("class C {}\n", encoding="utf-8")
        result = project_operations.project_state(str(self.root), access_config=self.config)
        self.assertEqual(result["changed_files"][0]["path"], "file.cs")

    def test_manifest_rejects_project_token_for_minimum_api(self) -> None:
        (self.root / "manifest.json").write_text(json.dumps(self._manifest(MinimumApiVersion="%ProjectVersion%")), encoding="utf-8")
        result = project_operations.validate_manifest(str(self.root), access_config=self.config)
        self.assertFalse(result["valid"])

    def test_artifact_rejects_traversal_and_private_files(self) -> None:
        archive = self.root / "mod.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("manifest.json", json.dumps(self._manifest()))
            handle.writestr("WhereYouFell.dll", b"dll")
            handle.writestr("../escape", b"bad")
            handle.writestr("AGENTS.md", "private")
        result = project_operations.validate_mod_artifact(str(self.root), "mod.zip", access_config=self.config)
        self.assertFalse(result["valid"])
        self.assertTrue(any("Unsafe" in item for item in result["errors"]))
        self.assertTrue(any("AI/private" in item for item in result["errors"]))

    def test_log_excerpt_is_bounded_and_redacted(self) -> None:
        log = Path(self.temp.name) / "SMAPI-latest.txt"
        log.write_text("first\nAPI_KEY=fake-secret\nlast\n", encoding="utf-8")
        with patch.dict(os.environ, {"ARVIS_SMAPI_LOG_PATH": str(log)}):
            result = project_operations.smapi_log_excerpt(max_lines=2, max_chars=500)
        self.assertNotIn("fake-secret", result["content"])
        self.assertNotIn("first", result["content"])

    def test_command_output_removes_local_absolute_paths(self) -> None:
        output = project_operations._sanitize_command_output(
            f"built {self.root}/bin and /home/private-user/game",
            self.root,
        )
        self.assertEqual(output, "built ./bin and <HOME>/game")


if __name__ == "__main__":
    unittest.main()
