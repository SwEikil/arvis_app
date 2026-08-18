from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from mcp_access import McpAccessConfig
from mcp_security import redact_sensitive_lines, redact_sensitive_text
from project_context import (
    ProjectContextError,
    require_writable_project_root,
    resolve_project_root,
    safe_project_path,
)


PROJECT_EXTENSIONS = {".sln", ".csproj", ".fsproj", ".vbproj"}
CONFIGURATIONS = {"Debug", "Release"}
MAX_COMMAND_OUTPUT = 50_000
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5_000
MAX_GIT_LIST_ITEMS = 5_000
MAX_GIT_COMMITS = 500
MAX_GIT_CAPTURE_CHARS = 2_000_000


def project_state(project_root: str | None, *, access_config: McpAccessConfig) -> dict[str, Any]:
    root = resolve_project_root(project_root, access_config=access_config)
    result = _run_fixed(["git", "status", "--porcelain=v1", "--branch"], root, 10)
    lines = result["stdout"].splitlines()
    branch = lines[0][3:] if lines and lines[0].startswith("## ") else None
    changed = []
    for line in lines[1:] if branch is not None else lines:
        if len(line) < 4:
            continue
        changed.append({"index": line[0], "worktree": line[1], "path": redact_sensitive_text(line[3:])})
    projects = [p.relative_to(root).as_posix() for p in _project_files(root)[:50]]
    return {
        "project_root": ".",
        "is_git_repo": result["return_code"] == 0,
        "branch": branch,
        "changed_files": changed[:500],
        "changed_count": len(changed),
        "projects": projects,
        "truncated": len(changed) > 500 or len(projects) >= 50,
        "error": result["stderr"] if result["return_code"] else None,
    }


def git_diff(
    project_root: str | None,
    scope: str = "unstaged",
    path: str | None = None,
    context_lines: int = 3,
    max_chars: int = 20_000,
    *,
    access_config: McpAccessConfig,
) -> dict[str, Any]:
    root = resolve_project_root(project_root, access_config=access_config)
    if scope not in {"unstaged", "staged"}:
        raise ProjectContextError("scope має бути 'unstaged' або 'staged'.")
    context = min(max(int(context_lines), 0), 20)
    limit = min(max(int(max_chars), 500), MAX_COMMAND_OUTPUT)
    argv = ["git", "diff", "--no-ext-diff", "--no-color", f"--unified={context}"]
    if scope == "staged":
        argv.append("--cached")
    argv.append("--")
    relative = None
    if path:
        candidate = safe_project_path(root, path)
        relative = candidate.relative_to(root).as_posix()
        argv.append(relative)
    result = _run_fixed(argv, root, 15, max_chars=limit)
    return {
        "scope": scope,
        "path": relative,
        "diff": result["stdout"],
        "return_code": result["return_code"],
        "error": result["stderr"] or None,
        "truncated": result["truncated"],
    }


def git_inspect(
    project_root: str | None,
    max_tracked_files: int = 1_000,
    max_commits: int = 100,
    max_history_paths: int = 2_000,
    *,
    access_config: McpAccessConfig,
) -> dict[str, Any]:
    """Return a bounded, fixed-command audit of the current reachable Git state."""

    root = resolve_project_root(project_root, access_config=access_config)
    tracked_limit = min(max(int(max_tracked_files), 1), MAX_GIT_LIST_ITEMS)
    commit_limit = min(max(int(max_commits), 1), MAX_GIT_COMMITS)
    path_limit = min(max(int(max_history_paths), 1), MAX_GIT_LIST_ITEMS)

    repo = _run_fixed(["git", "rev-parse", "--is-inside-work-tree"], root, 10, max_chars=200)
    if repo["return_code"] != 0 or repo["stdout"].strip() != "true" or not _git_top_level_matches(root):
        return {
            "project_root": ".",
            "is_git_repo": False,
            "branch": None,
            "head": None,
            "tracked_files": [],
            "tracked_files_truncated": False,
            "commits": [],
            "history_truncated": False,
            "history_paths": [],
            "history_paths_truncated": False,
            "error": repo["stderr"] or "Not a Git work tree.",
        }

    branch_result = _run_fixed(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], root, 10, max_chars=1_000)
    head_result = _run_fixed(["git", "rev-parse", "--verify", "HEAD"], root, 10, max_chars=200)
    tracked_result = _run_fixed(
        ["git", "ls-files", "-z"],
        root,
        15,
        max_chars=MAX_GIT_CAPTURE_CHARS,
    )
    tracked_all = _bounded_git_paths(tracked_result["stdout"].split("\0"), tracked_limit + 1)
    tracked_truncated = tracked_result["truncated"] or len(tracked_all) > tracked_limit

    commit_result = _run_fixed(
        [
            "git",
            "log",
            "--no-show-signature",
            f"--max-count={commit_limit + 1}",
            "--format=%H%x1f%P%x1f%aI%x1f%s%x1e",
            "HEAD",
        ],
        root,
        20,
        max_chars=MAX_GIT_CAPTURE_CHARS,
    )
    commits = _parse_git_commits(commit_result["stdout"], commit_limit + 1)
    history_truncated = commit_result["truncated"] or len(commits) > commit_limit

    history_path_result = _run_fixed(
        [
            "git",
            "log",
            "--format=",
            "--name-only",
            "-z",
            "--diff-filter=ACDMRTUXB",
            "HEAD",
            "--",
        ],
        root,
        30,
        max_chars=MAX_GIT_CAPTURE_CHARS,
    )
    history_paths_all = _bounded_git_paths(history_path_result["stdout"].split("\0"), path_limit + 1)
    history_paths_truncated = history_path_result["truncated"] or len(history_paths_all) > path_limit

    return {
        "project_root": ".",
        "is_git_repo": True,
        "branch": branch_result["stdout"].strip() or None,
        "detached_head": branch_result["return_code"] != 0 and head_result["return_code"] == 0,
        "head": head_result["stdout"].strip() or None,
        "tracked_files": tracked_all[:tracked_limit],
        "tracked_file_count": min(len(tracked_all), tracked_limit),
        "tracked_files_truncated": tracked_truncated,
        "commits": commits[:commit_limit],
        "commit_count": min(len(commits), commit_limit),
        "history_truncated": history_truncated,
        "history_paths": history_paths_all[:path_limit],
        "history_path_count": min(len(history_paths_all), path_limit),
        "history_paths_truncated": history_paths_truncated,
        "error": head_result["stderr"] or None,
    }


def _git_top_level_matches(root: Path) -> bool:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            shell=False,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
            env=_safe_subprocess_env(),
        )
        return completed.returncode == 0 and Path(completed.stdout.strip()).resolve() == root.resolve()
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return False


def build_project(
    project_root: str | None,
    project: str | None = None,
    configuration: str = "Debug",
    timeout_seconds: int = 300,
    *,
    access_config: McpAccessConfig,
) -> dict[str, Any]:
    return _dotnet_operation("build", project_root, project, configuration, timeout_seconds, access_config)


def test_project(
    project_root: str | None,
    project: str | None = None,
    configuration: str = "Debug",
    timeout_seconds: int = 300,
    *,
    access_config: McpAccessConfig,
) -> dict[str, Any]:
    return _dotnet_operation("test", project_root, project, configuration, timeout_seconds, access_config)


def validate_manifest(
    project_root: str | None,
    path: str = "manifest.json",
    *,
    access_config: McpAccessConfig,
) -> dict[str, Any]:
    root = resolve_project_root(project_root, access_config=access_config)
    manifest_path = safe_project_path(root, path)
    data = _load_manifest(manifest_path)
    errors, warnings = _manifest_findings(data, manifest_path.parent)
    return {
        "path": manifest_path.relative_to(root).as_posix(),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "manifest": {key: data.get(key) for key in ("Name", "Author", "Version", "UniqueID", "EntryDll", "MinimumApiVersion")},
    }


def validate_mod_artifact(
    project_root: str | None,
    path: str,
    *,
    access_config: McpAccessConfig,
) -> dict[str, Any]:
    root = resolve_project_root(project_root, access_config=access_config)
    archive = safe_project_path(root, path)
    if archive.suffix.casefold() != ".zip" or not archive.is_file():
        raise ProjectContextError("Artifact має бути наявним ZIP-файлом у корені проєкту.")
    if archive.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ProjectContextError("Artifact перевищує дозволений розмір.")
    errors: list[str] = []
    warnings: list[str] = []
    with zipfile.ZipFile(archive) as handle:
        infos = handle.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ProjectContextError("Artifact містить забагато entries.")
        names = [info.filename.replace("\\", "/") for info in infos]
        for name in names:
            parts = Path(name).parts
            if name.startswith("/") or ".." in parts:
                errors.append(f"Unsafe archive path: {name[:200]}")
        manifests = [name for name in names if Path(name).name.casefold() == "manifest.json"]
        if len(manifests) != 1:
            errors.append("Artifact має містити рівно один manifest.json.")
            manifest = {}
        else:
            try:
                manifest = json.loads(handle.read(manifests[0]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError):
                manifest = {}
                errors.append("Artifact manifest.json не є коректним UTF-8 JSON.")
        if manifest:
            manifest_dir = Path(manifests[0]).parent
            manifest_errors, manifest_warnings = _manifest_findings(manifest, None)
            errors.extend(manifest_errors)
            warnings.extend(manifest_warnings)
            entry = manifest.get("EntryDll")
            if isinstance(entry, str) and (manifest_dir / entry).as_posix() not in names:
                errors.append("EntryDll відсутній в artifact.")
        forbidden = re.compile(r"(^|/)(AGENTS\.md|\.codex|agent-state|handoffs?|prompts?)(/|$)", re.I)
        leaked = [name for name in names if forbidden.search(name)]
        if leaked:
            errors.append("Artifact містить AI/private service files.")
    return {
        "path": archive.relative_to(root).as_posix(),
        "valid": not errors,
        "entry_count": len(names),
        "errors": errors[:100],
        "warnings": warnings[:100],
        "manifest": {key: manifest.get(key) for key in ("Name", "Version", "UniqueID", "EntryDll")},
    }


def stardew_environment() -> dict[str, Any]:
    game = _discover_game_directory()
    if game is None:
        return {"found": False, "game_version": None, "smapi_version": None, "source": None}
    game_dll = game / "Stardew Valley.dll"
    smapi_dll = game / "StardewModdingAPI.dll"
    return {
        "found": game_dll.is_file(),
        "source": "configured" if os.getenv("ARVIS_STARDEW_GAME_PATH") else "steam_metadata",
        "game_version": _assembly_version_hint(game_dll, r"1\.6\.\d+(?:\.\d+)?"),
        "smapi_version": _assembly_version_hint(smapi_dll, r"4\.\d+\.\d+(?:\.\d+)?"),
        "has_game_dll": game_dll.is_file(),
        "has_smapi": smapi_dll.is_file(),
    }


def smapi_log_excerpt(max_lines: int = 120, max_chars: int = 20_000) -> dict[str, Any]:
    log = _discover_smapi_log()
    if log is None:
        return {"found": False, "content": "", "line_count": 0, "truncated": False}
    line_limit = min(max(int(max_lines), 1), 500)
    char_limit = min(max(int(max_chars), 500), MAX_COMMAND_OUTPUT)
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)[-line_limit:]
    content = "".join(redact_sensitive_lines(lines))
    return {"found": True, "content": content[:char_limit], "line_count": len(lines), "truncated": len(content) > char_limit}


def smapi_mod_status(mod_id: str, max_matches: int = 80) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", mod_id or ""):
        raise ProjectContextError("Некоректний mod ID.")
    log = _discover_smapi_log()
    if log is None:
        return {"found_log": False, "loaded": False, "has_errors": False, "matches": []}
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    relevant = [line for line in lines if mod_id.casefold() in line.casefold()]
    errors = [line for line in relevant if re.search(r"\b(error|failed|exception|crash)\b", line, re.I)]
    limit = min(max(int(max_matches), 1), 200)
    safe = redact_sensitive_lines([line + "\n" for line in relevant[-limit:]])
    loaded = any(re.search(r"(loading|loaded).*(ID:\s*" + re.escape(mod_id) + r"\b|" + re.escape(mod_id) + r")", line, re.I) for line in relevant)
    return {"found_log": True, "loaded": loaded, "has_errors": bool(errors), "match_count": len(relevant), "matches": [line.rstrip("\n") for line in safe], "truncated": len(relevant) > limit}


def _dotnet_operation(operation: str, project_root: str | None, project: str | None, configuration: str, timeout_seconds: int, access_config: McpAccessConfig) -> dict[str, Any]:
    root = resolve_project_root(project_root, access_config=access_config)
    require_writable_project_root(root, access_config=access_config)
    if configuration not in CONFIGURATIONS:
        raise ProjectContextError("configuration має бути Debug або Release.")
    target = _resolve_project(root, project)
    dotnet = _dotnet_path()
    timeout = min(max(int(timeout_seconds), 10), 600)
    result = _run_fixed([str(dotnet), operation, target.relative_to(root).as_posix(), "--configuration", configuration, "--nologo"], root, timeout)
    combined = (result["stdout"] + "\n" + result["stderr"]).splitlines()
    errors = [line for line in combined if re.search(r"\berror\s+[A-Z]*\d+\b|Build FAILED|Test Run Failed", line, re.I)][:100]
    warnings = [line for line in combined if re.search(r"\bwarning\s+[A-Z]*\d+\b", line, re.I)][:100]
    return {
        "operation": operation,
        "project": target.relative_to(root).as_posix(),
        "configuration": configuration,
        "success": result["return_code"] == 0,
        "return_code": result["return_code"],
        "timed_out": result["timed_out"],
        "duration_seconds": result["duration_seconds"],
        "errors": errors,
        "warnings": warnings,
        "output": result["stdout"],
        "stderr": result["stderr"],
        "truncated": result["truncated"],
    }


def _resolve_project(root: Path, project: str | None) -> Path:
    if project:
        target = safe_project_path(root, project)
        if target.suffix.casefold() not in PROJECT_EXTENSIONS or not target.is_file():
            raise ProjectContextError("Непідтримуваний project file.")
        return target
    files = _project_files(root)
    solutions = [p for p in files if p.suffix.casefold() == ".sln"]
    candidates = solutions or files
    if len(candidates) != 1:
        raise ProjectContextError("Вкажи project: автоматичний вибір неоднозначний або неможливий.")
    return candidates[0]


def _project_files(root: Path) -> list[Path]:
    return sorted((p for p in root.rglob("*") if p.is_file() and p.suffix.casefold() in PROJECT_EXTENSIONS and not any(part in {".git", "bin", "obj"} for part in p.relative_to(root).parts)), key=lambda p: p.as_posix())


def _dotnet_path() -> Path:
    configured = os.getenv("ARVIS_DOTNET_PATH")
    value = Path(configured).expanduser().resolve() if configured else (Path(shutil.which("dotnet")) if shutil.which("dotnet") else None)
    if value is None or not value.is_file() or not os.access(value, os.X_OK):
        raise ProjectContextError(".NET SDK executable не налаштовано.")
    return value


def _run_fixed(argv: list[str], cwd: Path, timeout: int, max_chars: int = MAX_COMMAND_OUTPUT) -> dict[str, Any]:
    import time
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(argv, cwd=cwd, shell=False, stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=timeout, check=False, env=_safe_subprocess_env())
        code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        code = 124
        stdout = exc.stdout or ""
        stderr = "Operation timed out."
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
    except OSError as exc:
        raise ProjectContextError("Не вдалося запустити дозволену операцію.") from exc
    stdout = redact_sensitive_text(_sanitize_command_output(stdout, cwd))
    stderr = redact_sensitive_text(_sanitize_command_output(stderr, cwd))
    truncated = len(stdout) > max_chars or len(stderr) > 8_000
    return {"return_code": code, "stdout": stdout[:max_chars], "stderr": stderr[:8_000], "timed_out": timed_out, "duration_seconds": round(time.monotonic() - started, 3), "truncated": truncated}


def _safe_subprocess_env() -> dict[str, str]:
    allowed = {"HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "DOTNET_ROOT", "NUGET_PACKAGES", "XDG_DATA_HOME", "XDG_CONFIG_HOME"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    env["DOTNET_NOLOGO"] = "1"
    return env


def _sanitize_command_output(value: str, cwd: Path) -> str:
    """Remove machine-local absolute prefixes while keeping diagnostics useful."""
    text = value.replace(str(cwd), ".")
    try:
        text = text.replace(str(cwd.resolve()), ".")
    except OSError:
        pass
    text = text.replace(str(Path.home()), "<HOME>")
    text = re.sub(r"/(?:var/)?home/[^/\s'\"]+", "<HOME>", text)
    text = re.sub(r"[A-Za-z]:\\Users\\[^\\\s'\"]+", "<HOME>", text)
    return text


def _bounded_git_paths(values: list[str], limit: int) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = raw.strip("\r\n")
        if not value or value in seen:
            continue
        seen.add(value)
        paths.append(redact_sensitive_text(value[:1_000]))
        if len(paths) >= limit:
            break
    return paths


def _parse_git_commits(value: str, limit: int) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    for record in value.split("\x1e"):
        if not record.strip():
            continue
        fields = record.strip("\r\n").split("\x1f", 3)
        if len(fields) != 4:
            continue
        sha, parents, authored_at, subject = fields
        commits.append(
            {
                "sha": sha,
                "parents": [parent for parent in parents.split() if parent],
                "authored_at": authored_at,
                "subject": redact_sensitive_text(subject[:500]),
            }
        )
        if len(commits) >= limit:
            break
    return commits


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > 1_000_000:
        raise ProjectContextError("manifest.json відсутній або завеликий.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectContextError("manifest.json не є коректним UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise ProjectContextError("manifest.json має містити JSON object.")
    return value


def _manifest_findings(data: dict[str, Any], base: Path | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for key in ("Name", "Author", "Version", "Description", "UniqueID", "EntryDll", "MinimumApiVersion"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            errors.append(f"Missing or invalid {key}.")
    if isinstance(data.get("UniqueID"), str) and not re.fullmatch(r"[A-Za-z0-9_.-]+", data["UniqueID"]):
        errors.append("UniqueID has unsupported characters.")
    semantic_version = r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?"
    if isinstance(data.get("Version"), str) and not re.fullmatch(rf"(?:%ProjectVersion%|{semantic_version})", data["Version"]):
        errors.append("Version is not a supported semantic version.")
    if isinstance(data.get("MinimumApiVersion"), str) and not re.fullmatch(semantic_version, data["MinimumApiVersion"]):
        errors.append("MinimumApiVersion is not a supported semantic version.")
    entry = data.get("EntryDll")
    if isinstance(entry, str) and (Path(entry).is_absolute() or ".." in Path(entry).parts or Path(entry).suffix.casefold() != ".dll"):
        errors.append("EntryDll must be a relative DLL path without traversal.")
    if base is not None and isinstance(entry, str) and not (base / entry).is_file():
        warnings.append("EntryDll is not present next to the source manifest (it may be build output only).")
    if data.get("ContentPackFor") is not None:
        warnings.append("Manifest is a content pack while an EntryDll is expected for this validator.")
    return errors, warnings


def _discover_game_directory() -> Path | None:
    configured = os.getenv("ARVIS_STARDEW_GAME_PATH")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        return candidate if candidate.is_dir() else None
    steam_roots = [Path.home() / ".local/share/Steam", Path.home() / ".steam/steam"]
    for steam in steam_roots:
        libraries = [steam]
        vdf = steam / "steamapps/libraryfolders.vdf"
        if vdf.is_file():
            text = vdf.read_text(encoding="utf-8", errors="replace")[:2_000_000]
            libraries.extend(Path(value.replace("\\\\", "\\")) for value in re.findall(r'"path"\s+"([^"]+)"', text))
        for library in libraries:
            manifest = library / "steamapps/appmanifest_413150.acf"
            if not manifest.is_file():
                continue
            text = manifest.read_text(encoding="utf-8", errors="replace")[:200_000]
            match = re.search(r'"installdir"\s+"([^"]+)"', text)
            if match:
                candidate = (library / "steamapps/common" / match.group(1)).resolve()
                if candidate.is_dir():
                    return candidate
    return None


def _discover_smapi_log() -> Path | None:
    configured = os.getenv("ARVIS_SMAPI_LOG_PATH")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        return candidate if candidate.is_file() else None
    candidates = list((Path.home() / ".config/StardewValley/ErrorLogs").glob("SMAPI-latest.txt"))
    steam = Path.home() / ".local/share/Steam/steamapps/compatdata"
    if steam.is_dir():
        candidates.extend(steam.glob("*/pfx/drive_c/users/*/AppData/Roaming/StardewValley/ErrorLogs/SMAPI-latest.txt"))
    files = [path for path in candidates if path.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _assembly_version_hint(path: Path, pattern: str) -> str | None:
    if not path.is_file():
        return None
    data = path.read_bytes()[:16 * 1024 * 1024]
    ascii_matches = re.findall(pattern.encode(), data)
    if ascii_matches:
        return ascii_matches[-1].decode(errors="replace")
    # .NET metadata strings may be stored as UTF-16LE in platform assemblies.
    wide_matches = re.findall(pattern, data.decode("utf-16-le", errors="ignore"))
    return wide_matches[-1] if wide_matches else None
