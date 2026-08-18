from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_access import McpAccessConfig, path_is_within
from mcp_security import redact_sensitive_text
from project_context import ProjectContextError, require_writable_project_root, resolve_project_root


MAX_TASK_CHARS = 40_000
MAX_RESULT_CHARS = 50_000
MAX_HANDOFF_CHARS = 30_000
AGENT_ID = re.compile(r"^[0-9a-f]{32}$")


def control_enabled() -> bool:
    return os.getenv("ARVIS_CODEX_AGENT_CONTROL_ENABLED", "").strip().casefold() in {"1", "true", "yes", "on"}


def visible_terminal_enabled() -> bool:
    return os.getenv("ARVIS_CODEX_VISIBLE_TERMINAL_ENABLED", "").strip().casefold() in {"1", "true", "yes", "on"}


def create_agent(task: str, project_root: str | None, mode: str = "read_only", handoff_from: str | None = None, handoff_text: str | None = None, visible: bool = False, *, access_config: McpAccessConfig) -> dict[str, Any]:
    if not control_enabled():
        raise ProjectContextError("Codex agent control не ввімкнено локально.")
    if not task or len(task) > MAX_TASK_CHARS:
        raise ProjectContextError("Задача агента порожня або перевищує ліміт.")
    if mode not in {"read_only", "workspace_write"}:
        raise ProjectContextError("mode має бути read_only або workspace_write.")
    if handoff_text is not None and (not handoff_text.strip() or len(handoff_text) > MAX_HANDOFF_CHARS):
        raise ProjectContextError("Initial handoff порожній або перевищує ліміт.")
    if handoff_from and handoff_text is not None:
        raise ProjectContextError("Використовуй handoff_from або handoff_text, але не обидва.")
    workspace = resolve_project_root(project_root, access_config=access_config)
    if mode == "workspace_write":
        require_writable_project_root(workspace, access_config=access_config)
    state_root = _state_root(workspace)
    parent_result = None
    if handoff_from:
        parent = _agent_dir(state_root, handoff_from)
        parent_status = _read_json(parent / "status.json")
        if parent_status.get("status") not in {"completed", "failed", "closed"} or not (parent / "result.md").is_file():
            raise ProjectContextError("Попередній агент ще не має збереженого handoff/result.")
        parent_result = (parent / "result.md").read_text(encoding="utf-8", errors="replace")[:MAX_RESULT_CHARS]
    agent_id = uuid.uuid4().hex
    agent_dir = state_root / agent_id
    agent_dir.mkdir(mode=0o700)
    preserved_handoff = parent_result if parent_result is not None else handoff_text
    prompt = task if preserved_handoff is None else f"Continue from this preserved handoff/result:\n\n{preserved_handoff}\n\nNew task:\n{task}"
    request = {"agent_id": agent_id, "workspace": str(workspace), "task": prompt, "mode": mode, "handoff_from": handoff_from, "handoff_supplied": preserved_handoff is not None, "visible_requested": bool(visible), "created_at": _now()}
    _write_json(agent_dir / "request.json", request)
    _write_json(agent_dir / "status.json", {"agent_id": agent_id, "status": "initializing", "outcome": None, "pid": None, "worker_pid": None, "created_at": request["created_at"], "updated_at": _now(), "handoff_from": handoff_from, "visible": False, "visibility_state": "not_requested"})
    worker = Path(__file__).with_name("codex_agent_worker.py")
    try:
        process = subprocess.Popen([sys.executable, str(worker), str(agent_dir / "request.json")], cwd=Path(__file__).parent, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError as exc:
        _write_json(agent_dir / "status.json", {"agent_id": agent_id, "status": "failed", "outcome": "launch_failed", "pid": None, "created_at": request["created_at"], "updated_at": _now(), "handoff_from": handoff_from})
        raise ProjectContextError("Не вдалося запустити Codex agent worker.") from exc
    # Record the process group immediately so close_agent is safe even before the
    # worker has completed its own initialization.
    status = _read_json(agent_dir / "status.json")
    status.update({"pid": process.pid, "worker_pid": process.pid, "updated_at": _now()})
    _write_json(agent_dir / "status.json", status)
    visibility: dict[str, Any] = {"requested": bool(visible), "opened": False, "mode": "background"}
    if visible:
        try:
            visibility = _launch_terminal(agent_dir, workspace)
        except ProjectContextError as exc:
            status = _read_json(agent_dir / "status.json")
            status.update({"visible": False, "visibility_state": "launch_failed", "updated_at": _now()})
            _write_json(agent_dir / "status.json", status)
            visibility = {"requested": True, "opened": False, "mode": "background", "error": str(exc)}
    return {"agent_id": agent_id, "status": "initializing", "worker_pid": process.pid, "handoff_from": handoff_from, "handoff_supplied": preserved_handoff is not None, "visibility": visibility}


def get_status(agent_id: str, *, workspace_hint: str | None, access_config: McpAccessConfig) -> dict[str, Any]:
    state_root = _state_root(resolve_project_root(workspace_hint, access_config=access_config))
    status = _read_json(_agent_dir(state_root, agent_id) / "status.json")
    return _public_status(status)


def get_result(agent_id: str, max_chars: int = 20_000, *, workspace_hint: str | None, access_config: McpAccessConfig) -> dict[str, Any]:
    state_root = _state_root(resolve_project_root(workspace_hint, access_config=access_config))
    agent_dir = _agent_dir(state_root, agent_id)
    status = _read_json(agent_dir / "status.json")
    limit = min(max(int(max_chars), 500), MAX_RESULT_CHARS)
    result_path = agent_dir / "result.md"
    content = result_path.read_text(encoding="utf-8", errors="replace") if result_path.is_file() else ""
    return {**_public_status(status), "result": redact_sensitive_text(content[:limit]), "result_available": result_path.is_file(), "truncated": len(content) > limit}


def close_agent(agent_id: str, *, workspace_hint: str | None, access_config: McpAccessConfig) -> dict[str, Any]:
    state_root = _state_root(resolve_project_root(workspace_hint, access_config=access_config))
    agent_dir = _agent_dir(state_root, agent_id)
    status_path = agent_dir / "status.json"
    status = _read_json(status_path)
    previous = status.get("status")
    worker_pid = status.get("worker_pid", status.get("pid"))
    terminal_pid = status.get("terminal_pid")
    active = previous in {"initializing", "running", "stopping", "interactive"}
    if active:
        status["status"] = "stopping"
        status["updated_at"] = _now()
        _write_json(status_path, status)
        if previous in {"initializing", "running", "stopping"} and isinstance(worker_pid, int) and worker_pid > 1:
            try:
                os.killpg(worker_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                raise ProjectContextError("Немає дозволу зупинити agent worker.") from exc
        if isinstance(terminal_pid, int) and terminal_pid > 1:
            try:
                os.kill(terminal_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                raise ProjectContextError("Немає дозволу зупинити visible agent terminal.") from exc
    else:
        status["status"] = "closed"
        status["outcome"] = status.get("outcome") or previous
        status["updated_at"] = _now()
        _write_json(status_path, status)
    return {"agent_id": agent_id, "previous_status": previous, "status": status["status"], "result_preserved": (agent_dir / "result.md").is_file()}


def show_agent(agent_id: str, *, workspace_hint: str | None, access_config: McpAccessConfig) -> dict[str, Any]:
    workspace = resolve_project_root(workspace_hint, access_config=access_config)
    state_root = _state_root(workspace)
    agent_dir = _agent_dir(state_root, agent_id)
    request = _read_json(agent_dir / "request.json")
    if Path(request.get("workspace", "")).resolve() != workspace:
        raise ProjectContextError("Agent не належить вказаному workspace.")
    status = _read_json(agent_dir / "status.json")
    if status.get("status") == "closed":
        raise ProjectContextError("Закритий agent не можна повторно відкрити у terminal.")
    if status.get("visibility_state") in {"launching", "read_only_viewer", "interactive_same_session"}:
        return {"agent_id": agent_id, "opened": False, "already_visible": True, "visibility_state": status.get("visibility_state"), "same_session": True, "user_interaction": status.get("user_interaction") is True}
    return {"agent_id": agent_id, **_launch_terminal(agent_dir, workspace)}


def _state_root(workspace: Path) -> Path:
    configured = os.getenv("ARVIS_CODEX_AGENT_STATE_ROOT")
    if not configured:
        raise ProjectContextError("ARVIS_CODEX_AGENT_STATE_ROOT не налаштовано.")
    root = Path(configured).expanduser().resolve()
    if root == Path(root.anchor) or path_is_within(root, workspace) or path_is_within(workspace, root):
        raise ProjectContextError("Agent state root має бути фізично поза project workspace.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _agent_dir(state_root: Path, agent_id: str) -> Path:
    if not AGENT_ID.fullmatch(agent_id or ""):
        raise ProjectContextError("Некоректний agent ID.")
    path = (state_root / agent_id).resolve()
    if path.parent != state_root or not path.is_dir():
        raise ProjectContextError("Agent не знайдено.")
    return path


def _public_status(status: dict[str, Any]) -> dict[str, Any]:
    return {key: status.get(key) for key in ("agent_id", "status", "outcome", "created_at", "updated_at", "started_at", "finished_at", "handoff_from", "exit_code", "interactive_exit_code", "task_received", "handoff_received", "workspace_accessible", "session_id", "visible", "visibility_state", "same_session", "user_interaction", "result_scope")}


def _launch_terminal(agent_dir: Path, workspace: Path) -> dict[str, Any]:
    if not visible_terminal_enabled():
        raise ProjectContextError("Visible Codex terminal не ввімкнено локально.")
    if not os.getenv("WAYLAND_DISPLAY") and not os.getenv("DISPLAY"):
        raise ProjectContextError("Desktop display недоступний для visible Codex terminal.")
    konsole = shutil.which("konsole")
    if not konsole or Path(konsole).name != "konsole":
        raise ProjectContextError("Konsole недоступний для visible Codex terminal.")
    helper = Path(__file__).with_name("codex_agent_terminal.py")
    status_path = agent_dir / "status.json"
    status = _read_json(status_path)
    status.update({"visible": True, "visibility_state": "launching", "same_session": True, "user_interaction": False, "updated_at": _now()})
    _write_json(status_path, status)
    had_terminal = _konsole_is_running()
    try:
        process = subprocess.Popen(
            [konsole, "--new-tab", "--workdir", str(workspace), "-e", sys.executable, str(helper), str(agent_dir / "request.json")],
            cwd=workspace,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        raise ProjectContextError("Не вдалося відкрити narrowly-scoped Konsole для agent.") from exc
    status = _read_json(status_path)
    status.update({"terminal_launcher_pid": process.pid, "updated_at": _now()})
    _write_json(status_path, status)
    return {"requested": True, "opened": True, "already_visible": False, "same_session": True, "mode": "read_only_then_interactive_resume", "user_interaction": False, "terminal_target": "existing_tab" if had_terminal else "new_window"}


def _konsole_is_running() -> bool:
    proc = Path("/proc")
    try:
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if (entry / "comm").read_text(encoding="utf-8", errors="replace").strip() in {"konsole", "konsole-bin"}:
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectContextError("Стан агента недоступний або пошкоджений.") from exc
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
