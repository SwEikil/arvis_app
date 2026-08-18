from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


TERMINAL_STATES = {"completed", "failed", "closed"}
MAX_DISPLAY_CHARS = 4_000


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def update_status(path: Path, **changes) -> dict:
    status = read_json(path)
    status.update(changes)
    status["updated_at"] = now()
    write_json(path, status)
    return status


def safe_child_env() -> dict[str, str]:
    allowed = {"HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "CODEX_HOME"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def render_event(line: str) -> None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return
    if event.get("type") == "thread.started":
        print(f"Codex session: {event.get('thread_id')}", flush=True)
        return
    if event.get("type") != "item.completed" or not isinstance(event.get("item"), dict):
        return
    item = event["item"]
    if item.get("type") == "agent_message":
        print("\n" + str(item.get("text", ""))[:MAX_DISPLAY_CHARS] + "\n", flush=True)
    elif item.get("type") == "command_execution":
        command = str(item.get("command", ""))[:500]
        print(f"[{item.get('status', 'command')}] {command}", flush=True)


def follow_primary(events_path: Path, status_path: Path) -> dict:
    offset = 0
    while True:
        if events_path.is_file():
            with events_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                for line in handle:
                    render_event(line)
                offset = handle.tell()
        status = read_json(status_path)
        if status.get("status") in TERMINAL_STATES:
            return status
        time.sleep(0.25)


def session_id_from_events(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
                    return event["thread_id"]
    except OSError:
        return None
    return None


def find_session_file(session_id: str) -> Path | None:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    sessions = codex_home / "sessions"
    if not sessions.is_dir():
        return None
    matches = list(sessions.rglob(f"*-{session_id}.jsonl"))
    return max(matches, key=lambda path: path.stat().st_mtime) if matches else None


def latest_final_answer(session_id: str) -> str | None:
    session_file = find_session_file(session_id)
    if session_file is None:
        return None
    result = None
    with session_file.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload")
            if event.get("type") != "response_item" or not isinstance(payload, dict):
                continue
            if payload.get("type") != "message" or payload.get("role") != "assistant" or payload.get("phase") != "final_answer":
                continue
            parts = [item.get("text", "") for item in payload.get("content", []) if isinstance(item, dict) and item.get("type") == "output_text"]
            if parts:
                result = "".join(parts)
    return result


def main() -> int:
    request_path = Path(sys.argv[1]).resolve()
    agent_dir = request_path.parent
    request = read_json(request_path)
    status_path = agent_dir / "status.json"
    child: subprocess.Popen | None = None
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, stop)
    update_status(status_path, visible=True, visibility_state="read_only_viewer", terminal_pid=os.getpid(), same_session=True, user_interaction=False)
    print("Arvis Codex lifecycle viewer", flush=True)
    print("This is the same agent. The live phase is read-only; interaction starts only after the primary run completes.", flush=True)
    status = follow_primary(agent_dir / "events.jsonl", status_path)
    if stopping or status.get("status") == "closed":
        update_status(status_path, visibility_state="closed", user_interaction=False)
        return 0
    session_id = status.get("session_id") or session_id_from_events(agent_dir / "events.jsonl")
    if session_id and status.get("session_id") != session_id:
        status = update_status(status_path, session_id=session_id)
    if status.get("status") != "completed" or not isinstance(session_id, str) or not session_id:
        update_status(status_path, visibility_state="unavailable", user_interaction=False)
        print("The primary agent did not produce a resumable Codex session.", flush=True)
        return 1
    codex = os.getenv("ARVIS_CODEX_EXECUTABLE") or shutil.which("codex")
    if not codex:
        update_status(status_path, visibility_state="unavailable", user_interaction=False)
        print("Codex CLI is unavailable.", flush=True)
        return 127
    sandbox = "read-only" if request["mode"] == "read_only" else "workspace-write"
    argv = [codex, "resume", "--include-non-interactive", "--sandbox", sandbox, "-c", 'approval_policy="never"', "--cd", request["workspace"], session_id]
    update_status(status_path, status="interactive", visibility_state="interactive_same_session", user_interaction=True, result_scope="initial_run")
    print("Opening interactive continuation of the same Codex session. Input is now enabled.", flush=True)
    try:
        child = subprocess.Popen(argv, cwd=request["workspace"], shell=False, close_fds=True, env=safe_child_env())
        code = child.wait()
    except OSError:
        code = 125
    answer = latest_final_answer(session_id)
    if answer is not None:
        (agent_dir / "result.md").write_text(answer, encoding="utf-8")
    current = read_json(status_path)
    was_closed = stopping or current.get("status") in {"stopping", "closed"}
    update_status(
        status_path,
        status="closed" if was_closed else ("completed" if code == 0 else "failed"),
        outcome="closed" if was_closed else ("completed" if code == 0 else "failed"),
        interactive_exit_code=code,
        visibility_state="closed",
        user_interaction=False,
        result_scope="latest_session" if answer is not None else current.get("result_scope", "initial_run"),
        finished_at=now(),
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
