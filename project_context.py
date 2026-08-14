from __future__ import annotations

import os
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_access import McpAccessConfig, load_mcp_access_config, path_is_within
from mcp_security import redact_sensitive_lines, redact_sensitive_text


class ProjectContextError(ValueError):
    """Контрольована помилка небезпечного або непідтримуваного запиту контексту."""


EXCLUDED_NAMES = {
    ".aws",
    ".cache",
    ".codex",
    ".docker",
    ".env",
    ".env.local",
    ".git",
    ".gnupg",
    ".kube",
    ".ssh",
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
    ".jks",
    ".key",
    ".keystore",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".pt",
    ".pth",
    ".pyc",
    ".safetensors",
    ".sqlite",
}
SECRET_FILE_NAMES = {
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".envrc",
    ".git-credentials",
    "auth.json",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_ecdsa",
    "id_rsa",
    "secrets.json",
    "service-account.json",
}
SAFE_SECRET_EXAMPLE_SUFFIXES = (".example", ".sample", ".template")
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

MAX_SCAN_FILE_BYTES = 2 * 1024 * 1024
MAX_OPERATION_SCAN_BYTES = 32 * 1024 * 1024
MAX_TRAVERSAL_ENTRIES = 20_000
MAX_REGEX_PATTERN_CHARS = 256
MAX_REGEX_LINE_CHARS = 20_000
MAX_SEARCH_QUERY_CHARS = 1_000
MAX_TASK_CHARS = 8_000


@dataclass
class _ScanBudget:
    max_total_bytes: int = MAX_OPERATION_SCAN_BYTES
    max_entries: int = MAX_TRAVERSAL_ENTRIES
    scanned_files: int = 0
    scanned_bytes: int = 0
    visited_entries: int = 0
    truncated: bool = False


def resolve_project_root(
    project_root: str | None = None,
    *,
    access_config: McpAccessConfig | None = None,
) -> Path:
    config = access_config or load_mcp_access_config()
    if config.configuration_error:
        raise ProjectContextError(config.configuration_error)
    if not config.allowed_roots or config.default_root is None:
        raise ProjectContextError("Не налаштовано жодного кореня MCP-проєкту.")

    if project_root and project_root.strip():
        requested = Path(project_root.strip()).expanduser()
        candidate = requested if requested.is_absolute() else config.default_root / requested
    else:
        candidate = config.default_root

    try:
        root = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise ProjectContextError("Не вдалося визначити корінь проєкту.") from exc
    if root == Path(root.anchor):
        raise ProjectContextError("Корінь файлової системи не можна використовувати як корінь проєкту.")
    if not any(path_is_within(root, allowed_root) for allowed_root in config.allowed_roots):
        raise ProjectContextError("Корінь проєкту перебуває поза налаштованим списком дозволених коренів.")
    if not root.exists():
        raise ProjectContextError("Корінь проєкту не існує.")
    if not root.is_dir():
        raise ProjectContextError("Корінь проєкту не є каталогом.")
    return root


def safe_project_path(root: Path, user_path: str) -> Path:
    if not user_path or not user_path.strip():
        raise ProjectContextError("Шлях не може бути порожнім.")

    try:
        resolved_root = root.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise ProjectContextError("Не вдалося визначити корінь проєкту.") from exc
    requested = Path(user_path).expanduser()
    try:
        candidate = requested.resolve() if requested.is_absolute() else (resolved_root / requested).resolve()
    except (OSError, RuntimeError) as exc:
        raise ProjectContextError("Не вдалося визначити шлях.") from exc

    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ProjectContextError("Шлях перебуває поза коренем проєкту.")

    relative = candidate.relative_to(resolved_root)
    _reject_excluded_relative_path(relative)
    return candidate


def project_map(
    project_root: str | None = None,
    max_files: int = 400,
    *,
    access_config: McpAccessConfig | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(project_root, access_config=access_config)
    limit = _bounded_int(max_files, default=400, minimum=1, maximum=2000)
    files: list[dict[str, Any]] = []
    extension_counts: Counter[str] = Counter()
    budget = _ScanBudget(max_total_bytes=MAX_OPERATION_SCAN_BYTES)

    for path in _iter_safe_files(root, budget=budget):
        if len(files) >= limit:
            budget.truncated = True
            break
        relative = path.relative_to(root)
        extension = path.suffix.lower()
        extension_counts[extension or "[none]"] += 1
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files.append(
            {
                "path": redact_sensitive_text(_relative_string(relative)),
                "size": size,
                "kind": _guess_file_kind(path),
                "extension": extension,
            }
        )

    return {
        "project_root": ".",
        "max_files": limit,
        "file_count": len(files),
        "truncated": budget.truncated,
        "skipped_after_limit": 1 if budget.truncated and len(files) >= limit else 0,
        "visited_entries": budget.visited_entries,
        "extension_counts": dict(sorted(extension_counts.items())),
        "files": files,
    }


def read_file_excerpt(
    path: str,
    project_root: str | None = None,
    start_line: int = 1,
    end_line: int | None = None,
    max_chars: int = 12000,
    *,
    access_config: McpAccessConfig | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(project_root, access_config=access_config)
    file_path = safe_project_path(root, path)
    _ensure_safe_text_file(root, file_path)

    start = _bounded_int(start_line, default=1, minimum=1, maximum=1_000_000)
    char_limit = _bounded_int(max_chars, default=12000, minimum=200, maximum=50000)
    requested_end = None
    if end_line is not None:
        requested_end = _bounded_int(end_line, default=start, minimum=start, maximum=1_000_000)

    lines = _read_text_lines(file_path)
    redacted_lines = redact_sensitive_lines(lines)
    total_lines = len(lines)
    stop = requested_end if requested_end is not None else min(total_lines, start + 199)
    stop = min(stop, total_lines)
    selected = redacted_lines[start - 1 : stop] if start <= total_lines else []
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
    *,
    access_config: McpAccessConfig | None = None,
    _max_total_bytes: int = MAX_OPERATION_SCAN_BYTES,
    _max_entries: int = MAX_TRAVERSAL_ENTRIES,
) -> dict[str, Any]:
    if not query:
        raise ProjectContextError("Пошуковий запит не може бути порожнім.")
    if len(query) > MAX_SEARCH_QUERY_CHARS:
        raise ProjectContextError("Пошуковий запит перевищує ліміт довжини MCP.")

    root = resolve_project_root(project_root, access_config=access_config)
    match_limit = _bounded_int(max_matches, default=50, minimum=1, maximum=500)
    context_limit = _bounded_int(context_lines, default=0, minimum=0, maximum=5)
    flags = 0 if case_sensitive else re.IGNORECASE

    if regex:
        _validate_regex_query(query)
        try:
            pattern = re.compile(query, flags)
        except re.error as exc:
            raise ProjectContextError("Некоректний regex.") from exc
    else:
        pattern = None
        needle = query if case_sensitive else query.lower()

    matches: list[dict[str, Any]] = []
    total_matches = 0
    budget = _ScanBudget(max_total_bytes=_max_total_bytes, max_entries=_max_entries)

    for path in _iter_safe_files(root, budget=budget):
        lines = _read_text_lines(path)
        redacted_lines = redact_sensitive_lines(lines)
        for index, line in enumerate(lines):
            haystack = line if case_sensitive else line.lower()
            regex_line = line[:MAX_REGEX_LINE_CHARS] if pattern else line
            is_match = bool(pattern.search(regex_line)) if pattern else needle in haystack
            if not is_match:
                continue

            total_matches += 1
            if len(matches) >= match_limit:
                continue

            item: dict[str, Any] = {
                "path": redact_sensitive_text(_relative_string(path.relative_to(root))),
                "line_number": index + 1,
                "line": redacted_lines[index].rstrip("\n"),
            }
            if context_limit:
                before_start = max(0, index - context_limit)
                after_stop = min(len(lines), index + context_limit + 1)
                item["context"] = [
                    {"line_number": i + 1, "line": redacted_lines[i].rstrip("\n")}
                    for i in range(before_start, after_stop)
                    if i != index
                ]
            matches.append(item)

    return {
        "query": redact_sensitive_text(query),
        "regex": regex,
        "case_sensitive": case_sensitive,
        "max_matches": match_limit,
        "match_count": len(matches),
        "total_matches": total_matches,
        "truncated": total_matches > len(matches) or budget.truncated,
        "scan_truncated": budget.truncated,
        "scanned_files": budget.scanned_files,
        "scanned_bytes": budget.scanned_bytes,
        "matches": matches,
    }


def git_status_summary(
    project_root: str | None = None,
    max_chars: int = 12000,
    *,
    access_config: McpAccessConfig | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(project_root, access_config=access_config)
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
        except subprocess.TimeoutExpired:
            outputs[name] = ""
            errors[name] = "Перевищено час очікування команди Git."
            is_git_repo = False
            continue
        except OSError:
            outputs[name] = ""
            errors[name] = "Не вдалося запустити команду Git."
            is_git_repo = False
            continue

        stdout = redact_sensitive_text(_truncate_text(result.stdout, char_limit))
        stderr = redact_sensitive_text(_truncate_text(result.stderr, 2000))
        outputs[name] = stdout
        if result.returncode != 0:
            errors[name] = stderr or f"git завершив роботу зі статусом {result.returncode}"
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
    *,
    access_config: McpAccessConfig | None = None,
) -> dict[str, Any]:
    if not task or not task.strip():
        raise ProjectContextError("Опис задачі не може бути порожнім.")
    if len(task) > MAX_TASK_CHARS:
        raise ProjectContextError("Опис задачі перевищує ліміт довжини MCP.")

    root = resolve_project_root(project_root, access_config=access_config)
    term_limit = _bounded_int(max_terms, default=8, minimum=1, maximum=20)
    matches_limit = _bounded_int(max_matches_per_term, default=8, minimum=1, maximum=50)
    redacted_task = redact_sensitive_text(task)
    terms = _extract_terms(redacted_task, term_limit)
    per_term_scan_bytes = max(1, MAX_OPERATION_SCAN_BYTES // max(1, len(terms)))
    per_term_entries = max(1, MAX_TRAVERSAL_ENTRIES // max(1, len(terms)))
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
            access_config=access_config,
            _max_total_bytes=per_term_scan_bytes,
            _max_entries=per_term_entries,
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
        "task": redacted_task,
        "warning": "Використовуй це лише як підказки. Перевіряй файли безпосередньо перед редагуванням.",
        "terms": terms,
        "candidate_files": candidates[:20],
        "term_results": term_results,
    }


def memory_read(
    name: str = "facts.md",
    project_root: str | None = None,
    max_chars: int = 12000,
    *,
    access_config: McpAccessConfig | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(project_root, access_config=access_config)
    memory_path = _memory_file_path(root, name)
    char_limit = _bounded_int(max_chars, default=12000, minimum=200, maximum=50000)

    if not memory_path.exists():
        return {"name": name, "content": "", "exists": False, "truncated": False}

    try:
        if memory_path.stat().st_size > MAX_SCAN_FILE_BYTES:
            raise ProjectContextError("Файл пам'яті перевищує ліміт розміру MCP.")
        content = memory_path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProjectContextError("Файл пам'яті не є коректним текстом UTF-8.") from exc
    except OSError as exc:
        raise ProjectContextError("Не вдалося прочитати файл пам'яті.") from exc
    redacted_content = "".join(redact_sensitive_lines(content.splitlines(keepends=True)))
    truncated = len(redacted_content) > char_limit
    return {
        "name": name,
        "content": redacted_content[:char_limit],
        "exists": True,
        "truncated": truncated,
    }


def memory_append(
    text: str,
    name: str = "task_history.md",
    project_root: str | None = None,
    source: str = "mcp_client",
    *,
    access_config: McpAccessConfig | None = None,
) -> dict[str, Any]:
    if not text or not text.strip():
        raise ProjectContextError("Текст нотатки пам'яті не може бути порожнім.")

    config = access_config or load_mcp_access_config()
    if not config.memory_writes_allowed:
        raise ProjectContextError("Запис до пам'яті вимкнений для цього профілю MCP.")

    root = resolve_project_root(project_root, access_config=config)
    memory_path = _memory_file_path(root, name)
    bounded_text = _truncate_text(text.strip(), 4000)
    bounded_source = re.sub(r"[^A-Za-z0-9_.:-]+", "_", source.strip() or "mcp_client")[:80]
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    note = f"\n## {timestamp} source={bounded_source}\n\n{bounded_text}\n"

    try:
        memory_path.parent.mkdir(mode=0o700, exist_ok=True)
        with memory_path.open("a", encoding="utf-8") as handle:
            handle.write(note)
    except OSError as exc:
        raise ProjectContextError("Не вдалося додати нотатку до файлу пам'яті MCP.") from exc

    return {
        "name": name,
        "path": _relative_string(memory_path.relative_to(root)),
        "written_chars": len(note),
        "truncated": len(text.strip()) > len(bounded_text),
    }


def _iter_safe_files(root: Path, *, budget: _ScanBudget):
    resolved_root = root.resolve()
    for current_root, dir_names, file_names in os.walk(root):
        current_path = Path(current_root)
        relative_root = current_path.relative_to(root)
        budget.visited_entries += 1
        if budget.visited_entries > budget.max_entries:
            budget.truncated = True
            return
        dir_names[:] = [
            name
            for name in sorted(dir_names)
            if not _is_excluded_relative_path(relative_root / name)
        ]
        for file_name in sorted(file_names):
            budget.visited_entries += 1
            if budget.visited_entries > budget.max_entries:
                budget.truncated = True
                return
            relative = relative_root / file_name
            if _is_excluded_relative_path(relative):
                continue
            path = current_path / file_name
            try:
                resolved_path = path.resolve()
                is_file = path.is_file()
                size = path.stat().st_size
            except (OSError, RuntimeError):
                continue
            if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
                continue
            if not is_file or size > MAX_SCAN_FILE_BYTES or not _is_likely_text_file(path):
                continue
            if budget.scanned_bytes + size > budget.max_total_bytes:
                budget.truncated = True
                return
            budget.scanned_files += 1
            budget.scanned_bytes += size
            yield path


def _ensure_safe_text_file(root: Path, path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise ProjectContextError("Шлях не вказує на доступний для читання файл.")
    relative = path.relative_to(root)
    _reject_excluded_relative_path(relative)
    try:
        if path.stat().st_size > MAX_SCAN_FILE_BYTES:
            raise ProjectContextError("Файл перевищує ліміт розміру MCP.")
    except OSError as exc:
        raise ProjectContextError("Не вдалося перевірити файл.") from exc
    if not _is_likely_text_file(path):
        raise ProjectContextError("Файл не належить до дозволених текстових файлів.")


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
        raise ProjectContextError("Файл не є коректним текстом UTF-8.") from exc
    except OSError as exc:
        raise ProjectContextError("Не вдалося прочитати файл.") from exc


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
    if any(part.casefold() in EXCLUDED_NAMES or part == MEMORY_DIR_NAME for part in parts):
        return True
    name = relative.name.casefold()
    if name.endswith(SAFE_SECRET_EXAMPLE_SUFFIXES):
        return False
    if name.startswith(".env.") or name in SECRET_FILE_NAMES:
        return True
    if name.startswith(("credentials.", "secrets.", "service-account.")):
        return True
    return relative.suffix.lower() in EXCLUDED_SUFFIXES


def _reject_excluded_relative_path(relative: Path) -> None:
    if _is_excluded_relative_path(relative):
        raise ProjectContextError("Шлях виключено з контексту MCP-проєкту.")


def _memory_file_path(root: Path, name: str) -> Path:
    if name not in ALLOWED_MEMORY_FILES:
        raise ProjectContextError("Непідтримувана назва файлу пам'яті.")
    memory_path = (root / MEMORY_DIR_NAME / name).resolve()
    if root not in memory_path.parents:
        raise ProjectContextError("Шлях пам'яті перебуває поза коренем проєкту.")
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
        "redacted",
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


def _validate_regex_query(query: str) -> None:
    if len(query) > MAX_REGEX_PATTERN_CHARS:
        raise ProjectContextError("Regex-запит перевищує ліміт довжини MCP.")
    if re.search(r"\\[1-9]", query):
        raise ProjectContextError("Зворотні посилання regex не підтримуються.")
    if re.search(r"\((?:[^()]|\\.)*[+*?{][^()]*\)\s*[+*?{]", query):
        raise ProjectContextError("Вкладене повторення regex не підтримується.")
    if re.search(r"\((?:[^()]|\\.)*\|(?:[^()]|\\.)*\)\s*[+*?{]", query):
        raise ProjectContextError("Повторювана альтернація regex не підтримується.")
    if re.search(r"(?:\.\*){2,}|(?:\.\+){2,}", query):
        raise ProjectContextError("Повторюваний wildcard regex не підтримується.")
