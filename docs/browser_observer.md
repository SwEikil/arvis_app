# Browser Observer

Browser Observer is the public observation-only browser subsystem. It may inspect
configured pages, detect reviewed signals, write events, report status, and notify
the user. It does not click, type, submit forms, navigate arbitrary URLs, attach to
an existing browser profile, or perform gameplay/reward automation.

## Event log

Events are appended to `.runtime/browser_observer/events.jsonl`. New records use
schema version `1`. Existing records are never migrated or rewritten automatically.
Each line is one independent JSON object.

Required v1 fields:

- `schema_version`: integer `1`.
- `event_id`: unique UUID string.
- `watch_id`: watcher identifier.
- `profile`: configured observer profile name.
- `timestamp`: timezone-aware ISO 8601 timestamp in UTC.
- `event_type`: non-empty event type. Readers must tolerate unknown types.
- `source`: event source such as `browser_observer` or `background_watch`.
- `message`: short event description.
- `metadata`: object containing only additional lifecycle metadata.

Optional v1 fields are omitted instead of being serialized as `null`:

- `confidence`: number from `0.0` through `1.0`.
- `page_url`: sanitized HTTP(S) URL.
- `page_title`: page title when available.
- `region`: observed viewport region.
- `coordinates`: object with available `bbox` and/or `center` members.
- `screenshot_path`: project-relative debug screenshot path.
- `payload`: detector-specific data such as selector count or change ratio.

Core fields are not duplicated in `metadata`. Detector-specific fields belong in
`payload`; sequence and watcher lifecycle values may remain in `metadata`.

Example:

```json
{"confidence":1.0,"event_id":"82f83a57-3dc1-4ef0-bec8-e29bf55d25fc","event_type":"text_appeared","message":"Configured text appeared.","metadata":{"sequence":3,"started_at":"2026-07-23T09:59:00+00:00"},"page_title":"Example Domain","page_url":"https://example.com/?view=summary","payload":{"count":1,"mode":"text_appeared"},"profile":"text_appeared","schema_version":1,"source":"background_watch","timestamp":"2026-07-23T10:00:00+00:00","watch_id":"text_appeared"}
```

The example contains no user-specific paths, credentials, cookies, or private
configuration.

## Compatibility and damaged records

Readers accept legacy records without `schema_version` when their core fields and
types are valid. Legacy `bbox`/`center`, URL, profile, and source values are
normalized into the internal representation. A stable internal legacy event ID is
derived from the line position and content hash so it can be used as a cursor.

Unknown integer schema versions and unknown non-empty event types do not stop log
reading. They are returned when the common core fields remain structurally valid.

The reader skips invalid JSON, non-object JSON, missing required fields, invalid
timestamps, invalid confidence values, non-object `metadata`, and invalid optional
structures. Skipped records are not included in valid or returned event counts.
The result may report only the number of skipped records; their content is never
echoed.

Reading is streaming and memory-bounded. Filters are applied before the final
limit, and the returned events remain in chronological order.

## URL and credential handling

Before event persistence, status/error construction, and rendering:

- URL fragments and user information are removed.
- Sensitive query values are replaced with `REDACTED`.
- Matching is case-insensitive and handles underscore, hyphen, camel-case, and
  URL-encoded key variants.
- Safe query parameters are preserved.
- Nested encoded HTTP(S) URL values are sanitized recursively.
- Cookie, authorization-header, and credential field contents are replaced with
  `REDACTED`; external absolute paths are omitted.

Sensitive query names include token/access-token variants, auth/authorization,
key/API-key variants, passwords, authorization codes, session identifiers,
signatures, client secrets, credentials, and JWT values.

Raw URLs are used only for runtime allowlist checks. Sanitized URLs are used for
logs, status, errors, and user output.

## Event filters

`browser_watch_events` accepts these `ActionIntent.params` keys:

- `profile`
- `source`
- `event_type`
- `url`: sanitized URL prefix.
- `domain`: exact hostname or any of its subdomains.
- `from`: inclusive timezone-aware ISO 8601 timestamp.
- `to`: inclusive timezone-aware ISO 8601 timestamp.
- `limit`: integer from `1` through `100`, default `5`.
- `after_event_id`: exclusive stable cursor.

All supplied filters are combined with AND semantics. An unknown cursor or invalid
filter returns a Ukrainian validation error and no event result.

The deterministic resolver intentionally supports a narrow syntax:

```text
покажи події спостереження profile=text_appeared event_type=text_appeared domain=example.com limit=10
show watch events source=background_watch after_event_id=82f83a57-3dc1-4ef0-bec8-e29bf55d25fc
```

The existing unfiltered commands continue to work. A non-`observer` action target
continues to act as the profile filter.

## Status and dry-run

`browser_watch_status` returns separate structured `active_watches` and
`completed_watches` arrays. Available watcher fields include profile/status,
timestamps, last event information, valid event count, suppressed event counts,
active filters, limits, sanitized current URL, `last_error`, and `stop_reason`.

`browser_watch_status` and `browser_watch_events` are read-only and execute even
when global dry-run is enabled. Dry-run still previews and blocks
`browser_watch_start`, `browser_watch_stop`, `browser_watch_poll_once`, and every
other modifying Browser Observer operation.

Normal completion reasons such as timeout, explicit stop, shutdown, and test
iteration limits are reported through `stop_reason`, not `last_error`.
