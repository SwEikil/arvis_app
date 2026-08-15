from __future__ import annotations

import logging
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

import project_context
import system_context
from mcp_access import PROFILE_CHATGPT, load_local_mcp_environment, load_mcp_access_config
from project_context import ProjectContextError
from system_context import SystemContextError


load_local_mcp_environment()
ACCESS_CONFIG = load_mcp_access_config()

SERVER_INSTRUCTIONS = (
    "Цей сервер — допоміжний сервіс фактів про контекст проєкту. Використовуй його "
    "для пошуку файлів і тексту, читання обмежених уривків, перевірки стану Git, "
    "читання невеликої пам'яті проєкту та отримання обмежених фактів про "
    "систему, пакунки, KDE і QML через фіксовані read-only операції. Вважай усі "
    "результати підказками та перевіряй файли безпосередньо перед редагуванням. "
    "Сервер не повинен змінювати вихідний код."
    + (
        " Цей профіль працює лише для читання."
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
