from __future__ import annotations

import os
import inspect
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest import mock

import safe_git_control
from safe_git_control import (
    SafeGitConfigError,
    SafeGitOperationError,
    SafeGitPolicy,
    commit_staged,
    preflight,
    push_current,
    rewrite_unpushed_identity,
    stage_paths,
)


GIT = shutil.which("git")


@unittest.skipUnless(GIT, "git is required for focused safe Git tests")
class SafeGitControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "project"
        self.remote = self.base / "remote.git"
        self.root.mkdir()
        self._git(self.root, "init", "-b", "main")
        self._git(self.remote.parent, "init", "--bare", str(self.remote))
        self._git(self.root, "config", "user.name", "Ambient Private Name")
        self._git(self.root, "config", "user.email", "ambient-private@example.test")
        (self.root / "tracked.txt").write_text("initial\n", encoding="utf-8")
        (self.root / "delete.txt").write_text("delete me\n", encoding="utf-8")
        self._git(self.root, "add", "tracked.txt", "delete.txt")
        self._git(self.root, "commit", "-m", "initial")
        self._git(self.root, "remote", "add", "origin", str(self.remote))
        self._git(self.root, "push", "origin", "main")
        self.policy = SafeGitPolicy(
            remote_name="origin",
            expected_remote_url=str(self.remote),
            public_name="Arvis Public",
            public_email="arvis-public@example.invalid",
            push_enabled=True,
            history_rewrite_enabled=True,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _git(
        self,
        cwd: Path,
        *args: str,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [GIT, *args],
            cwd=cwd,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def _commit_via_core(self, name: str = "next.txt", content: str = "next\n") -> str:
        (self.root / name).write_text(content, encoding="utf-8")
        stage_paths(self.root, [name], self.policy)
        return commit_staged(self.root, "Safe public commit", self.policy).sha

    def _git_bytes(
        self,
        cwd: Path,
        *args: str,
        check: bool = True,
        input_bytes: bytes | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [GIT, *args],
            cwd=cwd,
            check=check,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def _raw_commit(self, commit_id: str) -> bytes:
        return self._git_bytes(self.root, "cat-file", "commit", commit_id).stdout

    def _direct_commit(
        self,
        tree: str,
        parent: str,
        message: bytes,
        *,
        author_date: str,
        committer_date: str,
        private_name: str = "Old Private Identity",
        private_email: str = "old-private@example.test",
        private_committer_name: str = "Old Private Committer",
        private_committer_email: str = "old-private-committer@example.test",
    ) -> str:
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": private_name,
                "GIT_AUTHOR_EMAIL": private_email,
                "GIT_AUTHOR_DATE": author_date,
                "GIT_COMMITTER_NAME": private_committer_name,
                "GIT_COMMITTER_EMAIL": private_committer_email,
                "GIT_COMMITTER_DATE": committer_date,
            }
        )
        return self._git_bytes(
            self.root,
            "commit-tree",
            tree,
            "-p",
            parent,
            input_bytes=message,
            env=env,
        ).stdout.decode("ascii").strip()

    def _make_linear_commits(self, count: int) -> tuple[list[str], list[bytes]]:
        parent = self._git(self.root, "rev-parse", "HEAD").stdout.strip()
        commits: list[str] = []
        messages: list[bytes] = []
        for index in range(count):
            (self.root / "tracked.txt").write_text(f"version {index}\n", encoding="utf-8")
            self._git(self.root, "add", "tracked.txt")
            tree = self._git(self.root, "write-tree").stdout.strip()
            message = (
                f"rewrite subject {index}\n\nblank line body {index}: blåbær 🫐\n".encode("utf-8")
            )
            commit_id = self._direct_commit(
                tree,
                parent,
                message,
                author_date=f"17000000{index + 10} +0530",
                committer_date=f"17000001{index + 10} -0230",
            )
            self._git(self.root, "update-ref", "refs/heads/main", commit_id, parent)
            commits.append(commit_id)
            messages.append(message)
            parent = commit_id
        return commits, messages

    def _write_synthetic_commit(self, raw: bytes) -> str:
        commit_id = self._git_bytes(
            self.root,
            "hash-object",
            "--literally",
            "-t",
            "commit",
            "-w",
            "--stdin",
            input_bytes=raw,
        ).stdout.decode("ascii").strip()
        old_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()
        self._git(self.root, "update-ref", "refs/heads/main", commit_id, old_head)
        return commit_id

    def _advance_remote_without_fetch(self) -> str:
        clone = self.base / "remote-writer"
        self._git(self.base, "clone", str(self.remote), str(clone))
        self._git(clone, "checkout", "main")
        self._git(clone, "config", "user.name", "Other Writer")
        self._git(clone, "config", "user.email", "other-writer@example.invalid")
        (clone / "remote-only.txt").write_text("remote change\n", encoding="utf-8")
        self._git(clone, "add", "remote-only.txt")
        self._git(clone, "commit", "-m", "remote advances")
        new_head = self._git(clone, "rev-parse", "HEAD").stdout.strip()
        self._git(clone, "push", "origin", "main")
        return new_head

    def _assert_linear_rewrite(self, count: int) -> safe_git_control.SafeGitRewriteResult:
        remote_head = self._git(self.remote, "rev-parse", "refs/heads/main").stdout.strip()
        originals, messages = self._make_linear_commits(count)
        original_objects = [safe_git_control._parse_raw_commit(self._raw_commit(sha)) for sha in originals]

        result = rewrite_unpushed_identity(self.root, self.policy)

        rewritten_ids = self._git(
            self.root,
            "rev-list",
            "--reverse",
            f"{remote_head}..HEAD",
        ).stdout.splitlines()
        rewritten_objects = [
            safe_git_control._parse_raw_commit(self._raw_commit(sha)) for sha in rewritten_ids
        ]
        self.assertEqual(result.rewritten_count, count)
        self.assertEqual(result.old_head, originals[-1])
        self.assertEqual(result.new_head, rewritten_ids[-1])
        self.assertNotEqual(result.old_head, result.new_head)
        self.assertEqual(result.branch, "main")
        self.assertEqual(result.remote_name, "origin")
        self.assertEqual(
            self._git(self.remote, "rev-parse", "refs/heads/main").stdout.strip(),
            remote_head,
        )
        self.assertEqual([item.message for item in original_objects], messages)
        for original, rewritten in zip(original_objects, rewritten_objects, strict=True):
            self.assertEqual(rewritten.tree, original.tree)
            self.assertEqual(rewritten.message, original.message)
            self.assertEqual(rewritten.author.timestamp, original.author.timestamp)
            self.assertEqual(rewritten.author.timezone, original.author.timezone)
            self.assertEqual(rewritten.committer.timestamp, original.committer.timestamp)
            self.assertEqual(rewritten.committer.timezone, original.committer.timezone)
            self.assertEqual(rewritten.author.name, self.policy.public_name.encode("utf-8"))
            self.assertEqual(rewritten.author.email, self.policy.public_email.encode("utf-8"))
            self.assertEqual(rewritten.committer.name, self.policy.public_name.encode("utf-8"))
            self.assertEqual(rewritten.committer.email, self.policy.public_email.encode("utf-8"))
        return result

    def test_policy_validation_and_repr_redaction(self) -> None:
        invalid_fields = (
            {"remote_name": "-origin"},
            {"remote_name": "bad/name"},
            {"expected_remote_url": ""},
            {"expected_remote_url": " remote"},
            {"public_name": "bad\nname"},
            {"public_email": "not-an-email"},
            {"push_enabled": 1},
            {"history_rewrite_enabled": 1},
        )
        defaults: dict[str, object] = {
            "remote_name": "origin",
            "expected_remote_url": "file:///safe/remote.git",
            "public_name": "Public Name",
            "public_email": "public@example.invalid",
            "push_enabled": False,
            "history_rewrite_enabled": False,
        }
        for override in invalid_fields:
            with self.subTest(override=override), self.assertRaises(SafeGitConfigError):
                SafeGitPolicy(**(defaults | override))

        policy_repr = repr(self.policy)
        self.assertNotIn(self.policy.expected_remote_url, policy_repr)
        self.assertNotIn(self.policy.public_name, policy_repr)
        self.assertNotIn(self.policy.public_email, policy_repr)

    def test_exact_top_level_and_detached_head_are_required(self) -> None:
        child = self.root / "child"
        child.mkdir()
        with self.assertRaises(SafeGitOperationError):
            preflight(child, self.policy)

        self._git(self.root, "checkout", "--detach")
        with self.assertRaisesRegex(SafeGitOperationError, "Detached"):
            preflight(self.root, self.policy)

    def test_write_rejects_linked_worktree_metadata_outside_writable_root(self) -> None:
        main = self.base / "outside-main"
        allowed = self.base / "authorized"
        linked = allowed / "linked"
        main.mkdir()
        allowed.mkdir()
        self._git(main, "init", "-b", "main")
        self._git(main, "config", "user.name", "Test")
        self._git(main, "config", "user.email", "test@example.invalid")
        (main / "initial.txt").write_text("initial\n", encoding="utf-8")
        self._git(main, "add", "initial.txt")
        self._git(main, "commit", "-m", "initial")
        self._git(main, "worktree", "add", "-b", "linked", str(linked), "HEAD")
        (linked / "linked.txt").write_text("linked\n", encoding="utf-8")

        with self.assertRaisesRegex(SafeGitOperationError, "metadata"):
            stage_paths(
                linked.resolve(),
                ["linked.txt"],
                self.policy,
                writable_roots=(allowed.resolve(),),
            )

        self.assertIn("?? linked.txt", self._git(linked, "status", "--short").stdout)

    def test_write_rejects_linked_or_separate_metadata_even_under_broad_writable_root(self) -> None:
        broad = self.base.resolve()
        linked = self.base / "linked-inside-broad-root"
        self._git(self.root, "worktree", "add", "-b", "linked-broad", str(linked), "HEAD")
        (linked / "linked.txt").write_text("linked\n", encoding="utf-8")

        with self.assertRaisesRegex(SafeGitOperationError, "linked or external"):
            stage_paths(
                linked.resolve(),
                ["linked.txt"],
                self.policy,
                writable_roots=(broad,),
            )

        separate_worktree = self.base / "separate-worktree"
        separate_metadata = self.base / "separate-metadata"
        separate_worktree.mkdir()
        self._git(
            separate_worktree,
            "init",
            "-b",
            "main",
            "--separate-git-dir",
            str(separate_metadata),
        )
        (separate_worktree / "new.txt").write_text("new\n", encoding="utf-8")
        with self.assertRaisesRegex(SafeGitOperationError, "linked or external"):
            stage_paths(
                separate_worktree.resolve(),
                ["new.txt"],
                self.policy,
                writable_roots=(broad,),
            )

    def test_repository_filter_and_credential_helper_commands_are_never_executed(self) -> None:
        marker = self.base / "process-marker"
        script = self.base / "should-not-run"
        script.write_text(f"#!/bin/sh\ntouch '{marker}'\ncat\n", encoding="utf-8")
        script.chmod(0o700)

        self._git(self.root, "config", "filter.hostile.clean", str(script))
        (self.root / ".gitattributes").write_text("filtered.txt filter=hostile\n", encoding="utf-8")
        (self.root / "filtered.txt").write_text("content\n", encoding="utf-8")
        with self.assertRaisesRegex(SafeGitOperationError, "filters are unsupported"):
            stage_paths(self.root, ["filtered.txt"], self.policy)
        self.assertFalse(marker.exists())

        self._git(self.root, "config", "--unset-all", "filter.hostile.clean")
        self._git(self.root, "config", "credential.helper", f"!{script}")
        with self.assertRaisesRegex(SafeGitOperationError, "process-launch"):
            preflight(self.root, self.policy)
        self.assertFalse(marker.exists())

        self._git(self.root, "config", "--unset-all", "credential.helper")
        self._git(
            self.root,
            "config",
            "url.https://example.invalid/rewrite/.insteadOf",
            str(self.remote),
        )
        with self.assertRaisesRegex(SafeGitOperationError, "URL rewrite"):
            preflight(self.root, self.policy)
        self.assertFalse(marker.exists())

    def test_repository_http_transport_settings_are_rejected_before_host_auth(self) -> None:
        github = replace(
            self.policy,
            expected_remote_url="https://github.com/example/arvis.git",
        )
        settings = (
            ("http.proxy", "http://127.0.0.1:8080"),
            ("http.sslVerify", "false"),
            ("http.curloptResolve", "+github.com:443:127.0.0.1"),
            ("http.https://github.com/.proxy", "http://127.0.0.1:8080"),
            ("http.https://github.com/.sslVerify", "false"),
            (
                "http.https://github.com/.curloptResolve",
                "+github.com:443:127.0.0.1",
            ),
            ("remote.origin.proxy", "http://127.0.0.1:8080"),
            ("remote.origin.proxyAuthMethod", "basic"),
        )
        for key, value in settings:
            with self.subTest(key=key):
                self._git(self.root, "config", key, value)
                try:
                    with (
                        mock.patch.object(
                            safe_git_control,
                            "_host_github_credential_helpers",
                        ) as helpers,
                        self.assertRaisesRegex(SafeGitOperationError, "HTTP transport"),
                    ):
                        preflight(self.root, github)
                    helpers.assert_not_called()
                finally:
                    self._git(self.root, "config", "--unset-all", key, check=False)

    def test_repository_included_http_transport_settings_are_rejected_before_host_auth(self) -> None:
        github = replace(
            self.policy,
            expected_remote_url="https://github.com/example/arvis.git",
        )
        included = self.base / "private-included-git-config"
        included.write_text(
            "[http]\n"
            "\tproxy = http://127.0.0.1:8080\n"
            "\tsslVerify = false\n"
            "\tcurloptResolve = +github.com:443:127.0.0.1\n",
            encoding="utf-8",
        )
        self._git(self.root, "config", "include.path", str(included))

        with (
            mock.patch.object(
                safe_git_control,
                "_host_github_credential_helpers",
            ) as helpers,
            self.assertRaisesRegex(SafeGitOperationError, "HTTP transport"),
        ):
            preflight(self.root, github)

        helpers.assert_not_called()

    def test_repository_hook_pager_editor_askpass_ssh_and_remote_trampolines_fail_closed(self) -> None:
        marker = self.base / "trampoline-marker"
        script = self.base / "trampoline"
        script.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
        script.chmod(0o700)
        settings = (
            ("core.hooksPath", str(script)),
            ("core.pager", str(script)),
            ("pager.status", str(script)),
            ("core.editor", str(script)),
            ("sequence.editor", str(script)),
            ("core.askPass", str(script)),
            ("core.sshCommand", str(script)),
            ("core.fsmonitor", str(script)),
            ("remote.origin.uploadpack", str(script)),
            ("remote.origin.receivepack", str(script)),
            ("remote.origin.vcs", str(script)),
        )
        for key, value in settings:
            with self.subTest(key=key):
                self._git(self.root, "config", key, value)
                try:
                    with self.assertRaisesRegex(SafeGitOperationError, "process-launch"):
                        preflight(self.root, self.policy)
                    self.assertFalse(marker.exists())
                finally:
                    self._git(self.root, "config", "--unset-all", key, check=False)

    def test_remote_policy_rejects_process_transports_and_embedded_credentials(self) -> None:
        for remote_url in (
            "ext::sh -c evil",
            "ssh://git@github.com/example/arvis.git",
            "git@github.com:example/arvis.git",
            "https://token@github.com/example/arvis.git",
            "https://github.com:bad/example/arvis.git",
            "https://github.com/example/arvis.git?credential=unexpected",
        ):
            with self.subTest(remote_url=remote_url), self.assertRaises(SafeGitConfigError):
                replace(self.policy, expected_remote_url=remote_url)

    def test_preflight_reports_bounded_safe_state(self) -> None:
        (self.root / "tracked.txt").write_text("modified\n", encoding="utf-8")
        (self.root / "new.txt").write_text("new\n", encoding="utf-8")
        stage_paths(self.root, ["new.txt"], self.policy)

        result = preflight(self.root, self.policy)

        self.assertEqual(result.branch, "main")
        self.assertEqual(result.remote_name, "origin")
        self.assertEqual(result.staged_paths, ("new.txt",))
        self.assertEqual(result.changed_count, 2)
        self.assertEqual(result.remote_state, "available")
        self.assertEqual(result.ahead_count, 0)

    def test_preflight_reports_remote_unavailable_without_exposing_url(self) -> None:
        unavailable = self.base / "unavailable-private-remote.git"
        self._git(self.root, "remote", "set-url", "origin", str(unavailable))
        policy = SafeGitPolicy(
            remote_name="origin",
            expected_remote_url=str(unavailable),
            public_name="Arvis Public",
            public_email="arvis-public@example.invalid",
            push_enabled=True,
            history_rewrite_enabled=True,
        )

        result = preflight(self.root, policy)

        self.assertEqual(result.remote_state, "unavailable")
        self.assertIsNone(result.remote_head)
        self.assertIsNone(result.ahead_count)
        self.assertNotIn(str(unavailable), repr(result))

    def test_remote_url_mismatch_is_rejected_without_leaking_values(self) -> None:
        wrong = SafeGitPolicy(
            remote_name="origin",
            expected_remote_url=str(self.base / "wrong-private.git"),
            public_name="Secret Name",
            public_email="secret@example.invalid",
            push_enabled=True,
            history_rewrite_enabled=True,
        )
        with self.assertRaises(SafeGitOperationError) as caught:
            preflight(self.root, wrong)
        error = str(caught.exception)
        self.assertNotIn(wrong.expected_remote_url, error)
        self.assertNotIn(wrong.public_name, error)
        self.assertNotIn(wrong.public_email, error)
        self.assertNotIn(str(self.root), error)

    def test_stage_modified_new_and_deleted_paths(self) -> None:
        (self.root / "tracked.txt").write_text("modified\n", encoding="utf-8")
        (self.root / "new.txt").write_text("new\n", encoding="utf-8")
        (self.root / "delete.txt").unlink()

        result = stage_paths(self.root, ["tracked.txt", "new.txt", "delete.txt"], self.policy)

        self.assertEqual(result.staged_count, 3)
        self.assertEqual(set(result.staged_paths), {"tracked.txt", "new.txt", "delete.txt"})
        staged = self._git(self.root, "diff", "--cached", "--name-only").stdout.splitlines()
        self.assertEqual(set(staged), {"tracked.txt", "new.txt", "delete.txt"})

    def test_stage_rejects_clean_unknown_absolute_directory_and_private_paths(self) -> None:
        directory = self.root / "folder"
        directory.mkdir()
        (directory / "changed.txt").write_text("new\n", encoding="utf-8")
        rejected = (
            "tracked.txt",
            "unknown.txt",
            str(self.root / "tracked.txt"),
            "folder",
            ".env",
            ".env.local",
            ".git/config",
            ".runtime/debug.txt",
            "credentials.json",
            "secrets.toml",
            "private.pem",
        )
        for path in rejected:
            with self.subTest(path=path), self.assertRaises(SafeGitOperationError):
                stage_paths(self.root, [path], self.policy)

        with self.assertRaises(SafeGitOperationError):
            stage_paths(self.root, [], self.policy)
        with self.assertRaises(SafeGitOperationError):
            stage_paths(self.root, ["folder/changed.txt"] * 101, self.policy)

    def test_literal_pathspec_does_not_expand_glob_characters(self) -> None:
        literal = self.root / "*.json"
        matching = self.root / "other.json"
        literal.write_text("literal\n", encoding="utf-8")
        matching.write_text("matching\n", encoding="utf-8")

        stage_paths(self.root, ["*.json"], self.policy)

        staged = self._git(self.root, "diff", "--cached", "--name-only").stdout.splitlines()
        self.assertEqual(staged, ["*.json"])
        self.assertIn("?? other.json", self._git(self.root, "status", "--short").stdout)

    def test_commit_is_staged_only_and_forces_trusted_identity(self) -> None:
        (self.root / "tracked.txt").write_text("unstaged remains\n", encoding="utf-8")
        (self.root / "staged.txt").write_text("staged\n", encoding="utf-8")
        stage_paths(self.root, ["staged.txt"], self.policy)

        result = commit_staged(self.root, "Public subject", self.policy)

        committed = self._git(self.root, "show", "--format=", "--name-only", result.sha).stdout.splitlines()
        self.assertEqual(committed, ["staged.txt"])
        self.assertIn(" M tracked.txt", self._git(self.root, "status", "--short").stdout)
        identity = self._git(
            self.root,
            "show",
            "-s",
            "--format=%an%n%ae%n%cn%n%ce%n%s",
            result.sha,
        ).stdout.splitlines()
        self.assertEqual(
            identity,
            [
                self.policy.public_name,
                self.policy.public_email,
                self.policy.public_name,
                self.policy.public_email,
                "Public subject",
            ],
        )

    def test_ambient_git_environment_is_stripped(self) -> None:
        (self.root / "safe.txt").write_text("safe\n", encoding="utf-8")
        hostile_env = {
            "GIT_DIR": str(self.base / "wrong-git-dir"),
            "GIT_INDEX_FILE": str(self.base / "wrong-index"),
            "GIT_AUTHOR_NAME": "Ambient Secret Author",
            "GIT_AUTHOR_EMAIL": "ambient-secret@example.test",
            "GIT_COMMITTER_NAME": "Ambient Secret Committer",
            "GIT_COMMITTER_EMAIL": "ambient-committer@example.test",
        }

        with mock.patch.dict(os.environ, hostile_env):
            stage_paths(self.root, ["safe.txt"], self.policy)
            result = commit_staged(self.root, "Controlled environment", self.policy)

        identity = self._git(
            self.root,
            "show",
            "-s",
            "--format=%an%n%ae%n%cn%n%ce",
            result.sha,
        ).stdout.splitlines()
        self.assertEqual(
            identity,
            [
                self.policy.public_name,
                self.policy.public_email,
                self.policy.public_name,
                self.policy.public_email,
            ],
        )

    def test_run_git_inherits_only_explicit_safe_environment(self) -> None:
        ambient = {
            "HOME": "/safe/home",
            "PATH": "/safe/bin",
            "LANG": "uk_UA.UTF-8",
            "LC_ALL": "host-locale",
            "TMPDIR": "/safe/tmp",
            "XDG_CONFIG_HOME": "/safe/config",
            "XDG_DATA_HOME": "/safe/data",
            "XDG_RUNTIME_DIR": "/safe/run",
            "SSH_AUTH_SOCK": "/safe/run/ssh-agent.sock",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/safe/run/dbus",
            "GIT_TERMINAL_PROMPT": "1",
            "GIT_NO_REPLACE_OBJECTS": "0",
            "GIT_OPTIONAL_LOCKS": "1",
            "GIT_DIR": "/hostile/git-dir",
            "GIT_INDEX_FILE": "/hostile/index",
            "OPENAI_API_KEY": "openai-secret",
            "GH_TOKEN": "gh-secret",
            "GITHUB_TOKEN": "github-secret",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "SECRET_VALUE": "random-secret",
        }
        process = mock.Mock(returncode=0)
        with (
            mock.patch.dict(os.environ, ambient, clear=True),
            mock.patch.object(safe_git_control.subprocess, "Popen", return_value=process) as popen,
            mock.patch.object(
                safe_git_control,
                "_collect_process_output",
                return_value=(b"", b"", False, False, False),
            ),
        ):
            safe_git_control._run_git(GIT, ["status"], self.root, self.policy)

        child_env = popen.call_args.kwargs["env"]
        for key in (
            "HOME",
            "PATH",
            "LANG",
            "TMPDIR",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS",
        ):
            with self.subTest(allowed_key=key):
                self.assertEqual(child_env[key], ambient[key])
        self.assertEqual(child_env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(child_env["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(child_env["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(child_env["GIT_PAGER"], "")
        self.assertEqual(child_env["PAGER"], "")
        self.assertEqual(child_env["GIT_EDITOR"], "/bin/false")
        self.assertEqual(child_env["GIT_SEQUENCE_EDITOR"], "/bin/false")
        self.assertEqual(child_env["GIT_MERGE_AUTOEDIT"], "no")
        self.assertEqual(child_env["GIT_ASKPASS"], "/bin/false")
        self.assertEqual(child_env["SSH_ASKPASS"], "/bin/false")
        self.assertEqual(child_env["SSH_ASKPASS_REQUIRE"], "never")
        self.assertEqual(child_env["GIT_SSH_COMMAND"], "/bin/false")
        self.assertEqual(child_env["GCM_INTERACTIVE"], "Never")
        self.assertEqual(child_env["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(child_env["LC_ALL"], "C")
        argv = popen.call_args.args[0]
        self.assertEqual(argv[1], "--no-pager")
        self.assertIn("core.hooksPath=/dev/null", argv)
        self.assertIn("core.fsmonitor=false", argv)
        self.assertIn("protocol.allow=never", argv)
        self.assertIn("protocol.https.allow=always", argv)
        self.assertIn("credential.helper=", argv)
        for key in (
            "GIT_DIR",
            "GIT_INDEX_FILE",
            "SSH_AUTH_SOCK",
            "OPENAI_API_KEY",
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "AWS_SECRET_ACCESS_KEY",
            "SECRET_VALUE",
        ):
            with self.subTest(stripped_key=key):
                self.assertNotIn(key, child_env)

    def test_only_pinned_standard_github_https_can_retain_host_credential_helpers(self) -> None:
        github = replace(self.policy, expected_remote_url="https://github.com/example/arvis.git")
        github_443 = replace(
            self.policy,
            expected_remote_url="https://github.com:443/example/arvis.git",
        )
        lookalike = replace(
            self.policy,
            expected_remote_url="https://github.com.evil.invalid/example/arvis.git",
        )
        other = replace(
            self.policy,
            expected_remote_url="https://git.example.invalid/example/arvis.git",
        )

        self.assertIn("credential.helper=", safe_git_control._safe_git_config_args(github))
        github_args = safe_git_control._safe_git_config_args(
            github,
            trusted_helpers=("!gh auth git-credential",),
        )
        self.assertIn("credential.helper=", github_args)
        self.assertIn("credential.helper=!gh auth git-credential", github_args)
        self.assertIn("credential.helper=", safe_git_control._safe_git_config_args(github_443))
        self.assertIn("credential.helper=", safe_git_control._safe_git_config_args(lookalike))
        self.assertIn("credential.helper=", safe_git_control._safe_git_config_args(other))
        with self.assertRaisesRegex(SafeGitOperationError, "not allowed"):
            safe_git_control._safe_git_config_args(
                other,
                trusted_helpers=("!gh auth git-credential",),
            )

    def test_github_remote_command_rebuilds_helpers_from_bounded_host_scopes(self) -> None:
        github = replace(self.policy, expected_remote_url="https://github.com/example/arvis.git")
        system_process = mock.Mock(returncode=1)
        global_process = mock.Mock(returncode=0)
        git_process = mock.Mock(returncode=0)
        host_output = (
            b"credential.helper\nstore\0"
            b"credential.https://github.com.helper\n\0"
            b"credential.https://github.com.helper\n!gh auth git-credential\0"
            b"credential.https://example.invalid.helper\n!ignored\0"
        )
        with (
            mock.patch.object(
                safe_git_control.subprocess,
                "Popen",
                side_effect=(system_process, global_process, git_process),
            ) as popen,
            mock.patch.object(
                safe_git_control,
                "_collect_process_output",
                side_effect=(
                    (b"", b"", False, False, False),
                    (host_output, b"", False, False, False),
                    (b"", b"", False, False, False),
                ),
            ),
        ):
            safe_git_control._run_git(
                GIT,
                ["ls-remote", github.expected_remote_url, "refs/heads/main"],
                self.root,
                github,
                allow_host_github_auth=True,
            )

        argv = popen.call_args_list[-1].args[0]
        self.assertIn("credential.helper=", argv)
        self.assertIn("credential.helper=store", argv)
        self.assertIn("credential.helper=!gh auth git-credential", argv)
        self.assertNotIn("credential.helper=!ignored", argv)
        self.assertLess(
            argv.index("credential.helper="),
            argv.index("credential.helper=!gh auth git-credential"),
        )

    def test_run_git_accepts_only_controlled_identity_and_date_overrides(self) -> None:
        controlled = {
            "GIT_AUTHOR_NAME": "Public Author",
            "GIT_AUTHOR_EMAIL": "author@example.invalid",
            "GIT_AUTHOR_DATE": "2026-08-31T12:00:00+00:00",
            "GIT_COMMITTER_NAME": "Public Committer",
            "GIT_COMMITTER_EMAIL": "committer@example.invalid",
            "GIT_COMMITTER_DATE": "2026-08-31T12:00:01+00:00",
        }
        process = mock.Mock(returncode=0)
        with (
            mock.patch.object(safe_git_control.subprocess, "Popen", return_value=process) as popen,
            mock.patch.object(
                safe_git_control,
                "_collect_process_output",
                return_value=(b"", b"", False, False, False),
            ),
        ):
            safe_git_control._run_git(
                GIT,
                ["status"],
                self.root,
                self.policy,
                controlled_env=controlled,
            )

        child_env = popen.call_args.kwargs["env"]
        for key, value in controlled.items():
            with self.subTest(controlled_key=key):
                self.assertEqual(child_env[key], value)

        with (
            mock.patch.object(safe_git_control.subprocess, "Popen") as popen,
            self.assertRaisesRegex(SafeGitOperationError, "not allowed"),
        ):
            safe_git_control._run_git(
                GIT,
                ["status"],
                self.root,
                self.policy,
                controlled_env={"GIT_DIR": "/hostile/git-dir"},
            )
        popen.assert_not_called()

    def test_commit_message_validation_and_no_staged_diff(self) -> None:
        for message in (
            "",
            "   ",
            "two\nlines",
            "two\u2028lines",
            "bad\x7fvalue",
            "x" * 161,
        ):
            with self.subTest(message=message), self.assertRaises(SafeGitOperationError):
                commit_staged(self.root, message, self.policy)
        with self.assertRaisesRegex(SafeGitOperationError, "no staged"):
            commit_staged(self.root, "Nothing staged", self.policy)

    def test_commit_rejects_externally_staged_private_path(self) -> None:
        private = self.root / ".env"
        private.write_text("TOKEN=private\n", encoding="utf-8")
        self._git(self.root, "add", "-f", ".env")

        with self.assertRaises(SafeGitOperationError):
            commit_staged(self.root, "Must reject", self.policy)

        self.assertEqual(self._git(self.root, "log", "-1", "--format=%s").stdout.strip(), "initial")

    def test_commit_hook_is_disabled(self) -> None:
        marker = self.base / "commit-hook-marker"
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
        hook.chmod(0o700)
        self._commit_via_core()
        self.assertFalse(marker.exists())

    def test_push_disabled(self) -> None:
        disabled = SafeGitPolicy(
            remote_name="origin",
            expected_remote_url=str(self.remote),
            public_name="Arvis Public",
            public_email="arvis-public@example.invalid",
            push_enabled=False,
            history_rewrite_enabled=True,
        )
        with self.assertRaisesRegex(SafeGitOperationError, "disabled"):
            push_current(self.root, disabled)

    def test_local_remote_push_fails_closed_but_up_to_date_noop_is_safe(self) -> None:
        noop = push_current(self.root, self.policy)
        remote_before = self._git(self.remote, "rev-parse", "refs/heads/main").stdout.strip()
        new_head = self._commit_via_core()
        self._git(self.root, "tag", "local-only-tag")

        with self.assertRaisesRegex(SafeGitOperationError, "HTTPS"):
            push_current(self.root, self.policy)

        remote_head = self._git(self.remote, "rev-parse", "refs/heads/main").stdout.strip()
        self.assertNotEqual(remote_head, new_head)
        self.assertEqual(remote_head, remote_before)
        self.assertFalse(noop.pushed)
        self.assertEqual(noop.old_remote_head, noop.new_head)
        self.assertNotEqual(
            self._git(self.remote, "show-ref", "--verify", "refs/tags/local-only-tag", check=False).returncode,
            0,
        )
        self.assertNotEqual(
            self._git(self.root, "rev-parse", "--verify", "@{upstream}", check=False).returncode,
            0,
        )

    def test_push_rechecks_remote_url(self) -> None:
        self._commit_via_core()
        wrong = SafeGitPolicy(
            remote_name="origin",
            expected_remote_url=str(self.base / "not-the-remote.git"),
            public_name="Arvis Public",
            public_email="arvis-public@example.invalid",
            push_enabled=True,
            history_rewrite_enabled=True,
        )
        with self.assertRaisesRegex(SafeGitOperationError, "does not match"):
            push_current(self.root, wrong)

    def test_https_push_uses_only_the_exact_pinned_url(self) -> None:
        policy = replace(
            self.policy,
            expected_remote_url="https://github.com/example/arvis.git",
        )
        remote_head = "a" * 40
        local_head = "b" * 40
        success = safe_git_control._GitResult(
            returncode=0,
            stdout="",
            stderr="",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            stdout_valid_utf8=True,
        )
        with (
            mock.patch.object(safe_git_control, "_prepare", return_value=(GIT, self.root)),
            mock.patch.object(safe_git_control, "_current_branch", return_value="main"),
            mock.patch.object(safe_git_control, "_head", return_value=local_head),
            mock.patch.object(safe_git_control, "_verify_remote_url") as verify,
            mock.patch.object(safe_git_control, "_remote_head", return_value=remote_head),
            mock.patch.object(safe_git_control, "_object_exists", return_value=True),
            mock.patch.object(safe_git_control, "_run_git", return_value=success) as run_git,
        ):
            result = push_current(self.root, policy)

        self.assertTrue(result.pushed)
        self.assertEqual(verify.call_count, 2)
        push_args = next(
            call.args[1]
            for call in run_git.call_args_list
            if call.args[1] and "push" in call.args[1]
        )
        self.assertIn(policy.expected_remote_url, push_args)
        self.assertNotIn(policy.remote_name, push_args)

    def test_diverged_remote_is_rejected(self) -> None:
        clone = self.base / "other"
        self._git(self.base, "clone", str(self.remote), str(clone))
        self._git(clone, "checkout", "main")
        self._git(clone, "config", "user.name", "Other")
        self._git(clone, "config", "user.email", "other@example.invalid")
        (clone / "remote-only.txt").write_text("remote\n", encoding="utf-8")
        self._git(clone, "add", "remote-only.txt")
        self._git(clone, "commit", "-m", "remote advances")
        self._git(clone, "push", "origin", "main")
        self._commit_via_core("local-only.txt", "local\n")
        self._git(self.root, "fetch", "origin", "main")

        with self.assertRaisesRegex(SafeGitOperationError, "diverged"):
            push_current(self.root, self.policy)

    def test_pre_push_hook_is_disabled(self) -> None:
        marker = self.base / "push-hook-marker"
        hook = self.root / ".git" / "hooks" / "pre-push"
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
        hook.chmod(0o700)
        remote_marker = self.base / "receive-hook-marker"
        receive_hook = self.remote / "hooks" / "pre-receive"
        receive_hook.write_text(
            f"#!/bin/sh\ntouch '{remote_marker}'\nexit 1\n",
            encoding="utf-8",
        )
        receive_hook.chmod(0o700)
        self._commit_via_core()

        with self.assertRaisesRegex(SafeGitOperationError, "HTTPS"):
            push_current(self.root, self.policy)

        self.assertFalse(marker.exists())
        self.assertFalse(remote_marker.exists())

    def test_rewrite_disabled_is_controlled_deny(self) -> None:
        self._make_linear_commits(1)
        old_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()

        with self.assertRaisesRegex(SafeGitOperationError, "disabled"):
            rewrite_unpushed_identity(
                self.root,
                replace(self.policy, history_rewrite_enabled=False),
            )

        self.assertEqual(self._git(self.root, "rev-parse", "HEAD").stdout.strip(), old_head)

    def test_rewrite_two_linear_commits_preserves_raw_metadata(self) -> None:
        self._assert_linear_rewrite(2)

    def test_rewrite_three_linear_commits_then_local_push_fails_closed(self) -> None:
        result = self._assert_linear_rewrite(3)
        remote_head = self._git(self.remote, "rev-parse", "refs/heads/main").stdout.strip()

        with self.assertRaisesRegex(SafeGitOperationError, "HTTPS"):
            push_current(self.root, self.policy)

        self.assertEqual(
            self._git(self.remote, "rev-parse", "refs/heads/main").stdout.strip(),
            remote_head,
        )
        self.assertNotEqual(remote_head, result.new_head)

    def test_rewrite_preserves_dirty_status_fingerprint_exactly(self) -> None:
        self._make_linear_commits(2)
        (self.root / "tracked.txt").write_text("unstaged dirty\n", encoding="utf-8")
        (self.root / "staged-dirty.txt").write_text("staged dirty\n", encoding="utf-8")
        (self.root / "untracked-dirty.txt").write_text("untracked dirty\n", encoding="utf-8")
        self._git(self.root, "add", "staged-dirty.txt")
        status_before = self._git_bytes(
            self.root,
            "status",
            "--porcelain=v1",
            "-z",
        ).stdout

        rewrite_unpushed_identity(self.root, self.policy)

        status_after = self._git_bytes(
            self.root,
            "status",
            "--porcelain=v1",
            "-z",
        ).stdout
        self.assertEqual(status_after, status_before)

    def test_rewrite_remote_at_head_is_nothing_to_rewrite(self) -> None:
        old_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()

        with self.assertRaisesRegex(SafeGitOperationError, "no unpushed"):
            rewrite_unpushed_identity(self.root, self.policy)

        self.assertEqual(self._git(self.root, "rev-parse", "HEAD").stdout.strip(), old_head)

    def test_rewrite_missing_remote_branch_is_rejected(self) -> None:
        self._make_linear_commits(1)
        old_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()
        self._git(self.remote, "update-ref", "-d", "refs/heads/main")

        with self.assertRaisesRegex(SafeGitOperationError, "does not exist"):
            rewrite_unpushed_identity(self.root, self.policy)

        self.assertEqual(self._git(self.root, "rev-parse", "HEAD").stdout.strip(), old_head)

    def test_rewrite_unavailable_remote_is_rejected(self) -> None:
        self._make_linear_commits(1)
        old_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()
        unavailable = self.base / "unavailable-rewrite-remote.git"
        self._git(self.root, "remote", "set-url", "origin", str(unavailable))
        policy = replace(self.policy, expected_remote_url=str(unavailable))

        with self.assertRaisesRegex(SafeGitOperationError, "unavailable"):
            rewrite_unpushed_identity(self.root, policy)

        self.assertEqual(self._git(self.root, "rev-parse", "HEAD").stdout.strip(), old_head)

    def test_rewrite_remote_head_not_local_is_rejected(self) -> None:
        self._make_linear_commits(1)
        old_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()
        self._advance_remote_without_fetch()

        with self.assertRaisesRegex(SafeGitOperationError, "not available"):
            rewrite_unpushed_identity(self.root, self.policy)

        self.assertEqual(self._git(self.root, "rev-parse", "HEAD").stdout.strip(), old_head)

    def test_rewrite_diverged_remote_is_rejected(self) -> None:
        self._make_linear_commits(1)
        old_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()
        self._advance_remote_without_fetch()
        self._git(self.root, "fetch", "origin", "main")

        with self.assertRaisesRegex(SafeGitOperationError, "diverged"):
            rewrite_unpushed_identity(self.root, self.policy)

        self.assertEqual(self._git(self.root, "rev-parse", "HEAD").stdout.strip(), old_head)

    def test_rewrite_merge_commit_is_rejected_without_ref_change(self) -> None:
        self._git(self.root, "checkout", "-b", "feature")
        (self.root / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git(self.root, "add", "feature.txt")
        self._git(self.root, "commit", "-m", "feature")
        self._git(self.root, "checkout", "main")
        (self.root / "main.txt").write_text("main\n", encoding="utf-8")
        self._git(self.root, "add", "main.txt")
        self._git(self.root, "commit", "-m", "main")
        self._git(self.root, "merge", "--no-ff", "feature", "-m", "merge")
        old_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()

        with self.assertRaisesRegex(SafeGitOperationError, "unsupported|linear"):
            rewrite_unpushed_identity(self.root, self.policy)

        self.assertEqual(self._git(self.root, "rev-parse", "HEAD").stdout.strip(), old_head)

    def test_rewrite_nonstandard_and_signed_headers_are_rejected(self) -> None:
        remote_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()
        tree = self._git(self.root, "rev-parse", "HEAD^{tree}").stdout.strip().encode("ascii")
        base_headers = (
            b"tree "
            + tree
            + b"\nparent "
            + remote_head.encode("ascii")
            + b"\nauthor Old Private Identity <old-private@example.test> 1700000000 +0000\n"
            + b"committer Old Private Identity <old-private@example.test> 1700000001 +0000\n"
        )
        variants = (
            base_headers + b"encoding UTF-8\n\nmessage\n",
            base_headers
            + b"gpgsig -----BEGIN PGP SIGNATURE-----\n signed continuation\n"
            + b"\nmessage\n",
        )
        for raw in variants:
            with self.subTest(header=raw.splitlines()[-2]):
                synthetic_head = self._write_synthetic_commit(raw)
                with self.assertRaisesRegex(SafeGitOperationError, "unsupported"):
                    rewrite_unpushed_identity(self.root, self.policy)
                self.assertEqual(
                    self._git(self.root, "rev-parse", "HEAD").stdout.strip(),
                    synthetic_head,
                )
                self._git(
                    self.root,
                    "update-ref",
                    "refs/heads/main",
                    remote_head,
                    synthetic_head,
                )

    def test_rewrite_malformed_identity_header_is_rejected(self) -> None:
        remote_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()
        tree = self._git(self.root, "rev-parse", "HEAD^{tree}").stdout.strip()
        raw = (
            f"tree {tree}\n"
            f"parent {remote_head}\n"
            "author Old Private Identity <old-private@example.test> not-a-time +0000\n"
            "committer Old Private Identity <old-private@example.test> 1700000001 +0000\n"
            "\nmessage\n"
        ).encode("utf-8")
        synthetic_head = self._write_synthetic_commit(raw)

        with self.assertRaisesRegex(SafeGitOperationError, "malformed"):
            rewrite_unpushed_identity(self.root, self.policy)

        self.assertEqual(self._git(self.root, "rev-parse", "HEAD").stdout.strip(), synthetic_head)

    def test_rewrite_remote_change_before_update_leaves_branch_unchanged(self) -> None:
        self._make_linear_commits(2)
        old_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()
        remote_head = self._git(self.remote, "rev-parse", "refs/heads/main").stdout.strip()
        changed_remote = "f" * len(remote_head)

        with (
            mock.patch.object(
                safe_git_control,
                "_remote_head",
                side_effect=[remote_head, changed_remote],
            ),
            mock.patch.object(
                safe_git_control,
                "_run_git",
                wraps=safe_git_control._run_git,
            ) as run_git,
            self.assertRaisesRegex(SafeGitOperationError, "Remote branch changed"),
        ):
            rewrite_unpushed_identity(self.root, self.policy)

        self.assertEqual(self._git(self.root, "rev-parse", "HEAD").stdout.strip(), old_head)
        update_calls = [
            call
            for call in run_git.call_args_list
            if len(call.args) > 1 and call.args[1] and call.args[1][0] == "update-ref"
        ]
        self.assertEqual(update_calls, [])

    def test_rewrite_constructs_only_current_branch_update_ref(self) -> None:
        self._make_linear_commits(1)
        with mock.patch.object(
            safe_git_control,
            "_run_git",
            wraps=safe_git_control._run_git,
        ) as run_git:
            result = rewrite_unpushed_identity(self.root, self.policy)

        commands = [call.args[1] for call in run_git.call_args_list if len(call.args) > 1]
        update_commands = [command for command in commands if command and command[0] == "update-ref"]
        self.assertEqual(
            update_commands,
            [["update-ref", "refs/heads/main", result.new_head, result.old_head]],
        )
        forbidden_commands = {"push", "reset", "checkout", "rebase", "filter-branch"}
        self.assertFalse(
            any(command and command[0] in forbidden_commands for command in commands)
        )

    def test_rewrite_more_than_100_commits_is_rejected(self) -> None:
        parent = self._git(self.root, "rev-parse", "HEAD").stdout.strip()
        tree = self._git(self.root, "rev-parse", "HEAD^{tree}").stdout.strip()
        for index in range(101):
            parent = self._direct_commit(
                tree,
                parent,
                f"commit {index}\n".encode("ascii"),
                author_date="1700000000 +0000",
                committer_date=f"170000{index + 1000} +0000",
            )
        remote_head = self._git(self.root, "rev-parse", "HEAD").stdout.strip()
        self._git(self.root, "update-ref", "refs/heads/main", parent, remote_head)

        with self.assertRaisesRegex(SafeGitOperationError, "More than 100"):
            rewrite_unpushed_identity(self.root, self.policy)

        self.assertEqual(self._git(self.root, "rev-parse", "HEAD").stdout.strip(), parent)

    def test_rewrite_result_and_errors_are_identity_and_path_redacted(self) -> None:
        result = self._assert_linear_rewrite(1)
        self.assertEqual(
            tuple(field.name for field in fields(result)),
            ("rewritten_count", "old_head", "new_head", "branch", "remote_name"),
        )
        rendered = repr(result)
        for secret in (
            "Old Private Identity",
            "old-private@example.test",
            "Old Private Committer",
            "old-private-committer@example.test",
            self.policy.public_name,
            self.policy.public_email,
            self.policy.expected_remote_url,
            str(self.root),
        ):
            self.assertNotIn(secret, rendered)

        with self.assertRaises(SafeGitOperationError) as caught:
            rewrite_unpushed_identity(
                self.root,
                replace(self.policy, history_rewrite_enabled=False),
            )
        error = str(caught.exception)
        for secret in (
            "Old Private Identity",
            "old-private@example.test",
            "Old Private Committer",
            "old-private-committer@example.test",
            self.policy.public_name,
            self.policy.public_email,
            self.policy.expected_remote_url,
            str(self.root),
        ):
            self.assertNotIn(secret, error)

    def test_prepare_resolves_and_verifies_git_executable(self) -> None:
        executable = self.base / "verified-git"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
        linked = self.base / "linked-git"
        linked.symlink_to(executable)
        top_result = safe_git_control._GitResult(
            returncode=0,
            stdout=f"{self.root}\n",
            stderr="",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            stdout_valid_utf8=True,
        )
        with (
            mock.patch.object(safe_git_control.shutil, "which", return_value=str(linked)),
            mock.patch.object(safe_git_control, "_run_git", return_value=top_result),
            mock.patch.object(
                safe_git_control,
                "_resolved_git_metadata_path",
                side_effect=[self.root / ".git", self.root / ".git"],
            ),
            mock.patch.object(safe_git_control, "_reject_process_trampolines"),
        ):
            resolved_git, resolved_root = safe_git_control._prepare(self.root, self.policy)
        self.assertEqual(resolved_git, str(executable.resolve()))
        self.assertEqual(resolved_root, self.root)

        with (
            mock.patch.object(safe_git_control.shutil, "which", return_value=str(self.base)),
            self.assertRaisesRegex(SafeGitConfigError, "could not be verified"),
        ):
            safe_git_control._prepare(self.root, self.policy)

    def test_result_reprs_do_not_expose_policy_or_absolute_root(self) -> None:
        push_result = push_current(self.root, self.policy)
        (self.root / "safe.txt").write_text("safe\n", encoding="utf-8")
        stage_result = stage_paths(self.root, ["safe.txt"], self.policy)
        commit_result = commit_staged(self.root, "Safe result", self.policy)
        results = (preflight(self.root, self.policy), stage_result, commit_result, push_result)

        for result in results:
            rendered = repr(result)
            with self.subTest(result=type(result).__name__):
                self.assertNotIn(self.policy.expected_remote_url, rendered)
                self.assertNotIn(self.policy.public_name, rendered)
                self.assertNotIn(self.policy.public_email, rendered)
                self.assertNotIn(str(self.root), rendered)

    def test_commit_result_redacts_identity_if_subject_contains_it(self) -> None:
        (self.root / "safe.txt").write_text("safe\n", encoding="utf-8")
        stage_paths(self.root, ["safe.txt"], self.policy)

        result = commit_staged(self.root, self.policy.public_email, self.policy)

        self.assertNotIn(self.policy.public_email, repr(result))

    def test_core_has_no_shell_true_and_operation_api_is_narrow(self) -> None:
        source = (Path(__file__).parents[1] / "safe_git_control.py").read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertIn("shell=False", source)
        for forbidden in ("force", "--tags", "--set-upstream", "--amend", "git add .", "git add -A"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertEqual(
            tuple(inspect.signature(stage_paths).parameters),
            ("root", "paths", "policy", "writable_roots"),
        )
        self.assertEqual(
            tuple(inspect.signature(commit_staged).parameters),
            ("root", "message", "policy", "writable_roots"),
        )
        self.assertEqual(
            tuple(inspect.signature(push_current).parameters),
            ("root", "policy", "writable_roots"),
        )
        self.assertEqual(
            tuple(inspect.signature(rewrite_unpushed_identity).parameters),
            ("root", "policy", "writable_roots"),
        )


if __name__ == "__main__":
    unittest.main()
