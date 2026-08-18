from __future__ import annotations

import importlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import project_context
from mcp_access import load_mcp_access_config
from project_context import ProjectContextError


class ProjectContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name) / "repo"
        self.root.mkdir()
        self._env = patch.dict(
            os.environ,
            {
                "ARVIS_MCP_PROFILE": "codex",
                "ARVIS_MCP_PROJECT_ROOT": str(self.root),
                "ARVIS_MCP_ALLOWED_ROOTS": str(self.root),
            },
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
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

    def test_project_root_allowlist_rejects_sibling_parent_root_and_fake_home(self) -> None:
        sibling = self.root.parent / "sibling"
        sibling.mkdir()
        fake_home = self.root.parent / "fake-home"
        fake_home.mkdir()

        for denied in (sibling, self.root.parent, Path("/"), Path("/etc"), fake_home):
            with self.subTest(denied=denied):
                with self.assertRaises(ProjectContextError):
                    project_context.resolve_project_root(str(denied))

    def test_project_root_allowlist_rejects_dot_dot_escape(self) -> None:
        sibling = self.root.parent / "sibling"
        sibling.mkdir()

        with self.assertRaises(ProjectContextError):
            project_context.resolve_project_root("../sibling")

    def test_project_root_allowlist_rejects_symlink_escape(self) -> None:
        outside = self.root.parent / "outside"
        outside.mkdir()
        (self.root / "outside-link").symlink_to(outside, target_is_directory=True)

        with self.assertRaises(ProjectContextError):
            project_context.resolve_project_root("outside-link")

    def test_parent_workspace_allows_bounded_private_handoff_but_not_escape(self) -> None:
        parent = self.root.parent / "workspace"
        public = parent / "public-mod"
        private = parent / "local-dev"
        handoff = private / "handoffs" / "stage1.md"
        public.mkdir(parents=True)
        handoff.parent.mkdir(parents=True)
        handoff.write_text("API_KEY=fake-private-value\nStage 1 facts\n", encoding="utf-8")
        unrelated = self.root.parent / "unrelated"
        unrelated.mkdir()
        (unrelated / "secret.md").write_text("outside\n", encoding="utf-8")
        config = load_mcp_access_config(
            environ={
                "ARVIS_MCP_PROFILE": "chatgpt",
                "ARVIS_MCP_PROJECT_ROOT": str(public),
                "ARVIS_MCP_ALLOWED_ROOTS": str(parent),
                "ARVIS_MCP_WRITABLE_ROOTS": str(public),
            },
            cwd=parent,
        )

        from_parent = project_context.read_file_excerpt(
            "local-dev/handoffs/stage1.md",
            project_root=str(parent),
            access_config=config,
        )
        from_private = project_context.read_file_excerpt(
            "handoffs/stage1.md",
            project_root=str(private),
            access_config=config,
        )

        self.assertIn("Stage 1 facts", from_parent["content"])
        self.assertEqual(from_parent["content"], from_private["content"])
        self.assertNotIn("fake-private-value", from_parent["content"])
        with self.assertRaises(ProjectContextError):
            project_context.read_file_excerpt("../unrelated/secret.md", project_root=str(parent), access_config=config)
        with self.assertRaises(ProjectContextError):
            project_context.resolve_project_root(str(unrelated), access_config=config)

    def test_chatgpt_profile_without_allowlist_fails_closed(self) -> None:
        config = load_mcp_access_config(
            environ={"ARVIS_MCP_PROFILE": "chatgpt"},
            cwd=self.root,
        )

        self.assertEqual(config.allowed_roots, ())
        with self.assertRaises(ProjectContextError):
            project_context.resolve_project_root(access_config=config)

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

        self.assertIn("Некоректний regex", str(caught.exception))

    def test_grep_project_rejects_risky_or_oversized_regex(self) -> None:
        self._write("app.py", "aaaa\n")

        for query in (
            "(a+)+$",
            "(a|aa)+$",
            "a" * (project_context.MAX_REGEX_PATTERN_CHARS + 1),
        ):
            with self.subTest(query=query[:20]):
                with self.assertRaises(ProjectContextError):
                    project_context.grep_project(query, project_root=str(self.root), regex=True)

    def test_read_and_grep_redact_fake_credentials_without_destroying_code(self) -> None:
        source = """API_KEY=fake-secret-value
apiKey: fake-secret-value
Authorization: Bearer fake-token
password=fake-password
Cookie: session=fake-cookie
token=fake-token
client_secret=fake-client-secret
access_token=fake-access-token
refresh_token=fake-refresh-token
-----BEGIN PRIVATE KEY-----
fake-private-key-body
-----END PRIVATE KEY-----

def handle(token: str, password: str) -> None:
    # token and password are interface names here.
    token = get_token()
"""
        self._write("settings.py", source)

        excerpt = project_context.read_file_excerpt("settings.py", project_root=str(self.root))
        private_key_body = project_context.read_file_excerpt(
            "settings.py",
            project_root=str(self.root),
            start_line=11,
            end_line=11,
        )
        grep = project_context.grep_project("fake-", project_root=str(self.root))
        serialized = repr(excerpt) + repr(private_key_body) + repr(grep)

        for fake_value in (
            "fake-secret-value",
            "fake-token",
            "fake-password",
            "fake-cookie",
            "fake-client-secret",
            "fake-access-token",
            "fake-refresh-token",
            "fake-private-key-body",
        ):
            self.assertNotIn(fake_value, serialized)
        self.assertIn("def handle(token: str, password: str) -> None:", excerpt["content"])
        self.assertIn("# token and password are interface names here.", excerpt["content"])
        self.assertIn("token = get_token()", excerpt["content"])

    def test_memory_read_redacts_fake_credentials(self) -> None:
        project_context.memory_append(
            "API_KEY=fake-memory-secret",
            name="facts.md",
            project_root=str(self.root),
        )

        result = project_context.memory_read("facts.md", project_root=str(self.root))

        self.assertNotIn("fake-memory-secret", result["content"])
        self.assertIn("[REDACTED]", result["content"])

    def test_task_brief_does_not_echo_credentials_in_terms(self) -> None:
        self._write("notes.txt", "inspect package metadata\n")
        task = "inspect token=fake-task-secret sk-proj-abcdefghijklmnop"

        result = project_context.task_brief(task, project_root=str(self.root))

        serialized = repr(result)
        self.assertNotIn("fake-task-secret", serialized)
        self.assertNotIn("sk-proj-abcdefghijklmnop", serialized)
        self.assertNotIn("REDACTED", result["terms"])

    def test_secret_files_and_oversized_files_are_not_read(self) -> None:
        self._write(".env.production", "API_KEY=fake-secret-value\n")
        self._write("id_rsa", "fake private key\n")
        self._write("certificate.pem", "fake private key\n")
        oversized = self.root / "large.txt"
        oversized.write_text("x" * (project_context.MAX_SCAN_FILE_BYTES + 1), encoding="utf-8")

        result = project_context.project_map(str(self.root))
        paths = {item["path"] for item in result["files"]}

        self.assertNotIn(".env.production", paths)
        self.assertNotIn("id_rsa", paths)
        self.assertNotIn("certificate.pem", paths)
        self.assertNotIn("large.txt", paths)
        with self.assertRaises(ProjectContextError):
            project_context.read_file_excerpt("large.txt", project_root=str(self.root))

    def test_git_status_summary_does_not_crash_outside_git_repo(self) -> None:
        result = project_context.git_status_summary(str(self.root))

        self.assertFalse(result["is_git_repo"])
        self.assertIn("status_short", result["outputs"])
        self.assertTrue(result["errors"])

    def test_git_status_timeout_is_a_controlled_path_free_error(self) -> None:
        timeout = project_context.subprocess.TimeoutExpired(cmd=["git"], timeout=5)
        with patch("project_context.subprocess.run", side_effect=timeout):
            result = project_context.git_status_summary(str(self.root))

        self.assertFalse(result["is_git_repo"])
        self.assertTrue(result["errors"])
        self.assertNotIn(str(self.root), repr(result))
        self.assertTrue(
            all(
                message == "Перевищено час очікування команди Git."
                for message in result["errors"].values()
            )
        )

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

    def test_chatgpt_profile_blocks_memory_append(self) -> None:
        config = load_mcp_access_config(
            environ={
                "ARVIS_MCP_PROFILE": "chatgpt",
                "ARVIS_MCP_ALLOWED_ROOTS": str(self.root),
            },
            cwd=self.root,
        )

        with self.assertRaises(ProjectContextError):
            project_context.memory_append(
                "must not be written",
                project_root=str(self.root),
                access_config=config,
            )
        self.assertFalse((self.root / ".arvis_mcp_memory").exists())

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
            repo_root / "mcp_access.py",
            repo_root / "mcp_security.py",
            repo_root / "codex_agent_terminal.py",
            repo_root / "system_context.py",
            repo_root / "tests" / "test_system_context.py",
            repo_root / ".env.example",
            repo_root / "doctor.py",
            repo_root / "README.md",
            repo_root / "docs" / "configuration.md",
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
