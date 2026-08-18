from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


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


def main() -> int:
    request_path = Path(sys.argv[1]).resolve()
    agent_dir = request_path.parent
    request = json.loads(request_path.read_text(encoding="utf-8"))
    status_path = agent_dir / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    workspace = Path(request["workspace"])
    status.update({
        "status": "running",
        "pid": os.getpid(),
        "worker_pid": os.getpid(),
        "started_at": now(),
        "updated_at": now(),
        "task_received": isinstance(request.get("task"), str) and bool(request["task"]),
        "handoff_received": request.get("handoff_supplied") is True,
        "workspace_accessible": workspace.is_dir() and os.access(workspace, os.R_OK),
    })
    write_json(status_path, status)
    codex = os.getenv("ARVIS_CODEX_EXECUTABLE") or shutil.which("codex")
    if not codex:
        status.update({"status": "failed", "outcome": "codex_unavailable", "finished_at": now(), "updated_at": now()})
        write_json(status_path, status)
        return 127
    sandbox = "read-only" if request["mode"] == "read_only" else "workspace-write"
    argv = [codex, "exec", "--json", "--color", "never", "--sandbox", sandbox, "-c", 'approval_policy="never"', "--cd", request["workspace"], "--output-last-message", str(agent_dir / "result.md"), request["task"]]
    allowed_env = {"HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "CODEX_HOME"}
    child_env = {key: value for key, value in os.environ.items() if key in allowed_env}
    events = (agent_dir / "events.jsonl").open("w", encoding="utf-8")
    errors = (agent_dir / "stderr.log").open("w", encoding="utf-8")
    process: subprocess.Popen | None = None

    def stop(_signum, _frame):
        nonlocal process
        if process is not None and process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        process = subprocess.Popen(argv, cwd=request["workspace"], shell=False, stdin=subprocess.DEVNULL, stdout=events, stderr=errors, text=True, close_fds=True, env=child_env)
        code = process.wait()
    except Exception:
        code = 125
    finally:
        events.close()
        errors.close()
    current = json.loads(status_path.read_text(encoding="utf-8"))
    requested_stop = current.get("status") == "stopping"
    outcome = "closed" if requested_stop else ("completed" if code == 0 else "failed")
    current.update({"status": outcome, "outcome": outcome, "exit_code": code, "session_id": session_id_from_events(agent_dir / "events.jsonl"), "result_scope": "initial_run", "finished_at": now(), "updated_at": now()})
    write_json(status_path, current)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
