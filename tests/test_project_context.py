from __future__ import annotations

import importlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

import project_context
from project_context import ProjectContextError


class ProjectContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name) / "repo"
        self.root.mkdir()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write(self, relative: str, text: str = "hello\n") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_project_map_skips_private_and_generated_paths(self) -> None:
        self._write("main.py", "print('ok')\n")
        self._write(".env", "SECRET=value\n")
        self._write(".runtime/state.json", "{}\n")
        self._write(".venv/lib/module.py", "hidden = True\n")
        self._write("models/model.txt", "hidden\n")
        self._write("node_modules/pkg/index.js", "hidden\n")
        self._write(".git/HEAD", "ref: refs/heads/main\n")

        result = project_context.project_map(str(self.root))
        paths = {item["path"] for item in result["files"]}

        self.assertIn("main.py", paths)
        self.assertNotIn(".env", paths)
        self.assertNotIn(".runtime/state.json", paths)
        self.assertNotIn(".venv/lib/module.py", paths)
        self.assertNotIn("models/model.txt", paths)
        self.assertNotIn("node_modules/pkg/index.js", paths)
        self.assertNotIn(".git/HEAD", paths)

    def test_project_map_and_grep_skip_symlink_to_outside_root(self) -> None:
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside secret needle\n", encoding="utf-8")
        (self.root / "leak.txt").symlink_to(outside)
        self._write("safe.txt", "safe needle\n")

        map_result = project_context.project_map(str(self.root))
        paths = {item["path"] for item in map_result["files"]}
        grep_result = project_context.grep_project("needle", project_root=str(self.root))
        grep_paths = {item["path"] for item in grep_result["matches"]}

        self.assertIn("safe.txt", paths)
        self.assertNotIn("leak.txt", paths)
        self.assertEqual(grep_paths, {"safe.txt"})

    def test_safe_project_path_rejects_traversal_outside_root(self) -> None:
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")

        with self.assertRaises(ProjectContextError):
            project_context.safe_project_path(self.root, "../outside.txt")

    def test_read_file_excerpt_reads_bounded_lines(self) -> None:
        self._write("notes.txt", "one\ntwo\nthree\nfour\n")

        result = project_context.read_file_excerpt(
            "notes.txt",
            project_root=str(self.root),
            start_line=2,
            end_line=3,
        )

        self.assertEqual(result["path"], "notes.txt")
        self.assertEqual(result["start_line"], 2)
        self.assertEqual(result["end_line"], 3)
        self.assertEqual(result["total_lines"], 4)
        self.assertEqual(result["content"], "two\nthree\n")
        self.assertFalse(result["truncated"])

    def test_grep_project_returns_relative_paths_and_line_numbers(self) -> None:
        self._write("app.py", "alpha\nneedle here\nomega\n")
        self._write("nested/other.py", "nothing\nNeedle again\n")

        result = project_context.grep_project("needle", project_root=str(self.root))

        self.assertEqual(result["match_count"], 2)
        self.assertEqual(result["matches"][0]["path"], "app.py")
        self.assertEqual(result["matches"][0]["line_number"], 2)
        self.assertEqual(result["matches"][1]["path"], "nested/other.py")
        self.assertEqual(result["matches"][1]["line_number"], 2)

    def test_grep_project_invalid_regex_is_controlled_error(self) -> None:
        self._write("app.py", "text\n")

        with self.assertRaises(ProjectContextError) as caught:
            project_context.grep_project("[", project_root=str(self.root), regex=True)

        self.assertIn("Invalid regex", str(caught.exception))

    def test_git_status_summary_does_not_crash_outside_git_repo(self) -> None:
        result = project_context.git_status_summary(str(self.root))

        self.assertFalse(result["is_git_repo"])
        self.assertIn("status_short", result["outputs"])
        self.assertTrue(result["errors"])

    def test_memory_append_and_read_use_memory_directory_only(self) -> None:
        append_result = project_context.memory_append(
            "remember this fact",
            name="facts.md",
            project_root=str(self.root),
            source="unit_test",
        )
        read_result = project_context.memory_read("facts.md", project_root=str(self.root))

        self.assertEqual(append_result["path"], ".arvis_mcp_memory/facts.md")
        self.assertTrue((self.root / ".arvis_mcp_memory" / "facts.md").exists())
        self.assertFalse((self.root / "facts.md").exists())
        self.assertIn("source=unit_test", read_result["content"])
        self.assertIn("remember this fact", read_result["content"])

    def test_unsupported_memory_filename_is_rejected(self) -> None:
        with self.assertRaises(ProjectContextError):
            project_context.memory_read("../facts.md", project_root=str(self.root))

        with self.assertRaises(ProjectContextError):
            project_context.memory_append("note", name="private.md", project_root=str(self.root))

    def test_public_mcp_files_do_not_contain_real_personal_paths_or_config(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        checked_paths = [
            repo_root / "project_context.py",
            repo_root / "arvis_mcp_server.py",
            repo_root / "docs" / "mcp_context_servant.md",
            repo_root / "AGENTS.md",
        ]
        forbidden = ["PRIVATE_TOKEN", "LOCAL_CODEX_TOKEN"]
        home = Path.home()
        if home.name:
            forbidden.append(home.name)
        if str(home):
            forbidden.append(str(home))
        user_name = os.environ.get("USER") or os.environ.get("USERNAME")
        if user_name:
            forbidden.append(user_name)

        for path in checked_paths:
            content = path.read_text(encoding="utf-8")
            for text in forbidden:
                self.assertNotIn(text, content, f"{text!r} found in {path}")

        self.assertFalse((repo_root / ".codex" / "config.toml").exists())

    @unittest.skipIf(importlib.util.find_spec("mcp") is None, "mcp SDK is not installed")
    def test_arvis_mcp_server_import_smoke(self) -> None:
        module = importlib.import_module("arvis_mcp_server")

        self.assertEqual(module.mcp.name, "Arvis MCP Context Servant")


if __name__ == "__main__":
    unittest.main()
