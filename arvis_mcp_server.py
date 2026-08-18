from __future__ import annotations

import logging
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

import project_context
import project_operations
import system_context
import codex_agents
from mcp_access import PROFILE_CHATGPT, load_local_mcp_environment, load_mcp_access_config
from project_context import ProjectContextError
from system_context import SystemContextError


load_local_mcp_environment()
ACCESS_CONFIG = load_mcp_access_config()

SERVER_INSTRUCTIONS = (
    "Цей сервер — допоміжний сервіс фактів про контекст проєкту. Використовуй його "
    "для пошуку файлів і тексту, читання обмежених уривків, перевірки стану Git, "
    "читання невеликої пам'яті проєкту та отримання обмежених фактів про "
    "систему, пакунки, KDE і QML через фіксовані операції. Вважай усі "
    "результати підказками та перевіряй файли безпосередньо перед редагуванням. "
    "Build/test і Codex lifecycle доступні лише як вузькі project-scoped операції; "
    "довільний shell недоступний."
    + (
        " Цей профіль не дозволяє source/memory writes; лише явно позначені "
        "project build/test та lifecycle control можуть змінювати локальний runtime state."
        if ACCESS_CONFIG.profile == PROFILE_CHATGPT
        else " Профіль Codex може додавати обмежені нотатки до локальної пам'яті."
    )
)

mcp = FastMCP("Arvis MCP Context Servant", instructions=SERVER_INSTRUCTIONS)
logger = logging.getLogger("arvis.mcp")

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
MEMORY_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
CONTROL_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


def _safe_call(fn: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        result = fn(*args, **kwargs)
    except ProjectContextError as exc:
        logger.warning("Інструмент MCP %s відхилив запит.", fn.__name__)
        return {"ok": False, "error": str(exc)}
    except SystemContextError as exc:
        logger.warning("Інструмент MCP %s відхилив запит: %s", fn.__name__, exc.code)
        return {"ok": False, "error_code": exc.code, "error": str(exc)}
    except Exception as exc:  # Захисна межа протоколу не повертає клієнту traceback.
        logger.error("Помилка інструменту MCP %s: %s", fn.__name__, type(exc).__name__)
        return {"ok": False, "error": "Внутрішня помилка інструменту MCP."}
    return {"ok": True, **result}


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def project_map(project_root: str | None = None, max_files: int = 400) -> dict[str, Any]:
    """Повернути обмежену карту безпечних текстових файлів проєкту."""

    return _safe_call(
        project_context.project_map,
        project_root=project_root,
        max_files=max_files,
        access_config=ACCESS_CONFIG,
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def read_file_excerpt(
    path: str,
    project_root: str | None = None,
    start_line: int = 1,
    end_line: int | None = None,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """Прочитати обмежений уривок безпечного текстового файлу в корені проєкту."""

    return _safe_call(
        project_context.read_file_excerpt,
        path=path,
        project_root=project_root,
        start_line=start_line,
        end_line=end_line,
        max_chars=max_chars,
        access_config=ACCESS_CONFIG,
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def grep_project(
    query: str,
    project_root: str | None = None,
    max_matches: int = 50,
    case_sensitive: bool = False,
    regex: bool = False,
    context_lines: int = 0,
) -> dict[str, Any]:
    """Знайти текст у безпечних файлах і повернути обмежені збіги з відносними шляхами."""

    return _safe_call(
        project_context.grep_project,
        query=query,
        project_root=project_root,
        max_matches=max_matches,
        case_sensitive=case_sensitive,
        regex=regex,
        context_lines=context_lines,
        access_config=ACCESS_CONFIG,
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def git_status_summary(project_root: str | None = None, max_chars: int = 12000) -> dict[str, Any]:
    """Повернути обмежений результат фіксованого набору безпечних команд Git."""

    return _safe_call(
        project_context.git_status_summary,
        project_root=project_root,
        max_chars=max_chars,
        access_config=ACCESS_CONFIG,
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def project_state(project_root: str | None = None) -> dict[str, Any]:
    """Повернути branch, структурований список змінених файлів і project files."""
    return _safe_call(project_operations.project_state, project_root=project_root, access_config=ACCESS_CONFIG)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def git_diff(project_root: str | None = None, scope: str = "unstaged", path: str | None = None, context_lines: int = 3, max_chars: int = 20000) -> dict[str, Any]:
    """Повернути bounded unstaged або staged Git diff без external diff drivers."""
    return _safe_call(project_operations.git_diff, project_root=project_root, scope=scope, path=path, context_lines=context_lines, max_chars=max_chars, access_config=ACCESS_CONFIG)


@mcp.tool(annotations=CONTROL_ANNOTATIONS)
def build_project(project_root: str | None = None, project: str | None = None, configuration: str = "Debug", timeout_seconds: int = 300) -> dict[str, Any]:
    """Запустити тільки dotnet build для валідованого project file."""
    return _safe_call(project_operations.build_project, project_root=project_root, project=project, configuration=configuration, timeout_seconds=timeout_seconds, access_config=ACCESS_CONFIG)


@mcp.tool(annotations=CONTROL_ANNOTATIONS)
def test_project(project_root: str | None = None, project: str | None = None, configuration: str = "Debug", timeout_seconds: int = 300) -> dict[str, Any]:
    """Запустити тільки dotnet test для валідованого project file."""
    return _safe_call(project_operations.test_project, project_root=project_root, project=project, configuration=configuration, timeout_seconds=timeout_seconds, access_config=ACCESS_CONFIG)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def validate_manifest(project_root: str | None = None, path: str = "manifest.json") -> dict[str, Any]:
    """Перевірити структуру SMAPI manifest.json у дозволеному проєкті."""
    return _safe_call(project_operations.validate_manifest, project_root=project_root, path=path, access_config=ACCESS_CONFIG)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def validate_mod_artifact(path: str, project_root: str | None = None) -> dict[str, Any]:
    """Перевірити bounded ZIP artifact, manifest, EntryDll і небезпечні paths/files."""
    return _safe_call(project_operations.validate_mod_artifact, project_root=project_root, path=path, access_config=ACCESS_CONFIG)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def stardew_environment() -> dict[str, Any]:
    """Знайти Stardew/SMAPI через local config або Steam metadata й повернути версії."""
    return _safe_call(project_operations.stardew_environment)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def smapi_log_excerpt(max_lines: int = 120, max_chars: int = 20000) -> dict[str, Any]:
    """Повернути redacted bounded tail останнього знайденого SMAPI log."""
    return _safe_call(project_operations.smapi_log_excerpt, max_lines=max_lines, max_chars=max_chars)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def smapi_mod_status(mod_id: str, max_matches: int = 80) -> dict[str, Any]:
    """Перевірити за останнім SMAPI log, чи згадується/завантажився конкретний mod ID."""
    return _safe_call(project_operations.smapi_mod_status, mod_id=mod_id, max_matches=max_matches)


if codex_agents.control_enabled():
    @mcp.tool(annotations=CONTROL_ANNOTATIONS)
    def codex_agent_create(task: str, project_root: str | None = None, mode: str = "read_only", handoff_from: str | None = None) -> dict[str, Any]:
        """Створити bounded Codex agent у дозволеному workspace без arbitrary shell."""
        return _safe_call(codex_agents.create_agent, task=task, project_root=project_root, mode=mode, handoff_from=handoff_from, access_config=ACCESS_CONFIG)

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def codex_agent_status(agent_id: str, project_root: str | None = None) -> dict[str, Any]:
        """Прочитати збережений lifecycle status Codex agent."""
        return _safe_call(codex_agents.get_status, agent_id=agent_id, workspace_hint=project_root, access_config=ACCESS_CONFIG)

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def codex_agent_result(agent_id: str, project_root: str | None = None, max_chars: int = 20000) -> dict[str, Any]:
        """Отримати bounded фінальний result/handoff завершеного Codex agent."""
        return _safe_call(codex_agents.get_result, agent_id=agent_id, max_chars=max_chars, workspace_hint=project_root, access_config=ACCESS_CONFIG)

    @mcp.tool(annotations=CONTROL_ANNOTATIONS)
    def codex_agent_close(agent_id: str, project_root: str | None = None) -> dict[str, Any]:
        """Коректно зупинити або закрити Codex agent, зберігши result/state."""
        return _safe_call(codex_agents.close_agent, agent_id=agent_id, workspace_hint=project_root, access_config=ACCESS_CONFIG)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def task_brief(
    task: str,
    project_root: str | None = None,
    max_terms: int = 8,
    max_matches_per_term: int = 8,
) -> dict[str, Any]:
    """Повернути пошукові підказки проєкту для задачі, не видаючи їх за остаточний висновок."""

    return _safe_call(
        project_context.task_brief,
        task=task,
        project_root=project_root,
        max_terms=max_terms,
        max_matches_per_term=max_matches_per_term,
        access_config=ACCESS_CONFIG,
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def memory_read(
    name: str = "facts.md",
    project_root: str | None = None,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """Прочитати один обмежений файл пам'яті проєкту з .arvis_mcp_memory/."""

    return _safe_call(
        project_context.memory_read,
        name=name,
        project_root=project_root,
        max_chars=max_chars,
        access_config=ACCESS_CONFIG,
    )


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def system_info() -> dict[str, Any]:
    """Повернути обмежені факти про ОС і runtime без ідентифікаційних чи мережевих даних."""

    return _safe_call(system_context.system_info)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def system_metrics() -> dict[str, Any]:
    """Повернути обмежений read-only знімок CPU, пам'яті, GPU, сховища та uptime без приватних ідентифікаційних даних."""

    return _safe_call(system_context.system_metrics)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def binary_exists(name: str) -> dict[str, Any]:
    """Перевірити валідовану назву executable без його запуску."""

    return _safe_call(system_context.binary_exists, name=name)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def package_installed(name: str) -> dict[str, Any]:
    """Перевірити валідовану назву пакунка в read-only базі RPM хоста."""

    return _safe_call(system_context.package_installed, name=name)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def package_info(name: str) -> dict[str, Any]:
    """Поєднати факти про встановлений RPM із cache-only метаданими репозиторію."""

    return _safe_call(system_context.package_info, name=name)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def package_search(query: str, limit: int = 10) -> dict[str, Any]:
    """Шукати в обмежених cache-only метаданих без оновлення репозиторіїв."""

    return _safe_call(system_context.package_search, query=query, limit=limit)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def plasma_info() -> dict[str, Any]:
    """Повернути обмежені факти про Plasma, KDE Frameworks, Qt і сесію."""

    return _safe_call(system_context.plasma_info)


@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def qml_module_available(module: str) -> dict[str, Any]:
    """Перевірити валідований QML URI через корені імпорту Qt і метадані qmldir."""

    return _safe_call(system_context.qml_module_available, module=module)


def memory_append(
    text: str,
    name: str = "task_history.md",
    project_root: str | None = None,
    source: str = "mcp_client",
) -> dict[str, Any]:
    """Додати одну обмежену нотатку до дозволеного файлу пам'яті проєкту."""

    return _safe_call(
        project_context.memory_append,
        text=text,
        name=name,
        project_root=project_root,
        source=source,
        access_config=ACCESS_CONFIG,
    )


if ACCESS_CONFIG.memory_writes_allowed:
    memory_append = mcp.tool(annotations=MEMORY_WRITE_ANNOTATIONS)(memory_append)


if __name__ == "__main__":
    mcp.run(transport="stdio")
