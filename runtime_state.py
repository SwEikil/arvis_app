from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


RUNTIME_DIR = Path(".runtime")
RELOAD_STATE_FILE = RUNTIME_DIR / "reload_state.json"


def save_reload_state(
    *,
    dry_run: bool,
    debug: bool,
    session_summary: str,
    active_history: list[dict[str, str]] | None = None,
    command_history: list[dict[str, object]] | None = None,
    command_counter: int | None = None,
) -> bool:
    state: dict[str, object] = {
        "dry_run": dry_run,
        "debug": debug,
        "session_summary": session_summary,
    }
    if active_history is not None:
        state["active_history"] = active_history
    if command_history is not None:
        state["command_history"] = command_history
    if command_counter is not None:
        state["command_counter"] = command_counter

    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        RELOAD_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError):
        try:
            RELOAD_STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    return True


def load_reload_state() -> dict[str, object] | None:
    if not RELOAD_STATE_FILE.exists():
        return None

    try:
        raw_state: Any = json.loads(RELOAD_STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw_state, dict):
            return None
        return raw_state
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    finally:
        try:
            RELOAD_STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass


def restart_current_process() -> None:
    print("Reloading Arvis...", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    python = sys.executable
    args = [python] + sys.argv
    os.execv(python, args)
