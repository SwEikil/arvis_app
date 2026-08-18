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

    def _commit(self, relative: str, content: str, message: str) -> str:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "--", relative], cwd=self.root, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Arvis Test", "-c", "user.email=arvis@example.invalid", "commit", "-q", "-m", message],
            cwd=self.root,
            check=True,
        )
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, text=True, capture_output=True, check=True).stdout.strip()

    def test_git_diff_rejects_path_traversal(self) -> None:
        with self.assertRaises(ProjectContextError):
            project_operations.git_diff(str(self.root), path="../outside", access_config=self.config)

    def test_project_state_lists_changed_files(self) -> None:
        (self.root / "file.cs").write_text("class C {}\n", encoding="utf-8")
        result = project_operations.project_state(str(self.root), access_config=self.config)
        self.assertEqual(result["changed_files"][0]["path"], "file.cs")

    def test_git_inspect_reports_head_tracked_history_and_history_paths(self) -> None:
        self._commit("first.txt", "one\n", "first")
        head = self._commit("nested/second.txt", "two\n", "API_KEY=fake-git-secret")

        result = project_operations.git_inspect(str(self.root), access_config=self.config)

        self.assertTrue(result["is_git_repo"])
        self.assertEqual(result["head"], head)
        self.assertIn(result["branch"], {"main", "master"})
        self.assertEqual(result["tracked_files"], ["first.txt", "nested/second.txt"])
        self.assertEqual(len(result["commits"]), 2)
        self.assertEqual(result["commits"][0]["sha"], head)
        self.assertEqual(set(result["history_paths"]), {"first.txt", "nested/second.txt"})
        self.assertNotIn("fake-git-secret", repr(result))

    def test_git_inspect_enforces_each_bound(self) -> None:
        self._commit("one.txt", "one\n", "one")
        self._commit("two.txt", "two\n", "two")

        result = project_operations.git_inspect(
            str(self.root),
            max_tracked_files=1,
            max_commits=1,
            max_history_paths=1,
            access_config=self.config,
        )

        self.assertEqual(len(result["tracked_files"]), 1)
        self.assertEqual(len(result["commits"]), 1)
        self.assertEqual(len(result["history_paths"]), 1)
        self.assertTrue(result["tracked_files_truncated"])
        self.assertTrue(result["history_truncated"])
        self.assertTrue(result["history_paths_truncated"])

    def test_git_inspect_rejects_outside_or_invalid_project_root(self) -> None:
        outside = self.root.parent / "outside"
        outside.mkdir()
        with self.assertRaises(ProjectContextError):
            project_operations.git_inspect(str(outside), access_config=self.config)
        child = self.root / "allowed-child-with-parent-git"
        child.mkdir()
        result = project_operations.git_inspect(str(child), access_config=self.config)
        self.assertFalse(result["is_git_repo"])

    def test_build_requires_explicit_writable_root(self) -> None:
        read_only_config = load_mcp_access_config(
            environ={
                "ARVIS_MCP_PROFILE": "chatgpt",
                "ARVIS_MCP_PROJECT_ROOT": str(self.root),
                "ARVIS_MCP_ALLOWED_ROOTS": str(self.root),
            },
            cwd=self.root,
        )
        with self.assertRaises(ProjectContextError):
            project_operations.build_project(str(self.root), access_config=read_only_config)

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
