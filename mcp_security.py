from __future__ import annotations

import re


REDACTED_VALUE = "[REDACTED]"

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_PRIVATE_KEY_BEGIN_RE = re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----", re.IGNORECASE)
_PRIVATE_KEY_END_RE = re.compile(r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----", re.IGNORECASE)
_AUTHORIZATION_RE = re.compile(
    r"(?im)(?P<prefix>\b(?:proxy[- ]?authorization|authorization)\s*[:=]\s*)[^\r\n]+"
)
_COOKIE_HEADER_RE = re.compile(
    r"(?im)(?P<prefix>\b(?:set[- ]?cookie|cookie|session[_ -]?cookie)\s*:\s*)[^\r\n]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{4,}")
_STANDALONE_CREDENTIAL_RE = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})\b"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)(?P<prefix>\b(?:password|passwd|pwd|api[_ -]?key|apiKey|access[_ -]?token|"
    r"refresh[_ -]?token|auth[_ -]?token|client[_ -]?secret|clientSecret|session[_ -]?token|"
    r"cookie|token)\b\s*[:=]\s*)(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s\r\n,;#]+)"
)
_SAFE_CODE_VALUE_RE = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_.]*(?:\([^\r\n]*\))?|\$\{[A-Za-z_][A-Za-z0-9_]*\}|"
    r"<[^>]+>|\[[A-Z_]+\])"
)
_SAFE_WORDS = {
    "false",
    "none",
    "null",
    "placeholder",
    "redacted",
    "str",
    "string",
    "true",
    "your_key",
    "your_token",
}


def redact_sensitive_text(text: str) -> str:
    """Редагувати очевидні credentials, зберігаючи звичайний текст коду."""

    redacted = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", text)
    redacted = _AUTHORIZATION_RE.sub(lambda match: f"{match.group('prefix')}{REDACTED_VALUE}", redacted)
    redacted = _COOKIE_HEADER_RE.sub(lambda match: f"{match.group('prefix')}{REDACTED_VALUE}", redacted)
    redacted = _BEARER_RE.sub(f"Bearer {REDACTED_VALUE}", redacted)
    redacted = _STANDALONE_CREDENTIAL_RE.sub(REDACTED_VALUE, redacted)
    return _SECRET_ASSIGNMENT_RE.sub(_redact_assignment, redacted)


def redact_sensitive_lines(lines: list[str]) -> list[str]:
    """Редагувати credentials зі збереженням номерів рядків у результатах пошуку."""

    result: list[str] = []
    inside_private_key = False
    for line in lines:
        starts_private_key = bool(_PRIVATE_KEY_BEGIN_RE.search(line))
        if starts_private_key:
            inside_private_key = True
        if inside_private_key:
            suffix = "\n" if line.endswith("\n") else ""
            result.append(f"[REDACTED_PRIVATE_KEY]{suffix}")
        else:
            result.append(redact_sensitive_text(line))
        if inside_private_key and _PRIVATE_KEY_END_RE.search(line):
            inside_private_key = False
    return result


def _redact_assignment(match: re.Match[str]) -> str:
    raw_value = match.group("value")
    value = raw_value.strip()
    is_quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}
    unquoted = value[1:-1].strip() if is_quoted else value
    normalized = unquoted.casefold()

    if not unquoted or normalized in _SAFE_WORDS or normalized.startswith(("example", "your_")):
        return match.group(0)
    if not is_quoted and (" " in unquoted or "\t" in unquoted):
        return match.group(0)
    safe_code_value = unquoted.rstrip("),")
    if value == unquoted and (
        _SAFE_CODE_VALUE_RE.fullmatch(unquoted)
        or _SAFE_CODE_VALUE_RE.fullmatch(safe_code_value)
    ):
        return match.group(0)
    return f"{match.group('prefix')}{REDACTED_VALUE}"
