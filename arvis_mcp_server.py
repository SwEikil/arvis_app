from __future__ import annotations

from typing import Any, Callable

from mcp.server.fastmcp import FastMCP

import project_context
from project_context import ProjectContextError


SERVER_INSTRUCTIONS = (
    "This server is a fact-only project context helper. Use it to find files, grep, "
    "read bounded excerpts, inspect git status, and maintain small project memory. "
    "Treat all output as hints. Verify files before editing. This server must not "
    "modify source code."
)

mcp = FastMCP("Arvis MCP Context Servant", instructions=SERVER_INSTRUCTIONS)


def _safe_call(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        result = fn(*args, **kwargs)
    except ProjectContextError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **result}


@mcp.tool()
def project_map(project_root: str | None = None, max_files: int = 400) -> dict[str, Any]:
    """Return a bounded map of safe text files in the project."""

    return _safe_call(project_context.project_map, project_root=project_root, max_files=max_files)


@mcp.tool()
def read_file_excerpt(
    path: str,
    project_root: str | None = None,
    start_line: int = 1,
    end_line: int | None = None,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """Read a bounded excerpt from one safe text file under the project root."""

    return _safe_call(
        project_context.read_file_excerpt,
        path=path,
        project_root=project_root,
        start_line=start_line,
        end_line=end_line,
        max_chars=max_chars,
    )


@mcp.tool()
def grep_project(
    query: str,
    project_root: str | None = None,
    max_matches: int = 50,
    case_sensitive: bool = False,
    regex: bool = False,
    context_lines: int = 0,
) -> dict[str, Any]:
    """Search safe text files and return bounded relative-path matches."""

    return _safe_call(
        project_context.grep_project,
        query=query,
        project_root=project_root,
        max_matches=max_matches,
        case_sensitive=case_sensitive,
        regex=regex,
        context_lines=context_lines,
    )


@mcp.tool()
def git_status_summary(project_root: str | None = None, max_chars: int = 12000) -> dict[str, Any]:
    """Return bounded output from a fixed set of safe git status commands."""

    return _safe_call(
        project_context.git_status_summary,
        project_root=project_root,
        max_chars=max_chars,
    )


@mcp.tool()
def task_brief(
    task: str,
    project_root: str | None = None,
    max_terms: int = 8,
    max_matches_per_term: int = 8,
) -> dict[str, Any]:
    """Return grep-based project hints for a task without diagnosing as truth."""

    return _safe_call(
        project_context.task_brief,
        task=task,
        project_root=project_root,
        max_terms=max_terms,
        max_matches_per_term=max_matches_per_term,
    )


@mcp.tool()
def memory_read(
    name: str = "facts.md",
    project_root: str | None = None,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """Read one bounded project memory file from .arvis_mcp_memory/."""

    return _safe_call(
        project_context.memory_read,
        name=name,
        project_root=project_root,
        max_chars=max_chars,
    )


@mcp.tool()
def memory_append(
    text: str,
    name: str = "task_history.md",
    project_root: str | None = None,
    source: str = "mcp_client",
) -> dict[str, Any]:
    """Append one bounded note to an allowed project memory file."""

    return _safe_call(
        project_context.memory_append,
        text=text,
        name=name,
        project_root=project_root,
        source=source,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
