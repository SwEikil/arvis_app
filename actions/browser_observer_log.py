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
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid4
from uuid import uuid5


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
    event_types: tuple[str, ...] | None = None
    since: datetime | None = None
    until: datetime | None = None
    site: str | None = None
    url_prefix: str | None = None
    limit: int = DEFAULT_EVENT_LIMIT
    after_event_id: str | None = None
    after_position: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "profile": sanitize_text(self.profile) if self.profile is not None else None,
            "event_types": (
                [sanitize_text(event_type) for event_type in self.event_types]
                if self.event_types is not None
                else None
            ),
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat() if self.until else None,
            "site": self.site,
            "url_prefix": sanitize_url(self.url_prefix) if self.url_prefix is not None else None,
            "limit": self.limit,
            "after_event_id": (
                sanitize_text(self.after_event_id) if self.after_event_id is not None else None
            ),
            "after_position": self.after_position,
        }


@dataclass(frozen=True)
class EventLogResult:
    events: list[dict[str, object]]
    matched_count: int
    valid_events_count: int
    legacy_events_count: int
    invalid_events_count: int
    unsupported_events_count: int
    query: EventQuery
    next_position: int
    cursor_found: bool = True

    @property
    def skipped_records(self) -> int:
        return self.invalid_events_count + self.unsupported_events_count

    @property
    def matching_events_count(self) -> int:
        return self.matched_count

    @property
    def returned_count(self) -> int:
        return len(self.events)

    @property
    def truncated(self) -> bool:
        return self.matched_count > self.returned_count

    def as_dict(self) -> dict[str, object]:
        cleaned_events = [
            cleaned
            for event in self.events
            if isinstance(cleaned := sanitize_event_data(event, key="event"), dict)
        ]
        return {
            "events": cleaned_events,
            "returned_count": len(cleaned_events),
            "matched_count": self.matched_count,
            "events_count": len(cleaned_events),
            "matching_events_count": self.matched_count,
            "valid_events_count": self.valid_events_count,
            "legacy_events_count": self.legacy_events_count,
            "invalid_events_count": self.invalid_events_count,
            "unsupported_events_count": self.unsupported_events_count,
            "skipped_records": self.skipped_records,
            "next_position": self.next_position,
            "truncated": self.truncated,
            "diagnostics": {
                "valid": self.valid_events_count,
                "legacy": self.legacy_events_count,
                "invalid": self.invalid_events_count,
                "unsupported": self.unsupported_events_count,
            },
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
    raw_page = event.get("page")
    page = dict(raw_page) if isinstance(raw_page, dict) else {}
    page_url = (
        page.get("url")
        or event.get("page_url")
        or metadata.get("page_url")
        or metadata.get("current_url")
        or metadata.get("url")
    )
    page_title = page.get("title") or event.get("page_title") or metadata.get("page_title") or metadata.get("title")

    raw_payload = event.get("payload")
    payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
    output_metadata: dict[str, object] = {}
    for key, value in metadata.items():
        if key in _CORE_METADATA_KEYS:
            continue
        if key in _OPERATIONAL_METADATA_KEYS:
            output_metadata[key] = value
        else:
            payload.setdefault(key, value)

    for key in ("region", "bbox", "center"):
        value = event.get(key)
        if isinstance(value, dict):
            payload.setdefault(key, value)
    coordinates = event.get("coordinates")
    if isinstance(coordinates, dict):
        for key, value in coordinates.items():
            payload.setdefault(str(key), value)
    screenshot_path = _sanitize_path(event.get("screenshot_path"), project_root)
    if screenshot_path:
        payload.setdefault("screenshot_path", screenshot_path)

    timestamp = _parse_timestamp(event.get("timestamp"))
    record: dict[str, object] = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": _writer_event_id(event.get("event_id")),
        "watch_id": str(event.get("watch_id") or "").strip(),
        "profile": profile,
        "timestamp": timestamp.isoformat() if timestamp is not None else "",
        "event_type": str(event.get("event_type") or "").strip(),
        "source": source,
        "payload": sanitize_event_data(payload, project_root=project_root, key="payload"),
    }
    sanitized_page = {
        key: value
        for key, value in {
            "url": sanitize_url(page_url),
            "title": sanitize_text(page_title),
        }.items()
        if value
    }
    message = sanitize_text(event.get("message"))
    cleaned_metadata = sanitize_event_data(output_metadata, project_root=project_root, key="metadata")
    if sanitized_page:
        record["page"] = sanitized_page
    if message:
        record["message"] = message
    if event.get("confidence") is not None:
        record["confidence"] = event["confidence"]
    if cleaned_metadata:
        record["metadata"] = cleaned_metadata
    return record


def normalize_event_record(payload: object, line_number: int) -> dict[str, object] | None:
    event, _ = _classify_event_record(payload, line_number)
    return event


def read_event_log(path: Path, query: EventQuery | None = None) -> EventLogResult:
    active_query = query or EventQuery()
    if not path.exists():
        return EventLogResult(
            [],
            0,
            0,
            0,
            0,
            0,
            active_query,
            0,
            cursor_found=active_query.after_event_id is None,
        )

    selected: deque[dict[str, object]] = deque(maxlen=active_query.limit)
    valid_count = 0
    legacy_count = 0
    matching_count = 0
    invalid_count = 0
    unsupported_count = 0
    cursor_found = active_query.after_event_id is None
    next_position = 0

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            next_position = line_number
            line = raw_line.strip()
            if not line:
                invalid_count += 1
                continue
            if "\ufffd" in line:
                invalid_count += 1
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                invalid_count += 1
                continue
            event, classification = _classify_event_record(payload, line_number)
            if event is None:
                if classification == "unsupported":
                    unsupported_count += 1
                else:
                    invalid_count += 1
                continue
            valid_count += 1
            if classification == "legacy":
                legacy_count += 1
            if active_query.after_position is not None and line_number <= active_query.after_position:
                continue
            if not cursor_found:
                if event["event_id"] == active_query.after_event_id:
                    cursor_found = True
                continue
            if not _event_matches(event, active_query):
                continue
            matching_count += 1
            selected.append(event)

    return EventLogResult(
        sorted(selected, key=lambda event: str(event["timestamp"])),
        matching_count,
        valid_count,
        legacy_count,
        invalid_count,
        unsupported_count,
        active_query,
        next_position,
        cursor_found=cursor_found,
    )


def count_valid_events(path: Path) -> int:
    return read_event_log(path, EventQuery(limit=1)).valid_events_count


def parse_event_query(
    params: dict[str, object] | None,
    target: str | None = None,
) -> tuple[EventQuery | None, str | None]:
    values = dict(params or {})
    allowed = {
        "profile",
        "event_types",
        "since",
        "until",
        "site",
        "url_prefix",
        "limit",
        "after_event_id",
        "after_position",
    }
    unknown = sorted(set(values) - allowed)
    if unknown:
        return None, "Передано невідомий фільтр Browser Observer."

    target_profile = (target or "").strip()
    if target_profile == "observer":
        target_profile = ""
    profile = _optional_string(values.get("profile"))
    if profile is None and values.get("profile") is not None:
        return None, "Фільтр profile має бути непорожнім текстовим значенням."
    if profile and target_profile and profile != target_profile:
        return None, "Target профілю конфліктує з фільтром profile."
    profile = profile or target_profile or None

    if profile and (len(profile) > 128 or not _IDENTIFIER_RE.fullmatch(profile)):
        return None, "Фільтр profile має некоректний формат."

    event_types, event_types_error = _parse_event_types(values.get("event_types"))
    if event_types_error:
        return None, event_types_error

    raw_limit = values.get("limit", DEFAULT_EVENT_LIMIT)
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        return None, f"Limit має бути цілим числом від 1 до {MAX_EVENT_LIMIT}."
    limit = raw_limit
    if limit < 1 or limit > MAX_EVENT_LIMIT:
        return None, f"Limit має бути від 1 до {MAX_EVENT_LIMIT}."

    raw_url_prefix = _optional_string(values.get("url_prefix"))
    if raw_url_prefix is None and values.get("url_prefix") is not None:
        return None, "Фільтр url_prefix має бути непорожнім HTTP(S) URL."
    url_prefix = sanitize_url(raw_url_prefix) if raw_url_prefix else None
    if raw_url_prefix and not url_prefix:
        return None, "Фільтр url_prefix має бути коректним HTTP(S) URL."

    raw_site = _optional_string(values.get("site"))
    if raw_site is None and values.get("site") is not None:
        return None, "Фільтр site має бути непорожнім hostname."
    site = _normalize_domain(raw_site) if raw_site else None
    if raw_site and not site:
        return None, "Фільтр site має бути коректним hostname без схеми, порту або шляху."

    since, since_error = _parse_filter_time(values.get("since"), "since")
    if since_error:
        return None, since_error
    until, until_error = _parse_filter_time(values.get("until"), "until")
    if until_error:
        return None, until_error
    if since and until and since >= until:
        return None, "Фільтр since має бути раніше за until."

    after_event_id = _optional_string(values.get("after_event_id"))
    if after_event_id is None and values.get("after_event_id") is not None:
        return None, "Фільтр after_event_id має бути непорожнім текстовим значенням."
    if after_event_id and (len(after_event_id) > 200 or not _IDENTIFIER_RE.fullmatch(after_event_id)):
        return None, "Фільтр after_event_id має некоректний формат."

    raw_after_position = values.get("after_position")
    if raw_after_position is None:
        after_position = None
    elif isinstance(raw_after_position, bool) or not isinstance(raw_after_position, int):
        return None, "Фільтр after_position має бути цілим невід'ємним номером рядка."
    elif raw_after_position < 0:
        return None, "Фільтр after_position має бути цілим невід'ємним номером рядка."
    else:
        after_position = raw_after_position
    if after_event_id is not None and after_position is not None:
        return None, "Фільтри after_event_id та after_position не можна використовувати разом."

    return (
        EventQuery(
            profile=profile,
            event_types=event_types,
            since=since,
            until=until,
            site=site,
            url_prefix=url_prefix,
            limit=limit,
            after_event_id=after_event_id,
            after_position=after_position,
        ),
        None,
    )


def _classify_event_record(
    payload: object,
    line_number: int,
) -> tuple[dict[str, object] | None, str]:
    if not isinstance(payload, dict):
        return None, "invalid"
    schema_version = payload.get("schema_version")
    if schema_version is None:
        event = _normalize_legacy_event(payload, line_number)
        return event, "legacy" if event is not None else "invalid"
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        return None, "invalid"
    if schema_version > EVENT_SCHEMA_VERSION:
        return None, "unsupported"
    if schema_version != EVENT_SCHEMA_VERSION:
        return None, "invalid"
    event = _normalize_versioned_event(payload)
    return event, "valid" if event is not None else "invalid"


def _normalize_versioned_event(payload: dict[str, object]) -> dict[str, object] | None:
    required_strings = ("event_id", "watch_id", "profile", "timestamp", "event_type", "source")
    if any(not isinstance(payload.get(key), str) or not str(payload.get(key)).strip() for key in required_strings):
        return None
    timestamp = _parse_timestamp(payload["timestamp"])
    if timestamp is None:
        return None
    if not _valid_v1_fields(payload):
        return None

    raw_payload = payload["payload"]
    assert isinstance(raw_payload, dict)
    event: dict[str, object] = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "event_id": str(payload["event_id"]).strip(),
        "watch_id": str(payload["watch_id"]).strip(),
        "profile": str(payload["profile"]).strip(),
        "timestamp": timestamp.isoformat(),
        "event_type": str(payload["event_type"]).strip(),
        "source": str(payload["source"]).strip(),
        "payload": sanitize_event_data(raw_payload, key="payload"),
    }
    raw_page = payload.get("page")
    if isinstance(raw_page, dict):
        page = {
            key: value
            for key, value in {
                "url": sanitize_url(raw_page.get("url")),
                "title": sanitize_text(raw_page.get("title")),
            }.items()
            if value
        }
        if page:
            event["page"] = page
    if "message" in payload:
        event["message"] = sanitize_text(payload["message"])
    if payload.get("confidence") is not None:
        event["confidence"] = payload["confidence"]
    if isinstance(payload.get("metadata"), dict):
        metadata = sanitize_event_data(payload["metadata"], key="metadata")
        if metadata:
            event["metadata"] = metadata
    return event


def _normalize_legacy_event(payload: dict[str, object], line_number: int) -> dict[str, object] | None:
    required_strings = ("watch_id", "timestamp", "event_type")
    if any(not isinstance(payload.get(key), str) or not str(payload.get(key)).strip() for key in required_strings):
        return None
    timestamp = _parse_timestamp(payload["timestamp"])
    if timestamp is None or not _valid_legacy_fields(payload):
        return None
    raw_metadata = payload.get("metadata")
    if not isinstance(raw_metadata, dict):
        return None
    metadata = dict(raw_metadata)
    profile = str(metadata.get("profile") or payload["watch_id"]).strip()
    source = str(metadata.get("source") or "browser_observer").strip()
    if not profile or not source:
        return None
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    legacy_event = dict(payload)
    legacy_event.update(
        {
            "event_id": str(uuid5(NAMESPACE_URL, f"arvis-browser-observer:{line_number}:{digest}")),
            "profile": profile,
            "source": source,
            "timestamp": timestamp.isoformat(),
        }
    )
    record = event_to_v1_record(legacy_event, Path("."))
    return _normalize_versioned_event(record)


def _valid_v1_fields(payload: dict[str, object]) -> bool:
    if not isinstance(payload.get("payload"), dict):
        return False
    if "metadata" in payload and not isinstance(payload["metadata"], dict):
        return False
    if "page" in payload:
        page = payload["page"]
        if not isinstance(page, dict):
            return False
        for key in ("url", "title"):
            if key in page and not isinstance(page[key], str):
                return False
    if "message" in payload and not isinstance(payload["message"], str):
        return False
    if "confidence" in payload:
        confidence = payload["confidence"]
        if (
            confidence is None
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            return False
    return True


def _valid_legacy_fields(payload: dict[str, object]) -> bool:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if "message" in payload and payload["message"] is not None and not isinstance(payload["message"], str):
        return False
    confidence = payload.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 <= float(confidence) <= 1.0
    ):
        return False
    for key in ("region", "bbox", "center", "payload"):
        if key in payload and payload[key] is not None and not isinstance(payload[key], dict):
            return False
    screenshot_path = payload.get("screenshot_path")
    return screenshot_path is None or isinstance(screenshot_path, str)


def _event_matches(event: dict[str, object], query: EventQuery) -> bool:
    if query.profile and str(event["profile"]) != query.profile:
        return False
    if query.event_types and str(event["event_type"]) not in query.event_types:
        return False
    page = event.get("page")
    page_url = str(page.get("url") or "") if isinstance(page, dict) else ""
    if query.url_prefix and not page_url.startswith(query.url_prefix):
        return False
    if query.site:
        try:
            hostname = (urlsplit(page_url).hostname or "").casefold()
        except ValueError:
            return False
        if hostname != query.site and not hostname.endswith(f".{query.site}"):
            return False
    timestamp = _parse_timestamp(event["timestamp"])
    if timestamp is None:
        return False
    if query.since and timestamp < query.since:
        return False
    if query.until and timestamp >= query.until:
        return False
    return True


def _parse_event_types(value: object) -> tuple[tuple[str, ...] | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, (list, tuple)):
        raw_values = list(value)
    else:
        return None, "Фільтр event_types має містити один або кілька точних типів подій."
    parsed: list[str] = []
    for raw in raw_values:
        item = _optional_string(raw)
        if item is None or len(item) > 128 or not _IDENTIFIER_RE.fullmatch(item):
            return None, "Фільтр event_types має містити один або кілька точних типів подій."
        if item not in parsed:
            parsed.append(item)
    if not parsed:
        return None, "Фільтр event_types має містити один або кілька точних типів подій."
    return tuple(parsed), None


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


def _writer_event_id(value: object) -> str:
    raw = str(value or "").strip()
    if raw:
        try:
            return str(UUID(raw))
        except ValueError:
            pass
    return str(uuid4())


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
