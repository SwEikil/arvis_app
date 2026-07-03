from __future__ import annotations

import os
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProjectContextError(ValueError):
    """Controlled error for unsafe or unsupported project context requests."""


EXCLUDED_NAMES = {
    ".cache",
    ".codex",
    ".env",
    ".env.local",
    ".git",
    ".runtime",
    ".venv",
    "__pycache__",
    "logs",
    "models",
    "node_modules",
    "ollama-models",
    "venv",
}
EXCLUDED_SUFFIXES = {
    ".bin",
    ".db",
    ".gguf",
    ".log",
    ".pt",
    ".pth",
    ".pyc",
    ".safetensors",
    ".sqlite",
}
MEMORY_DIR_NAME = ".arvis_mcp_memory"
ALLOWED_MEMORY_FILES = {
    "architecture.md",
    "commands.md",
    "decisions.md",
    "facts.md",
    "known_bugs.md",
    "task_history.md",
}
TEXT_EXTENSIONS = {
    "",
    ".cfg",
    ".css",
    ".csv",
    ".example",
    ".gitignore",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".lock",
    ".md",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_FILENAMES = {
    "AGENTS.md",
    "LICENSE",
    "Makefile",
    "README",
    "README.md",
    "requirements.txt",
}


def resolve_project_root(project_root: str | None = None) -> Path:
    root_value = project_root or os.environ.get("ARVIS_MCP_PROJECT_ROOT") or os.getcwd()
    root = Path(root_value).expanduser().resolve()
    if not root.exists():
        raise ProjectContextError("Project root does not exist.")
    if not root.is_dir():
        raise ProjectContextError("Project root is not a directory.")
    return root


def safe_project_path(root: Path, user_path: str) -> Path:
    if not user_path or not user_path.strip():
        raise ProjectContextError("Path must not be empty.")

    resolved_root = root.expanduser().resolve()
    requested = Path(user_path).expanduser()
    candidate = requested.resolve() if requested.is_absolute() else (resolved_root / requested).resolve()

    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ProjectContextError("Path is outside the project root.")

    relative = candidate.relative_to(resolved_root)
    _reject_excluded_relative_path(relative)
    return candidate


def project_map(project_root: str | None = None, max_files: int = 400) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    limit = _bounded_int(max_files, default=400, minimum=1, maximum=2000)
    files: list[dict[str, Any]] = []
    extension_counts: Counter[str] = Counter()
    skipped_after_limit = 0

    for path in _iter_safe_files(root):
        if len(files) >= limit:
            skipped_after_limit += 1
            continue
        relative = path.relative_to(root)
        extension = path.suffix.lower()
        extension_counts[extension or "[none]"] += 1
        files.append(
            {
                "path": _relative_string(relative),
                "size": path.stat().st_size,
                "kind": _guess_file_kind(path),
                "extension": extension,
            }
        )

    return {
        "project_root": ".",
        "max_files": limit,
        "file_count": len(files),
        "truncated": skipped_after_limit > 0,
        "skipped_after_limit": skipped_after_limit,
        "extension_counts": dict(sorted(extension_counts.items())),
        "files": files,
    }


def read_file_excerpt(
    path: str,
    project_root: str | None = None,
    start_line: int = 1,
    end_line: int | None = None,
    max_chars: int = 12000,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    file_path = safe_project_path(root, path)
    _ensure_safe_text_file(root, file_path)

    start = _bounded_int(start_line, default=1, minimum=1, maximum=1_000_000)
    char_limit = _bounded_int(max_chars, default=12000, minimum=200, maximum=50000)
    requested_end = None
    if end_line is not None:
        requested_end = _bounded_int(end_line, default=start, minimum=start, maximum=1_000_000)

    lines = _read_text_lines(file_path)
    total_lines = len(lines)
    stop = requested_end if requested_end is not None else min(total_lines, start + 199)
    stop = min(stop, total_lines)
    selected = lines[start - 1 : stop] if start <= total_lines else []
    content, included_lines, char_truncated = _join_bounded_lines(selected, char_limit)
    end = start + included_lines - 1 if included_lines else min(stop, total_lines)
    truncated = char_truncated

    if requested_end is None and stop < total_lines:
        truncated = True

    return {
        "path": _relative_string(file_path.relative_to(root)),
        "start_line": start,
        "end_line": end,
        "total_lines": total_lines,
        "content": content,
        "truncated": truncated,
    }


def grep_project(
    query: str,
    project_root: str | None = None,
    max_matches: int = 50,
    case_sensitive: bool = False,
    regex: bool = False,
    context_lines: int = 0,
) -> dict[str, Any]:
    if not query:
        raise ProjectContextError("Search query must not be empty.")

    root = resolve_project_root(project_root)
    match_limit = _bounded_int(max_matches, default=50, minimum=1, maximum=500)
    context_limit = _bounded_int(context_lines, default=0, minimum=0, maximum=5)
    flags = 0 if case_sensitive else re.IGNORECASE

    if regex:
        try:
            pattern = re.compile(query, flags)
        except re.error as exc:
            raise ProjectContextError(f"Invalid regex: {exc}") from exc
    else:
        pattern = None
        needle = query if case_sensitive else query.lower()

    matches: list[dict[str, Any]] = []
    total_matches = 0

    for path in _iter_safe_files(root):
        lines = _read_text_lines(path)
        for index, line in enumerate(lines):
            haystack = line if case_sensitive else line.lower()
            is_match = bool(pattern.search(line)) if pattern else needle in haystack
            if not is_match:
                continue

            total_matches += 1
            if len(matches) >= match_limit:
                continue

            item: dict[str, Any] = {
                "path": _relative_string(path.relative_to(root)),
                "line_number": index + 1,
                "line": line.rstrip("\n"),
            }
            if context_limit:
                before_start = max(0, index - context_limit)
                after_stop = min(len(lines), index + context_limit + 1)
                item["context"] = [
                    {"line_number": i + 1, "line": lines[i].rstrip("\n")}
                    for i in range(before_start, after_stop)
                    if i != index
                ]
            matches.append(item)

    return {
        "query": query,
        "regex": regex,
        "case_sensitive": case_sensitive,
        "max_matches": match_limit,
        "match_count": len(matches),
        "total_matches": total_matches,
        "truncated": total_matches > len(matches),
        "matches": matches,
    }


def git_status_summary(project_root: str | None = None, max_chars: int = 12000) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    char_limit = _bounded_int(max_chars, default=12000, minimum=500, maximum=50000)
    commands = {
        "status_short": ["git", "status", "--short"],
        "diff_stat": ["git", "diff", "--stat"],
        "diff_name_only": ["git", "diff", "--name-only"],
        "cached_name_only": ["git", "diff", "--cached", "--name-only"],
    }
    outputs: dict[str, str] = {}
    errors: dict[str, str] = {}
    is_git_repo = True

    for name, command in commands.items():
        try:
            result = subprocess.run(
                command,
                cwd=root,
                shell=False,
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            outputs[name] = ""
            errors[name] = str(exc)
            is_git_repo = False
            continue

        stdout = _truncate_text(result.stdout, char_limit)
        stderr = _truncate_text(result.stderr, 2000)
        outputs[name] = stdout
        if result.returncode != 0:
            errors[name] = stderr or f"git exited with status {result.returncode}"
            is_git_repo = False

    return {
        "project_root": ".",
        "is_git_repo": is_git_repo,
        "outputs": outputs,
        "errors": errors,
        "truncated": any(len(value) >= char_limit for value in outputs.values()),
    }


def task_brief(
    task: str,
    project_root: str | None = None,
    max_terms: int = 8,
    max_matches_per_term: int = 8,
) -> dict[str, Any]:
    if not task or not task.strip():
        raise ProjectContextError("Task must not be empty.")

    root = resolve_project_root(project_root)
    term_limit = _bounded_int(max_terms, default=8, minimum=1, maximum=20)
    matches_limit = _bounded_int(max_matches_per_term, default=8, minimum=1, maximum=50)
    terms = _extract_terms(task, term_limit)
    candidate_files: dict[str, dict[str, Any]] = {}
    term_results: list[dict[str, Any]] = []

    for term in terms:
        result = grep_project(
            term,
            project_root=str(root),
            max_matches=matches_limit,
            case_sensitive=False,
            regex=False,
            context_lines=0,
        )
        term_results.append(
            {
                "term": term,
                "match_count": result["match_count"],
                "truncated": result["truncated"],
                "matches": result["matches"],
            }
        )
        for match in result["matches"]:
            path = match["path"]
            candidate = candidate_files.setdefault(path, {"path": path, "score": 0, "lines": []})
            candidate["score"] += 1
            candidate["lines"].append(match["line_number"])

    candidates = sorted(candidate_files.values(), key=lambda item: (-item["score"], item["path"]))
    for candidate in candidates:
        candidate["lines"] = sorted(set(candidate["lines"]))[:20]

    return {
        "task": task,
        "warning": "Use this as hints only. Verify files directly before editing.",
        "terms": terms,
        "candidate_files": candidates[:20],
        "term_results": term_results,
    }


def memory_read(
    name: str = "facts.md",
    project_root: str | None = None,
    max_chars: int = 12000,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    memory_path = _memory_file_path(root, name)
    char_limit = _bounded_int(max_chars, default=12000, minimum=200, maximum=50000)

    if not memory_path.exists():
        return {"name": name, "content": "", "exists": False, "truncated": False}

    content = memory_path.read_text(encoding="utf-8", errors="replace")
    truncated = len(content) > char_limit
    return {
        "name": name,
        "content": content[:char_limit],
        "exists": True,
        "truncated": truncated,
    }


def memory_append(
    text: str,
    name: str = "task_history.md",
    project_root: str | None = None,
    source: str = "mcp_client",
) -> dict[str, Any]:
    if not text or not text.strip():
        raise ProjectContextError("Memory text must not be empty.")

    root = resolve_project_root(project_root)
    memory_path = _memory_file_path(root, name)
    bounded_text = _truncate_text(text.strip(), 4000)
    bounded_source = re.sub(r"[^A-Za-z0-9_.:-]+", "_", source.strip() or "mcp_client")[:80]
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    note = f"\n## {timestamp} source={bounded_source}\n\n{bounded_text}\n"

    memory_path.parent.mkdir(mode=0o700, exist_ok=True)
    with memory_path.open("a", encoding="utf-8") as handle:
        handle.write(note)

    return {
        "name": name,
        "path": _relative_string(memory_path.relative_to(root)),
        "written_chars": len(note),
        "truncated": len(text.strip()) > len(bounded_text),
    }


def _iter_safe_files(root: Path):
    resolved_root = root.resolve()
    for current_root, dir_names, file_names in os.walk(root):
        current_path = Path(current_root)
        relative_root = current_path.relative_to(root)
        dir_names[:] = [
            name
            for name in sorted(dir_names)
            if not _is_excluded_relative_path(relative_root / name)
        ]
        for file_name in sorted(file_names):
            relative = relative_root / file_name
            if _is_excluded_relative_path(relative):
                continue
            path = current_path / file_name
            try:
                resolved_path = path.resolve()
            except OSError:
                continue
            if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
                continue
            if not path.is_file() or not _is_likely_text_file(path):
                continue
            yield path


def _ensure_safe_text_file(root: Path, path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise ProjectContextError("Path is not a readable file.")
    relative = path.relative_to(root)
    _reject_excluded_relative_path(relative)
    if not _is_likely_text_file(path):
        raise ProjectContextError("File is not an allowed text file.")


def _is_likely_text_file(path: Path) -> bool:
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if path.name in TEXT_FILENAMES:
        return True
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return False
    try:
        with path.open("rb") as handle:
            chunk = handle.read(2048)
    except OSError:
        return False
    if b"\0" in chunk:
        return False
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _read_text_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="strict").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise ProjectContextError("File is not valid UTF-8 text.") from exc
    except OSError as exc:
        raise ProjectContextError(f"Could not read file: {exc}") from exc


def _join_bounded_lines(lines: list[str], max_chars: int) -> tuple[str, int, bool]:
    content_parts: list[str] = []
    remaining = max_chars
    included_lines = 0

    for line in lines:
        if remaining <= 0:
            return "".join(content_parts), included_lines, True
        if len(line) > remaining:
            content_parts.append(line[:remaining])
            return "".join(content_parts), included_lines + 1, True
        content_parts.append(line)
        remaining -= len(line)
        included_lines += 1

    return "".join(content_parts), included_lines, False


def _is_excluded_relative_path(relative: Path) -> bool:
    parts = relative.parts
    return any(part in EXCLUDED_NAMES or part == MEMORY_DIR_NAME for part in parts) or relative.suffix.lower() in EXCLUDED_SUFFIXES


def _reject_excluded_relative_path(relative: Path) -> None:
    if _is_excluded_relative_path(relative):
        raise ProjectContextError("Path is excluded from MCP project context.")


def _memory_file_path(root: Path, name: str) -> Path:
    if name not in ALLOWED_MEMORY_FILES:
        raise ProjectContextError("Unsupported memory file name.")
    memory_path = (root / MEMORY_DIR_NAME / name).resolve()
    if root not in memory_path.parents:
        raise ProjectContextError("Memory path is outside the project root.")
    return memory_path


def _bounded_int(value: int, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _guess_file_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if path.name in {"README", "README.md", "AGENTS.md"} or suffix in {".md", ".rst", ".txt"}:
        return "docs"
    if suffix == ".py":
        return "python"
    if suffix in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
        return "config"
    if suffix in {".sh"}:
        return "script"
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".css", ".html"}:
        return "web"
    return "text"


def _extract_terms(task: str, max_terms: int) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u0400-\u04FF]{3,}", task)
    stop_words = {
        "add",
        "and",
        "for",
        "from",
        "implement",
        "into",
        "the",
        "this",
        "with",
        "without",
    }
    counts: defaultdict[str, int] = defaultdict(int)
    original: dict[str, str] = {}
    for word in words:
        normalized = word.lower()
        if normalized in stop_words:
            continue
        counts[normalized] += 1
        original.setdefault(normalized, word)
    ranked = sorted(counts, key=lambda item: (-counts[item], item))
    return [original[item] for item in ranked[:max_terms]]


def _relative_string(path: Path) -> str:
    return path.as_posix() if path.as_posix() != "." else "."
