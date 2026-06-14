import json
import re
from typing import Any

from pydantic import ValidationError

from schemas import ActionIntent, MemoryIntent, ParsedAssistantResponse


INTENT_MARKERS = ("ACTION_INTENT:", "MEMORY_INTENT:")


def parse_assistant_response(
    raw_text: str,
    debug: bool = False,
) -> tuple[ParsedAssistantResponse, list[str]]:
    cleaned_text = remove_internal_reasoning(raw_text or "")
    warnings: list[str] = []

    action_payload, text_without_action, action_warning = _extract_json_after_marker(
        cleaned_text,
        "ACTION_INTENT:",
    )
    memory_payload, message_text, memory_warning = _extract_json_after_marker(
        text_without_action,
        "MEMORY_INTENT:",
    )

    action_intent = _build_action_intent(action_payload, warnings)
    memory_intent = _build_memory_intent(memory_payload, warnings)

    if action_warning:
        warnings.append(action_warning)
    if memory_warning:
        warnings.append(memory_warning)

    if debug and warnings:
        warnings = [f"[intent_parser] {warning}" for warning in warnings]

    return (
        ParsedAssistantResponse(
            message=_normalize_message(message_text),
            action_intent=action_intent,
            memory_intent=memory_intent,
        ),
        warnings,
    )


def remove_internal_reasoning(text: str) -> str:
    text = re.sub(
        r"<\s*(think|thinking|reasoning)\s*>.*?<\s*/\s*\1\s*>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"```(?:thinking|reasoning|thoughts?)\s*.*?```",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # If the model uses a final-answer label, prefer everything after it.
    final_answer_match = re.search(
        r"(?im)^\s*(final answer|final|відповідь|фінальна відповідь)\s*:\s*",
        text,
    )
    if final_answer_match:
        text = text[final_answer_match.end() :]

    lines = text.splitlines()
    kept_lines: list[str] = []
    skipping_reasoning_block = False

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()

        if any(marker.lower() in lower for marker in INTENT_MARKERS):
            skipping_reasoning_block = False

        if re.match(r"^(thinking process|thinking|reasoning|thought process|chain of thought)\s*:", stripped, re.I):
            skipping_reasoning_block = True
            continue

        # Drop the immediate paragraph after a reasoning heading, but stop at a blank
        # line so normal assistant text that follows is preserved.
        if skipping_reasoning_block:
            if not stripped:
                skipping_reasoning_block = False
            continue

        kept_lines.append(line)

    return "\n".join(kept_lines).strip()


def _extract_json_after_marker(
    text: str,
    marker: str,
) -> tuple[dict[str, Any] | None, str, str | None]:
    marker_match = re.search(re.escape(marker), text, flags=re.IGNORECASE)
    if not marker_match:
        return None, text, None

    json_start = text.find("{", marker_match.end())
    next_marker_start = _find_next_marker(text, marker_match.end(), exclude=marker)

    if json_start == -1 or (next_marker_start != -1 and next_marker_start < json_start):
        remove_end = next_marker_start if next_marker_start != -1 else len(text)
        return None, (text[: marker_match.start()] + text[remove_end:]).strip(), f"{marker} found without JSON object"

    json_end = _find_json_object_end(text, json_start)
    if json_end == -1:
        remove_end = next_marker_start if next_marker_start != -1 else len(text)
        return None, (text[: marker_match.start()] + text[remove_end:]).strip(), f"{marker} contains invalid JSON"

    raw_json = text[json_start : json_end + 1]
    block_end = json_end + 1
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        payload = None
        warning = f"{marker} JSON decode error: {exc.msg}"
    else:
        warning = None
        if not isinstance(payload, dict):
            payload = None
            warning = f"{marker} JSON must be an object"

    remaining_text = (text[: marker_match.start()] + text[block_end:]).strip()
    return payload, remaining_text, warning


def _find_next_marker(text: str, start: int, exclude: str) -> int:
    indexes = []
    for marker in INTENT_MARKERS:
        if marker.lower() == exclude.lower():
            continue
        match = re.search(re.escape(marker), text[start:], flags=re.IGNORECASE)
        if match:
            indexes.append(start + match.start())
    return min(indexes) if indexes else -1


def _find_json_object_end(text: str, start: int) -> int:
    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index

    return -1


def _build_action_intent(payload: dict[str, Any] | None, warnings: list[str]) -> ActionIntent | None:
    if payload is None:
        return None
    try:
        return ActionIntent(**payload)
    except ValidationError as exc:
        warnings.append(f"ACTION_INTENT validation error: {exc.errors()}")
        return None


def _build_memory_intent(payload: dict[str, Any] | None, warnings: list[str]) -> MemoryIntent | None:
    if payload is None:
        return None
    try:
        return MemoryIntent(**payload)
    except ValidationError as exc:
        warnings.append(f"MEMORY_INTENT validation error: {exc.errors()}")
        return None


def _normalize_message(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or "(Арвіс не повернув видимого повідомлення.)"
