from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl
from urllib.parse import quote
from urllib.parse import unquote_plus
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit


EVENT_SCHEMA_VERSION = 1
DEFAULT_EVENT_LIMIT = 5
MAX_EVENT_LIMIT = 100
REDACTED_VALUE = "REDACTED"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_PERSONAL_PATH_RE = re.compile(r"(?<!\w)/(?:var/)?home/[^/\s]+/[^\s|]+|(?<!\w)/Users/[^/\s]+/[^\s|]+")
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s|]+", re.IGNORECASE)
_CREDENTIAL_TEXT_RE = re.compile(
    r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+|"
    r"\b(?:access[_-]?token|api[_-]?key|password|session[_-]?id|client[_-]?secret|"
    r"authorization[_-]?header|auth[_-]?header|cookie[_-]?header|session[_-]?cookie|"
    r"credentials?(?:[_-]?blob)?)\s*[=:]\s*[^\s,;]+"
)
_CREDENTIAL_HEADER_RE = re.compile(
    r"(?im)(?P<quote>['\"]?)\b"
    r"(?P<name>set[-_ ]?cookie|cookie|proxy[-_ ]?authorization|authorization)"
    r"(?P=quote)\s*:\s*[^\r\n]*"
)
_SENSITIVE_QUERY_KEYS = {
    "token",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "authtoken",
    "auth",
    "authorization",
    "key",
    "apikey",
    "password",
    "passwd",
    "pwd",
    "code",
    "session",
    "sessionid",
    "sessiontoken",
    "sessionkey",
    "sid",
    "signature",
    "sig",
    "secret",
    "clientsecret",
    "xapikey",
    "authorizationcode",
    "credential",
    "credentials",
    "jwt",
}
_FORBIDDEN_DATA_KEYS = {
    "authorization",
    "proxyauthorization",
    "cookie",
    "cookies",
    "setcookie",
    "headers",
    "requestheaders",
    "responseheaders",
    "credential",
    "credentials",
    "clientsecret",
    "password",
    "passwd",
    "secret",
}
_URL_FIELD_KEYS = {
    "url",
    "pageurl",
    "currenturl",
    "starturl",
    "redirecturl",
    "locationurl",
}
_OPERATIONAL_METADATA_KEYS = {
    "sequence",
    "started_at",
    "suppressed_duplicate",
}
_CORE_METADATA_KEYS = {
    "source",
    "watch_id",
    "profile",
    "url",
    "current_url",
    "page_url",
    "page_title",
    "title",
    "detected_at",
}


@dataclass(frozen=True)
class EventQuery:
    profile: str | None = None
    source: str | None = None
    event_type: str | None = None
    url: str | None = None
    domain: str | None = None
    from_time: datetime | None = None
    to_time: datetime | None = None
    limit: int = DEFAULT_EVENT_LIMIT
    after_event_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            key: value
            for key, value in {
                "profile": self.profile,
                "source": self.source,
                "event_type": self.event_type,
                "url": self.url,
                "domain": self.domain,
                "from": self.from_time.isoformat() if self.from_time else None,
                "to": self.to_time.isoformat() if self.to_time else None,
                "limit": self.limit,
                "after_event_id": self.after_event_id,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class EventLogResult:
    events: list[dict[str, object]]
    matching_events_count: int
    valid_events_count: int
    skipped_records: int
    query: EventQuery
    cursor_found: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "events": self.events,
            "events_count": len(self.events),
            "matching_events_count": self.matching_events_count,
            "valid_events_count": self.valid_events_count,
            "skipped_records": self.skipped_records,
            "filters": self.query.as_dict(),
        }


def sanitize_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""

    hostname = parsed.hostname
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError:
        return ""
    netloc = f"{hostname}:{port}" if port is not None else hostname

    cleaned_query: list[tuple[str, str]] = []
    for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        if _is_sensitive_key(key):
            cleaned_query.append((key, REDACTED_VALUE))
            continue
        nested = _decode_repeatedly(query_value)
        if nested.lower().startswith(("http://", "https://")):
            sanitized_nested = sanitize_url(nested)
            cleaned_query.append((key, sanitized_nested or REDACTED_VALUE))
        else:
            cleaned_query.append((key, query_value))
    return urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            quote(parsed.path, safe="/%:@-._~!$&'()*+,;="),
            urlencode(cleaned_query, doseq=True),
            "",
        )
    )


def sanitize_text(value: object) -> str:
    text = str(value or "")

    def replace_url(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        suffix = ""
        while raw_url and raw_url[-1] in ".,);]":
            suffix = raw_url[-1] + suffix
            raw_url = raw_url[:-1]
        return f"{sanitize_url(raw_url) or '[redacted-url]'}{suffix}"

    text = _URL_IN_TEXT_RE.sub(replace_url, text)
    text = _CREDENTIAL_HEADER_RE.sub(
        lambda match: f"{match.group('name')}: {REDACTED_VALUE}",
        text,
    )
    text = _CREDENTIAL_TEXT_RE.sub(REDACTED_VALUE, text)
    return _PERSONAL_PATH_RE.sub("[redacted-path]", text)


def sanitize_event_data(value: object, project_root: Path | None = None, key: str = "") -> object:
    normalized_key = _normalized_key(key)
    if _is_sensitive_key(key):
        return REDACTED_VALUE
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if normalized_key in _URL_FIELD_KEYS:
            return sanitize_url(value)
        if normalized_key.endswith("path"):
            return _sanitize_path(value, project_root)
        return sanitize_text(value)
    if isinstance(value, list):
        return [
            cleaned
            for item in value
            if (cleaned := sanitize_event_data(item, project_root=project_root, key=key)) is not None
        ]
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for raw_key, item in value.items():
            item_key = str(raw_key)
            sanitized = sanitize_event_data(item, project_root=project_root, key=item_key)
            if sanitized is not None:
                cleaned[item_key] = sanitized
        return cleaned
    return sanitize_text(value)


def event_to_v1_record(event: dict[str, object], project_root: Path) -> dict[str, object]:
    raw_metadata = event.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    profile = str(event.get("profile") or metadata.get("profile") or event.get("watch_id") or "").strip()
    source = str(event.get("source") or metadata.get("source") or "browser_observer").strip()
    page_url = event.get("page_url") or metadata.get("page_url") or metadata.get("current_url") or metadata.get("url")
    page_title = event.get("page_title") or metadata.get("page_title") or metadata.get("title")

    payload = dict(event.get("payload") or {}) if isinstance(event.get("payload"), dict) else {}
    output_metadata: dict[str, object] = {}
    for key, value in metadata.items():
        if key in _CORE_METADATA_KEYS:
            continue
        if key in _OPERATIONAL_METADATA_KEYS:
            output_metadata[key] = value
        elif key == "text":
            payload.setdefault("text_length", len(str(value)))
        else:
            payload.setdefault(key, value)

    coordinates = {
        key: value
        for key, value in {
            "bbox": event.get("bbox"),
            "center": event.get("center"),
        }.items()
        if isinstance(value, dict)
    }
    record: dict[str, object] = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": str(event.get("event_id") or "").strip(),
        "watch_id": str(event.get("watch_id") or "").strip(),
        "profile": profile,
        "timestamp": str(event.get("timestamp") or "").strip(),
        "event_type": str(event.get("event_type") or "").strip(),
        "source": source,
        "message": sanitize_text(event.get("message")),
        "metadata": sanitize_event_data(output_metadata, project_root=project_root, key="metadata"),
    }
    optional = {
        "confidence": event.get("confidence"),
        "page_url": sanitize_url(page_url),
        "page_title": sanitize_text(page_title),
        "region": sanitize_event_data(event.get("region"), project_root=project_root, key="region"),
        "coordinates": sanitize_event_data(coordinates, project_root=project_root, key="coordinates") if coordinates else None,
        "screenshot_path": _sanitize_path(event.get("screenshot_path"), project_root),
        "payload": sanitize_event_data(payload, project_root=project_root, key="payload") if payload else None,
    }
    record.update({key: value for key, value in optional.items() if value not in (None, "", {}, [])})
    return record


def normalize_event_record(payload: object, line_number: int) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None

    schema_version = payload.get("schema_version")
    if schema_version is None:
        return _normalize_legacy_event(payload, line_number)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        return None
    return _normalize_versioned_event(payload, schema_version)


def read_event_log(path: Path, query: EventQuery | None = None) -> EventLogResult:
    active_query = query or EventQuery()
    if not path.exists():
        return EventLogResult([], 0, 0, 0, active_query, cursor_found=active_query.after_event_id is None)

    selected: deque[dict[str, object]] = deque(maxlen=active_query.limit)
    valid_count = 0
    matching_count = 0
    skipped_count = 0
    cursor_found = active_query.after_event_id is None

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if "\ufffd" in line:
                skipped_count += 1
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                skipped_count += 1
                continue
            event = normalize_event_record(payload, line_number)
            if event is None:
                skipped_count += 1
                continue
            valid_count += 1
            if not cursor_found:
                if event["event_id"] == active_query.after_event_id:
                    cursor_found = True
                continue
            if not _event_matches(event, active_query):
                continue
            matching_count += 1
            selected.append(event)

    return EventLogResult(
        list(selected),
        matching_count,
        valid_count,
        skipped_count,
        active_query,
        cursor_found=cursor_found,
    )


def count_valid_events(path: Path) -> int:
    return read_event_log(path, EventQuery(limit=1)).valid_events_count


def parse_event_query(
    params: dict[str, object] | None,
    target: str | None = None,
) -> tuple[EventQuery | None, str | None]:
    values = dict(params or {})
    allowed = {"profile", "source", "event_type", "url", "domain", "from", "to", "limit", "after_event_id"}
    unknown = sorted(set(values) - allowed)
    if unknown:
        return None, f"Непідтримувані фільтри Browser Observer: {', '.join(unknown)}."

    target_profile = (target or "").strip()
    if target_profile == "observer":
        target_profile = ""
    profile = _optional_string(values.get("profile"))
    if profile is None and values.get("profile") is not None:
        return None, "Фільтр profile має бути непорожнім текстовим значенням."
    if profile and target_profile and profile.casefold() != target_profile.casefold():
        return None, "Target профілю конфліктує з фільтром profile."
    profile = profile or target_profile or None

    identifiers: dict[str, str | None] = {"profile": profile}
    for key in ("source", "event_type"):
        raw = values.get(key)
        parsed = _optional_string(raw)
        if parsed is None and raw is not None:
            return None, f"Фільтр {key} має бути непорожнім текстовим значенням."
        identifiers[key] = parsed
    for key, value in identifiers.items():
        if value and (len(value) > 128 or not _IDENTIFIER_RE.fullmatch(value)):
            return None, f"Фільтр {key} має некоректний формат."

    raw_limit = values.get("limit", DEFAULT_EVENT_LIMIT)
    if isinstance(raw_limit, bool):
        return None, f"Limit має бути цілим числом від 1 до {MAX_EVENT_LIMIT}."
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return None, f"Limit має бути цілим числом від 1 до {MAX_EVENT_LIMIT}."
    if limit < 1 or limit > MAX_EVENT_LIMIT:
        return None, f"Limit має бути від 1 до {MAX_EVENT_LIMIT}."

    raw_url = _optional_string(values.get("url"))
    if raw_url is None and values.get("url") is not None:
        return None, "Фільтр url має бути непорожнім HTTP(S) URL."
    url = sanitize_url(raw_url) if raw_url else None
    if raw_url and not url:
        return None, "Фільтр url має бути коректним HTTP(S) URL."

    raw_domain = _optional_string(values.get("domain"))
    if raw_domain is None and values.get("domain") is not None:
        return None, "Фільтр domain має бути непорожнім доменом."
    domain = _normalize_domain(raw_domain) if raw_domain else None
    if raw_domain and not domain:
        return None, "Фільтр domain має некоректний формат."

    from_time, from_error = _parse_filter_time(values.get("from"), "from")
    if from_error:
        return None, from_error
    to_time, to_error = _parse_filter_time(values.get("to"), "to")
    if to_error:
        return None, to_error
    if from_time and to_time and from_time > to_time:
        return None, "Фільтр from не може бути пізніше за to."

    after_event_id = _optional_string(values.get("after_event_id"))
    if after_event_id is None and values.get("after_event_id") is not None:
        return None, "Фільтр after_event_id має бути непорожнім текстовим значенням."
    if after_event_id and (len(after_event_id) > 200 or any(character.isspace() for character in after_event_id)):
        return None, "Фільтр after_event_id має некоректний формат."

    return (
        EventQuery(
            profile=identifiers["profile"],
            source=identifiers["source"],
            event_type=identifiers["event_type"],
            url=url,
            domain=domain,
            from_time=from_time,
            to_time=to_time,
            limit=limit,
            after_event_id=after_event_id,
        ),
        None,
    )


def _normalize_versioned_event(payload: dict[str, object], schema_version: int) -> dict[str, object] | None:
    required_strings = ("event_id", "watch_id", "profile", "timestamp", "event_type", "source", "message")
    if any(not isinstance(payload.get(key), str) or not str(payload.get(key)).strip() for key in required_strings):
        return None
    timestamp = _parse_timestamp(payload["timestamp"])
    if timestamp is None:
        return None
    if not _valid_optional_fields(payload):
        return None

    event = {
        "schema_version": schema_version,
        "event_id": str(payload["event_id"]).strip(),
        "watch_id": str(payload["watch_id"]).strip(),
        "profile": str(payload["profile"]).strip(),
        "timestamp": timestamp.isoformat(),
        "event_type": str(payload["event_type"]).strip(),
        "source": str(payload["source"]).strip(),
        "message": sanitize_text(payload["message"]),
        "metadata": sanitize_event_data(payload["metadata"], key="metadata"),
    }
    for key in ("confidence", "page_title", "region", "coordinates", "screenshot_path", "payload"):
        if key in payload and payload[key] is not None:
            cleaned = sanitize_event_data(payload[key], key=key)
            if cleaned not in (None, "", {}, []):
                event[key] = cleaned
    if payload.get("page_url"):
        event["page_url"] = sanitize_url(payload["page_url"])
    return event


def _normalize_legacy_event(payload: dict[str, object], line_number: int) -> dict[str, object] | None:
    required_strings = ("watch_id", "timestamp", "event_type", "message")
    if any(not isinstance(payload.get(key), str) or not str(payload.get(key)).strip() for key in required_strings):
        return None
    timestamp = _parse_timestamp(payload["timestamp"])
    if timestamp is None or not _valid_optional_fields(payload, legacy=True):
        return None
    metadata = dict(payload["metadata"])
    profile = str(metadata.get("profile") or payload["watch_id"]).strip()
    source = str(metadata.get("source") or "browser_observer").strip()
    if not profile or not source:
        return None
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    event: dict[str, object] = {
        "schema_version": 0,
        "event_id": f"legacy-{line_number}-{digest}",
        "watch_id": str(payload["watch_id"]).strip(),
        "profile": profile,
        "timestamp": timestamp.isoformat(),
        "event_type": str(payload["event_type"]).strip(),
        "source": source,
        "message": sanitize_text(payload["message"]),
        "metadata": sanitize_event_data(
            {key: value for key, value in metadata.items() if key not in _CORE_METADATA_KEYS},
            key="metadata",
        ),
        "legacy": True,
    }
    confidence = payload.get("confidence")
    if confidence is not None:
        event["confidence"] = confidence
    page_url = metadata.get("page_url") or metadata.get("current_url") or metadata.get("url")
    if page_url:
        event["page_url"] = sanitize_url(page_url)
    page_title = metadata.get("page_title") or metadata.get("title")
    if page_title:
        event["page_title"] = sanitize_text(page_title)
    if isinstance(payload.get("region"), dict):
        event["region"] = sanitize_event_data(payload["region"], key="region")
    coordinates = {
        key: payload[key]
        for key in ("bbox", "center")
        if isinstance(payload.get(key), dict)
    }
    if coordinates:
        event["coordinates"] = sanitize_event_data(coordinates, key="coordinates")
    screenshot_path = _sanitize_path(payload.get("screenshot_path"), None)
    if screenshot_path:
        event["screenshot_path"] = screenshot_path
    return event


def _valid_optional_fields(payload: dict[str, object], legacy: bool = False) -> bool:
    confidence = payload.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 <= float(confidence) <= 1.0
    ):
        return False
    for key in ("region", "coordinates", "payload"):
        if key in payload and payload[key] is not None and not isinstance(payload[key], dict):
            return False
    if not legacy:
        for key in ("page_url", "page_title", "screenshot_path"):
            if key in payload and payload[key] is not None and not isinstance(payload[key], str):
                return False
    else:
        for key in ("region", "bbox", "center"):
            if key in payload and payload[key] is not None and not isinstance(payload[key], dict):
                return False
        if "screenshot_path" in payload and payload["screenshot_path"] is not None and not isinstance(payload["screenshot_path"], str):
            return False
    return True


def _event_matches(event: dict[str, object], query: EventQuery) -> bool:
    if query.profile and str(event["profile"]).casefold() != query.profile.casefold():
        return False
    if query.source and str(event["source"]).casefold() != query.source.casefold():
        return False
    if query.event_type and str(event["event_type"]).casefold() != query.event_type.casefold():
        return False
    page_url = str(event.get("page_url") or "")
    if query.url and not page_url.startswith(query.url):
        return False
    if query.domain:
        try:
            hostname = (urlsplit(page_url).hostname or "").casefold()
        except ValueError:
            return False
        if hostname != query.domain and not hostname.endswith(f".{query.domain}"):
            return False
    timestamp = _parse_timestamp(event["timestamp"])
    if timestamp is None:
        return False
    if query.from_time and timestamp < query.from_time:
        return False
    if query.to_time and timestamp > query.to_time:
        return False
    return True


def _parse_filter_time(value: object, name: str) -> tuple[datetime | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip():
        return None, f"Фільтр {name} має бути ISO 8601 timestamp із timezone."
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None, f"Фільтр {name} має бути ISO 8601 timestamp із timezone."
    return parsed, None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _normalize_domain(value: str) -> str:
    candidate = value.strip().casefold().rstrip(".")
    if not candidate or any(character in candidate for character in "/:@?#"):
        return ""
    try:
        ascii_domain = candidate.encode("idna").decode("ascii")
    except UnicodeError:
        return ""
    labels = ascii_domain.split(".")
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
        return ""
    if any(not re.fullmatch(r"[a-z0-9-]+", label) for label in labels):
        return ""
    return ascii_domain


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _sanitize_path(value: object, project_root: Path | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        if project_root is None:
            return None
        try:
            return str(path.resolve().relative_to(project_root.resolve()))
        except ValueError:
            return None
    if ".." in path.parts:
        return None
    return str(path)


def _normalized_key(value: object) -> str:
    decoded = _decode_repeatedly(str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]", "", decoded)


def _is_sensitive_key(value: object) -> bool:
    decoded = _decode_repeatedly(str(value or ""))
    normalized = _normalized_key(decoded)
    if normalized in _FORBIDDEN_DATA_KEYS or normalized in _SENSITIVE_QUERY_KEYS:
        return True

    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", decoded)
    tokens = {
        token.casefold()
        for token in re.split(r"[^A-Za-z0-9]+", separated)
        if token
    }
    direct_tokens = {
        "authorization",
        "authorizations",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "password",
        "passwords",
        "passwd",
        "pwd",
        "secret",
        "secrets",
        "signature",
        "signatures",
        "token",
        "tokens",
        "jwt",
        "jwts",
    }
    if tokens & direct_tokens:
        return True
    if "auth" in tokens and tokens & {"header", "headers", "token", "tokens", "code", "credential", "credentials"}:
        return True
    if "api" in tokens and "key" in tokens:
        return True
    if "client" in tokens and "secret" in tokens:
        return True
    if "session" in tokens and tokens & {"cookie", "cookies", "id", "key", "secret", "token", "tokens"}:
        return True

    strong_parts = (
        "authorization",
        "credential",
        "cookie",
        "password",
        "clientsecret",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "authtoken",
        "sessiontoken",
        "sessionid",
        "sessionkey",
        "apikey",
    )
    return any(part in normalized for part in strong_parts)


def _decode_repeatedly(value: str) -> str:
    decoded = value
    for _ in range(3):
        next_value = unquote_plus(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded
