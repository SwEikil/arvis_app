from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from conversation_summary import HARD_REQUEST_CHARACTERS
from conversation_summary import build_context_messages
from conversation_summary import is_valid_session_id
from conversation_summary import request_character_count
from conversation_summary import validate_reload_history
from conversation_summary import validate_reload_summary


RUNTIME_DIR = Path(".runtime")
RELOAD_STATE_FILE = RUNTIME_DIR / "reload_state.json"


def save_reload_state(
    *,
    dry_run: bool,
    debug: bool,
    session_id: str,
    session_summary: str,
    active_history: list[dict[str, str]] | None = None,
    command_history: list[dict[str, object]] | None = None,
    command_counter: int | None = None,
) -> bool:
    try:
        history = active_history if active_history is not None else []
        commands = command_history if command_history is not None else []
        validated_history = validate_reload_history(history)
        validated_summary = validate_reload_summary(session_summary)
        invalid_state = (
            not isinstance(dry_run, bool)
            or not isinstance(debug, bool)
            or not is_valid_session_id(session_id)
            or validated_history is None
            or validated_summary is None
            or not isinstance(commands, list)
            or len(commands) > 10
            or any(not isinstance(item, dict) for item in commands)
            or (
                command_counter is not None
                and (
                    not isinstance(command_counter, int)
                    or isinstance(command_counter, bool)
                    or command_counter < 0
                )
            )
            or request_character_count(build_context_messages(validated_history, validated_summary))
            > HARD_REQUEST_CHARACTERS
        )
    except Exception:
        return False
    if invalid_state:
        return False

    state: dict[str, object] = {
        "dry_run": dry_run,
        "debug": debug,
        "session_id": session_id,
        "session_summary": validated_summary,
    }
    if active_history is not None:
        state["active_history"] = validated_history
    if command_history is not None:
        state["command_history"] = command_history
    if command_counter is not None:
        state["command_counter"] = command_counter

    temporary_file = RUNTIME_DIR / f".reload_state.{uuid4().hex}.tmp"
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            RUNTIME_DIR.chmod(0o700)
        except Exception:
            pass
        descriptor = os.open(temporary_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_file, RELOAD_STATE_FILE)
        try:
            RELOAD_STATE_FILE.chmod(0o600)
        except Exception:
            pass
    except Exception:
        try:
            temporary_file.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            RELOAD_STATE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return False

    return True


def load_reload_state() -> dict[str, object] | None:
    if not RELOAD_STATE_FILE.exists():
        return None

    try:
        raw_state: Any = json.loads(RELOAD_STATE_FILE.read_text(encoding="utf-8"))
        validated = _validate_reload_state(raw_state)
        if validated is None:
            return None
        return validated
    except Exception:
        return None
    finally:
        try:
            RELOAD_STATE_FILE.unlink(missing_ok=True)
        except Exception:
            pass


def _validate_reload_state(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    session_id = value.get("session_id")
    summary = validate_reload_summary(value.get("session_summary"))
    history = validate_reload_history(value.get("active_history", []))
    if not is_valid_session_id(session_id) or summary is None or history is None:
        return None
    if request_character_count(build_context_messages(history, summary)) > HARD_REQUEST_CHARACTERS:
        return None
    dry_run = value.get("dry_run")
    debug = value.get("debug")
    if not isinstance(dry_run, bool) or not isinstance(debug, bool):
        return None

    result: dict[str, object] = {
        "dry_run": dry_run,
        "debug": debug,
        "session_id": session_id,
        "session_summary": summary,
        "active_history": history,
    }
    command_history = value.get("command_history", [])
    if not isinstance(command_history, list) or len(command_history) > 10:
        return None
    if any(not isinstance(item, dict) for item in command_history):
        return None
    result["command_history"] = [dict(item) for item in command_history]
    command_counter = value.get("command_counter", 0)
    if not isinstance(command_counter, int) or isinstance(command_counter, bool) or command_counter < 0:
        return None
    result["command_counter"] = command_counter
    return result


def restart_current_process() -> None:
    print("Reloading Arvis...", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    python = sys.executable
    args = [python] + sys.argv
    os.execv(python, args)
