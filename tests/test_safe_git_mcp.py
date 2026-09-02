from __future__ import annotations

import asyncio
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp_access import load_mcp_access_config
from project_context import ProjectContextError
from safe_git_control import (
    SafeGitCommitResult,
    SafeGitOperationError,
    SafeGitPreflight,
    SafeGitPushResult,
    SafeGitRewriteResult,
    SafeGitStageResult,
)
from safe_git_mcp import SafeGitIntegrationError, load_safe_git_controller


POLICY_VALUES = {
    "ARVIS_SAFE_GIT_REMOTE_NAME": "trusted-upstream",
    "ARVIS_SAFE_GIT_EXPECTED_REMOTE_URL": "https://git.example.invalid/team/project.git",
    "ARVIS_SAFE_GIT_PUBLIC_NAME": "Public Contributor",
    "ARVIS_SAFE_GIT_PUBLIC_EMAIL": "contributor@example.invalid",
}


class SafeGitControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.allowed = self.base / "allowed"
        self.writable = self.allowed / "writable"
        self.read_only = self.allowed / "read-only"
        self.denied = self.base / "denied"
        for path in (self.writable, self.read_only, self.denied):
            path.mkdir(parents=True)
        self.access = load_mcp_access_config(
            environ={
                "ARVIS_MCP_PROFILE": "chatgpt",
                "ARVIS_MCP_PROJECT_ROOT": str(self.writable),
                "ARVIS_MCP_ALLOWED_ROOTS": str(self.allowed),
                "ARVIS_MCP_WRITABLE_ROOTS": str(self.writable),
            },
            cwd=self.base,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _environment(
        self,
        *,
        enabled: str = "true",
        push: str = "false",
        rewrite: str = "false",
    ) -> dict[str, str]:
        return {
            "ARVIS_SAFE_GIT_CONTROL_ENABLED": enabled,
            "ARVIS_SAFE_GIT_PUSH_ENABLED": push,
            "ARVIS_SAFE_GIT_HISTORY_REWRITE_ENABLED": rewrite,
            **POLICY_VALUES,
        }

    def test_disabled_control_ignores_dormant_policy_and_flags(self) -> None:
        controller = load_safe_git_controller(
            environ=self._environment(enabled="false", push="true", rewrite="true")
        )

        self.assertFalse(controller.enabled)
        self.assertFalse(controller.available)
        self.assertFalse(controller.push_enabled)
        self.assertFalse(controller.history_rewrite_enabled)

    def test_missing_or_invalid_policy_fails_closed_without_leaking_values(self) -> None:
        cases = (
            {"ARVIS_SAFE_GIT_CONTROL_ENABLED": "true"},
            self._environment(push="yes"),
            {**self._environment(), "ARVIS_SAFE_GIT_PUBLIC_EMAIL": "not-an-email"},
        )
        for environment in cases:
            with self.subTest(keys=sorted(environment)):
                controller = load_safe_git_controller(environ=environment)
                self.assertTrue(controller.enabled)
                self.assertFalse(controller.available)
                self.assertFalse(controller.push_enabled)
                self.assertFalse(controller.history_rewrite_enabled)
                with self.assertRaises(SafeGitIntegrationError) as caught:
                    controller.preflight(None, access_config=self.access)
                rendered = repr(controller) + str(caught.exception)
                for value in environment.values():
                    if value not in {"true", "false", "yes"}:
                        self.assertNotIn(value, rendered)

    def test_effective_availability_requires_every_validated_writable_root(self) -> None:
        controller = load_safe_git_controller(environ=self._environment())
        self.assertTrue(controller.available_for(self.access))

        invalid_access = type(self.access)(
            profile=self.access.profile,
            allowed_roots=self.access.allowed_roots,
            writable_roots=(self.writable.resolve(), self.base / "missing"),
            default_root=self.access.default_root,
        )
        self.assertFalse(controller.available_for(invalid_access))

    def test_push_availability_requires_supported_https_remote(self) -> None:
        https_controller = load_safe_git_controller(
            environ=self._environment(push="true")
        )
        local_environment = self._environment(push="true")
        local_environment["ARVIS_SAFE_GIT_EXPECTED_REMOTE_URL"] = str(self.base / "remote.git")
        local_controller = load_safe_git_controller(environ=local_environment)
        file_environment = self._environment(push="true")
        file_environment["ARVIS_SAFE_GIT_EXPECTED_REMOTE_URL"] = (
            self.base / "remote.git"
        ).as_uri()
        file_controller = load_safe_git_controller(environ=file_environment)

        self.assertTrue(https_controller.push_available)
        self.assertFalse(local_controller.push_available)
        self.assertFalse(file_controller.push_available)

    def test_preflight_uses_read_root_but_write_operations_require_writable_root(self) -> None:
        controller = load_safe_git_controller(environ=self._environment())
        preflight_result = SafeGitPreflight(
            branch="main",
            head="a" * 40,
            remote_name=POLICY_VALUES["ARVIS_SAFE_GIT_REMOTE_NAME"],
            staged_paths=("safe.txt",),
            changed_count=2,
            push_enabled=False,
            remote_state="available",
            remote_head="b" * 40,
            ahead_count=1,
        )
        with mock.patch("safe_git_mcp.safe_git_control.preflight", return_value=preflight_result) as call:
            result = controller.preflight(str(self.read_only), access_config=self.access)
        self.assertEqual(call.call_args.args[0], self.read_only.resolve())
        self.assertEqual(result["staged_paths"], ["safe.txt"])
        self.assertNotIn("remote_name", result)
        self.assertNotIn("push_enabled", result)

        with mock.patch("safe_git_mcp.safe_git_control.stage_paths") as stage_call:
            with self.assertRaises(ProjectContextError):
                controller.stage_paths(["safe.txt"], str(self.read_only), access_config=self.access)
        stage_call.assert_not_called()

        with self.assertRaises(ProjectContextError):
            controller.preflight(str(self.denied), access_config=self.access)

    def test_operations_map_only_minimal_parameters_and_redacted_results(self) -> None:
        controller = load_safe_git_controller(
            environ=self._environment(push="true", rewrite="true")
        )
        stage_result = SafeGitStageResult(("one.txt",), 1)
        commit_result = SafeGitCommitResult("c" * 40, "Bounded subject", ("one.txt",))
        push_result = SafeGitPushResult(
            POLICY_VALUES["ARVIS_SAFE_GIT_REMOTE_NAME"],
            "main",
            "d" * 40,
            "e" * 40,
            True,
        )
        rewrite_result = SafeGitRewriteResult(
            2,
            "e" * 40,
            "f" * 40,
            "main",
            POLICY_VALUES["ARVIS_SAFE_GIT_REMOTE_NAME"],
        )
        with (
            mock.patch("safe_git_mcp.safe_git_control.stage_paths", return_value=stage_result) as stage,
            mock.patch("safe_git_mcp.safe_git_control.commit_staged", return_value=commit_result) as commit,
            mock.patch("safe_git_mcp.safe_git_control.push_current", return_value=push_result) as push,
            mock.patch(
                "safe_git_mcp.safe_git_control.rewrite_unpushed_identity",
                return_value=rewrite_result,
            ) as rewrite,
        ):
            staged = controller.stage_paths(["one.txt"], None, access_config=self.access)
            committed = controller.commit_staged("Bounded subject", None, access_config=self.access)
            pushed = controller.push_current(None, access_config=self.access)
            rewritten = controller.rewrite_unpushed_identity(None, access_config=self.access)

        policy = stage.call_args.args[2]
        self.assertEqual(stage.call_args.args, (self.writable.resolve(), ["one.txt"], policy))
        self.assertEqual(commit.call_args.args, (self.writable.resolve(), "Bounded subject", policy))
        self.assertEqual(push.call_args.args, (self.writable.resolve(), policy))
        self.assertEqual(rewrite.call_args.args, (self.writable.resolve(), policy))
        for call in (stage, commit, push, rewrite):
            self.assertEqual(
                call.call_args.kwargs,
                {"writable_roots": self.access.writable_roots},
            )
        self.assertEqual(staged, {"staged_paths": ["one.txt"], "staged_count": 1})
        self.assertEqual(committed["subject"], "Bounded subject")
        self.assertEqual(pushed["branch"], "main")
        self.assertEqual(rewritten["rewritten_count"], 2)
        rendered = repr((controller, staged, committed, pushed, rewritten))
        for value in POLICY_VALUES.values():
            self.assertNotIn(value, rendered)
        self.assertNotIn(str(self.writable), rendered)

    def test_engine_error_is_scrubbed_again_at_integration_boundary(self) -> None:
        environment = self._environment()
        environment["ARVIS_SAFE_GIT_REMOTE_NAME"] = "git"
        controller = load_safe_git_controller(environ=environment)
        outside_paths = (
            "/srv/private/git/includes/transport.conf",
            "/opt/private credential helpers/github-helper",
            r"C:\Users\Private\git-helper.exe",
        )
        private_values = (
            str(self.writable),
            environment["ARVIS_SAFE_GIT_REMOTE_NAME"],
            environment["ARVIS_SAFE_GIT_EXPECTED_REMOTE_URL"],
            environment["ARVIS_SAFE_GIT_PUBLIC_NAME"],
            environment["ARVIS_SAFE_GIT_PUBLIC_EMAIL"],
            *outside_paths,
        )
        private_error = (
            " ".join(private_values[:-3])
            + f" file {outside_paths[0]} helper '{outside_paths[1]}' windows {outside_paths[2]}"
        )
        with mock.patch(
            "safe_git_mcp.safe_git_control.preflight",
            side_effect=SafeGitOperationError(private_error),
        ):
            with self.assertRaises(SafeGitIntegrationError) as caught:
                controller.preflight(None, access_config=self.access)

        for value in private_values:
            self.assertNotIn(value, str(caught.exception))
        self.assertIn("<LOCAL_PATH>", str(caught.exception))

    def test_engine_error_conservatively_redacts_all_absolute_path_forms(self) -> None:
        controller = load_safe_git_controller(environ=self._environment())
        cases = (
            "POSIX /var/lib/private repo/git/config remains unreadable",
            "POSIX adjacent fatal:/var/lib/private repo/git/config remains unreadable",
            "POSIX quoted '/opt/private helpers/git/config' remains unreadable",
            "network //server/share/private repo/git/config remains unreadable",
            "URI file:///var/lib/private%20repo/git/config remains unreadable",
            r"drive C:\Users\Private User\repo\git-config remains unreadable",
            r'drive quoted "D:/Private User/repo/git-config" remains unreadable',
            r"UNC \\server\share\Private User\repo\git-config remains unreadable",
            r"UNC quoted '\\server\share\Private User\repo\git-config' remains unreadable",
        )

        for private_error in cases:
            with self.subTest(private_error=private_error):
                with mock.patch(
                    "safe_git_mcp.safe_git_control.preflight",
                    side_effect=SafeGitOperationError(private_error),
                ):
                    with self.assertRaises(SafeGitIntegrationError) as caught:
                        controller.preflight(None, access_config=self.access)

                self.assertEqual(
                    str(caught.exception),
                    "Safe Git operation was rejected: <LOCAL_PATH>.",
                )


@unittest.skipUnless(importlib.util.find_spec("mcp"), "mcp SDK is not installed")
class SafeGitRegistrationTests(unittest.TestCase):
    GIT_TOOLS = {
        "safe_git_preflight",
        "safe_git_stage_paths",
        "safe_git_commit_staged",
        "safe_git_push_current",
        "safe_git_rewrite_unpushed_identity",
    }

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _environment(
        self,
        *,
        enabled: str = "true",
        push: str = "false",
        rewrite: str = "false",
    ) -> dict[str, str]:
        return {
            "ARVIS_MCP_PROFILE": "chatgpt",
            "ARVIS_MCP_PROJECT_ROOT": str(self.root),
            "ARVIS_MCP_ALLOWED_ROOTS": str(self.root),
            "ARVIS_MCP_WRITABLE_ROOTS": str(self.root),
            "ARVIS_CODEX_AGENT_CONTROL_ENABLED": "false",
            "ARVIS_SAFE_COMMAND_CONTROL_ENABLED": "false",
            "ARVIS_SAFE_GIT_CONTROL_ENABLED": enabled,
            "ARVIS_SAFE_GIT_PUSH_ENABLED": push,
            "ARVIS_SAFE_GIT_HISTORY_REWRITE_ENABLED": rewrite,
            **POLICY_VALUES,
            "PATH": os.environ.get("PATH", ""),
        }

    def _load_server(self, environment: dict[str, str]):
        with mock.patch.dict(os.environ, environment, clear=True):
            import arvis_mcp_server

            return importlib.reload(arvis_mcp_server)

    def test_master_and_independent_opt_ins_gate_registration(self) -> None:
        cases = (
            ("false", "true", "true", set()),
            ("true", "false", "false", set(self.GIT_TOOLS) - {"safe_git_push_current", "safe_git_rewrite_unpushed_identity"}),
            ("true", "true", "false", set(self.GIT_TOOLS) - {"safe_git_rewrite_unpushed_identity"}),
            ("true", "true", "true", set(self.GIT_TOOLS)),
        )
        for enabled, push, rewrite, expected in cases:
            with self.subTest(enabled=enabled, push=push, rewrite=rewrite):
                module = self._load_server(
                    self._environment(enabled=enabled, push=push, rewrite=rewrite)
                )
                tools = {tool.name for tool in asyncio.run(module.mcp.list_tools())}
                self.assertEqual(tools & self.GIT_TOOLS, expected)

    def test_invalid_policy_registers_no_safe_git_tools(self) -> None:
        environment = self._environment(push="true", rewrite="true")
        environment.pop("ARVIS_SAFE_GIT_PUBLIC_EMAIL")
        module = self._load_server(environment)
        tools = {tool.name for tool in asyncio.run(module.mcp.list_tools())}

        self.assertEqual(tools & self.GIT_TOOLS, set())

    def test_local_or_file_remote_never_advertises_push(self) -> None:
        for remote_url in (str(self.root / "remote.git"), (self.root / "remote.git").as_uri()):
            with self.subTest(remote_url=remote_url):
                environment = self._environment(push="true", rewrite="true")
                environment["ARVIS_SAFE_GIT_EXPECTED_REMOTE_URL"] = remote_url
                module = self._load_server(environment)
                tools = {tool.name for tool in asyncio.run(module.mcp.list_tools())}

                self.assertNotIn("safe_git_push_current", tools)
                self.assertTrue(
                    {
                        "safe_git_preflight",
                        "safe_git_stage_paths",
                        "safe_git_commit_staged",
                        "safe_git_rewrite_unpushed_identity",
                    }.issubset(tools)
                )

    def test_invalid_access_or_missing_writable_roots_registers_no_safe_git_tools(self) -> None:
        cases = (
            {"ARVIS_MCP_WRITABLE_ROOTS": ""},
            {"ARVIS_MCP_WRITABLE_ROOTS": os.pathsep},
            {"ARVIS_MCP_WRITABLE_ROOTS": str(self.root / "missing")},
            {
                "ARVIS_MCP_WRITABLE_ROOTS": (
                    f"{self.root}{os.pathsep}{self.root / 'missing'}"
                )
            },
            {"ARVIS_MCP_PROFILE": "chatgpt", "ARVIS_MCP_WRITABLE_ROOTS": None},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                environment = self._environment(push="true", rewrite="true")
                for key, value in overrides.items():
                    if value is None:
                        environment.pop(key, None)
                    else:
                        environment[key] = value
                module = self._load_server(environment)
                tools = {tool.name for tool in asyncio.run(module.mcp.list_tools())}

                self.assertEqual(tools & self.GIT_TOOLS, set())

    def test_schemas_and_annotations_are_operation_specific(self) -> None:
        module = self._load_server(self._environment(push="true", rewrite="true"))
        tools = {tool.name: tool for tool in asyncio.run(module.mcp.list_tools())}

        expected_properties = {
            "safe_git_preflight": {"project_root"},
            "safe_git_stage_paths": {"paths", "project_root"},
            "safe_git_commit_staged": {"subject", "project_root"},
            "safe_git_push_current": {"project_root"},
            "safe_git_rewrite_unpushed_identity": {"project_root"},
        }
        for name, properties in expected_properties.items():
            self.assertEqual(set(tools[name].inputSchema["properties"]), properties)
        self.assertEqual(tools["safe_git_stage_paths"].inputSchema["required"], ["paths"])
        paths_schema = tools["safe_git_stage_paths"].inputSchema["properties"]["paths"]
        self.assertEqual(paths_schema["type"], "array")
        self.assertEqual(paths_schema["minItems"], 1)
        self.assertEqual(paths_schema["maxItems"], 100)
        self.assertEqual(paths_schema["items"]["minLength"], 1)
        self.assertEqual(paths_schema["items"]["maxLength"], 4096)
        self.assertEqual(tools["safe_git_commit_staged"].inputSchema["required"], ["subject"])
        subject_schema = tools["safe_git_commit_staged"].inputSchema["properties"]["subject"]
        self.assertEqual(subject_schema["minLength"], 1)
        self.assertEqual(subject_schema["maxLength"], 160)

        annotations = {
            name: (
                tool.annotations.readOnlyHint,
                tool.annotations.destructiveHint,
                tool.annotations.idempotentHint,
                tool.annotations.openWorldHint,
            )
            for name, tool in tools.items()
            if name in self.GIT_TOOLS
        }
        self.assertEqual(annotations["safe_git_preflight"], (True, False, True, True))
        self.assertEqual(annotations["safe_git_stage_paths"], (False, True, True, False))
        self.assertEqual(annotations["safe_git_commit_staged"], (False, True, False, False))
        self.assertEqual(annotations["safe_git_push_current"], (False, True, True, True))
        self.assertEqual(
            annotations["safe_git_rewrite_unpushed_identity"],
            (False, True, False, True),
        )

        schemas = repr({name: tools[name].inputSchema for name in self.GIT_TOOLS})
        for forbidden in (
            "executable",
            "remote_name",
            "remote_url",
            "public_name",
            "public_email",
            "branch",
            "refspec",
            "enable",
            "git_args",
            "shell",
            "force",
        ):
            self.assertNotIn(forbidden, schemas.casefold())

    def test_server_wrappers_map_only_schema_parameters_to_controller(self) -> None:
        module = self._load_server(self._environment(push="true", rewrite="true"))
        fake = mock.Mock()
        fake.preflight.return_value = {"branch": "main"}
        fake.stage_paths.return_value = {"staged_count": 1}
        fake.commit_staged.return_value = {"sha": "a" * 40}
        fake.push_current.return_value = {"pushed": True}
        fake.rewrite_unpushed_identity.return_value = {"rewritten_count": 1}
        module.SAFE_GIT_CONTROLLER = fake

        self.assertTrue(module.safe_git_preflight(str(self.root))["ok"])
        self.assertTrue(module.safe_git_stage_paths(["one.txt"], str(self.root))["ok"])
        self.assertTrue(module.safe_git_commit_staged("Subject", str(self.root))["ok"])
        self.assertTrue(module.safe_git_push_current(str(self.root))["ok"])
        self.assertTrue(module.safe_git_rewrite_unpushed_identity(str(self.root))["ok"])

        fake.preflight.assert_called_once_with(
            project_root=str(self.root), access_config=module.ACCESS_CONFIG
        )
        fake.stage_paths.assert_called_once_with(
            paths=["one.txt"], project_root=str(self.root), access_config=module.ACCESS_CONFIG
        )
        fake.commit_staged.assert_called_once_with(
            subject="Subject", project_root=str(self.root), access_config=module.ACCESS_CONFIG
        )
        fake.push_current.assert_called_once_with(
            project_root=str(self.root), access_config=module.ACCESS_CONFIG
        )
        fake.rewrite_unpushed_identity.assert_called_once_with(
            project_root=str(self.root), access_config=module.ACCESS_CONFIG
        )


if __name__ == "__main__":
    unittest.main()
