# Browser Observer

Browser Observer is the public observation-only browser subsystem. It may inspect
configured pages, detect reviewed signals, write events, report status, and notify
the user. It does not click, type, submit forms, navigate arbitrary URLs, attach to
an existing browser profile, or perform gameplay/reward automation.

## Event log

Events are appended to `.runtime/browser_observer/events.jsonl`. New records use
schema version `1`. Existing records are never migrated or rewritten automatically.
Each line is one independent JSON object.

### Schema v1 fields

| Field | Required | Type | Contract |
| --- | --- | --- | --- |
| `schema_version` | yes | integer | Exactly `1`. |
| `event_id` | yes | string | UUID generated for the event by the writer. |
| `watch_id` | yes | string | Non-empty watcher identifier. |
| `timestamp` | yes | string | Timezone-aware ISO 8601 normalized to UTC. |
| `event_type` | yes | string | Non-empty type; unknown types remain valid. |
| `source` | yes | string | Producer, normally `browser_observer`, `poll_once`, or `background_watch`. |
| `profile` | yes | string | Non-empty configured observer profile name. |
| `payload` | yes | object | Detector-specific data; `{}` is valid. |
| `page` | no | object | Sanitized `url` and/or `title`. |
| `message` | no | string | Short sanitized description. |
| `confidence` | no | number | Value from `0.0` through `1.0`. |
| `metadata` | no | object | Additional watcher/lifecycle data. |

Optional fields are omitted when absent instead of being serialized as `null`.
Core fields are not duplicated in `metadata`. Detector-specific values, viewport
`region`, `bbox`, `center`, and a project-relative `screenshot_path` are stored
inside `payload`.

Full JSONL example:

```json
{"schema_version":1,"event_id":"82f83a57-3dc1-4ef0-bec8-e29bf55d25fc","watch_id":"text_appeared","timestamp":"2026-07-23T10:00:00+00:00","event_type":"text_appeared","source":"background_watch","profile":"text_appeared","page":{"url":"https://example.com/?view=summary","title":"Example Domain"},"message":"Configured text appeared.","confidence":1.0,"payload":{"count":1,"mode":"text_appeared"},"metadata":{"sequence":3,"started_at":"2026-07-23T09:59:00+00:00"}}
```

Minimal JSONL example:

```json
{"schema_version":1,"event_id":"a8ec2aa9-f683-4a61-8a03-e07ee52d8b1d","watch_id":"viewport_change_full","timestamp":"2026-07-23T10:01:00+00:00","event_type":"observer_ready","source":"browser_observer","profile":"viewport_change_full","payload":{}}
```

Viewport event example:

```json
{"schema_version":1,"event_id":"dd7e0ddc-d14c-489c-a564-205643959ca7","watch_id":"viewport_change_full","timestamp":"2026-07-23T10:02:00+00:00","event_type":"viewport_changed","source":"browser_observer","profile":"viewport_change_full","page":{"url":"https://example.com/page","title":"Example"},"message":"Visible browser viewport changed.","confidence":0.92,"payload":{"region":{"x":0,"y":0,"width":1280,"height":800},"change_ratio":0.12,"threshold":0.1,"screenshot_path":".runtime/browser_observer/screenshots/viewport_change_full/frame.png"}}
```

These examples contain no user-specific absolute paths, credentials, cookies, or
private configuration.

## Compatibility and damaged records

The writer emits only schema v1 and never migrates the existing file. The reader
accepts v1 and recognizes the previous Browser Observer format only when
`schema_version` is absent. A valid legacy record is normalized to the v1 shape in
memory: profile/source/page are derived from the old metadata, detector fields are
moved to `payload`, and a deterministic UUID is derived from its line position and
content. The original JSONL bytes are not changed.

Schema versions greater than `1` are unsupported and skipped safely. Unknown
non-empty `event_type` values in an otherwise valid v1 record remain readable.
Explicit version `0`, negative versions, non-integer versions, and malformed v1
records are invalid rather than legacy.

The reader skips damaged JSON, non-object JSON, records missing v1 required fields,
invalid timestamps or confidence, and invalid object structures. Skipped records
are not counted as valid or returned events. Diagnostics report `valid`, `legacy`,
`invalid`, and `unsupported` counters separately; `legacy` is a subset of valid
events. The compatibility `skipped_records` value is the sum of invalid and
unsupported records. Raw rejected content is never echoed.

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
- Nested objects and lists in `payload` and `metadata` are sanitized recursively.
- Cookie, authorization-header, and credential field contents are replaced with
  `REDACTED`; safe sibling headers and fields are retained.
- External absolute paths are omitted; safe project-relative screenshot paths may
  remain in `payload`.

Sensitive query names include token/access-token variants, auth/authorization,
API-key variants, passwords, authorization codes, sessions, cookies, signatures,
client secrets, credentials, and JWT values.

Raw URLs are used only for runtime allowlist checks. Sanitized URLs are used for
logs, status, errors, and user output.

## Event filters

`browser_watch_events` accepts these `ActionIntent.params` keys:

- `profile`: exact, case-sensitive profile match.
- `event_types`: one exact event type string or a non-empty array of exact event
  type strings. Unknown but syntactically valid types remain searchable.
- `since`: timezone-aware ISO 8601 lower bound, inclusive.
- `until`: timezone-aware ISO 8601 upper bound, exclusive.
- `site`: normalized hostname match, including all its subdomains.
- `url_prefix`: normalized, privacy-cleaned HTTP(S) URL prefix.
- `limit`: strict JSON integer from `1` through `100`, default `5`. JSON booleans
  and numeric strings are invalid.
- `after_event_id`: only valid events physically after this event; the cursor is
  excluded and a missing cursor is an explicit error.
- `after_position`: only valid events after this one-based physical JSONL line.
  `0` starts at the beginning; negative values and JSON booleans are invalid.

All supplied selection filters use AND semantics. Filtering and cursors are
applied before `limit`; the bounded result contains the last `limit` matches in
oldest-to-newest chronological order. `since` must be strictly earlier than
`until`. Naive timestamps without a timezone are rejected. `after_event_id` and
`after_position` are mutually exclusive. Unknown filter names are rejected.

A non-`observer` action target continues to act as the exact `profile` filter.
Stage 4 defines structured `ActionIntent.params`; recognizing natural-language
filter phrases is intentionally outside this contract.

Example structured filters:

```json
{"profile":"text_appeared","event_types":["text_appeared","custom_notice"],"site":"example.com","since":"2026-07-30T10:00:00+02:00","until":"2026-07-30T12:00:00Z","limit":20}
```

```json
{"url_prefix":"https://example.com/page?view=compact&token=private","after_position":41,"limit":10}
```

The second filter is returned with `token=REDACTED`; the safe `view=compact`
value remains.

## `browser_watch_events` result contract

`CommandResult.data` contains:

| Field | Type | Meaning |
|---|---|---|
| `events` | array | Normalized schema-v1-shaped, privacy-cleaned events. |
| `returned_count` | integer | Events actually present in `events`. |
| `matched_count` | integer | Valid events matching cursors and filters before `limit`. |
| `events_count` | integer | Compatibility field; exactly equal to `returned_count`. |
| `matching_events_count` | integer | Compatibility alias for `matched_count`. |
| `valid_events_count` | integer | All valid v1 and normalized legacy events in the scanned journal. |
| `legacy_events_count` | integer | Valid legacy events included in `valid_events_count`. |
| `invalid_events_count` | integer | Blank, corrupt JSON, non-object, or invalid event records. |
| `unsupported_events_count` | integer | Records with a newer schema version. |
| `filters` | object | Canonical normalized filters; unavailable optional values are `null`. |
| `next_position` | integer | Last physical JSONL line inspected, or `0` for an empty/missing journal. |
| `truncated` | boolean | `true` when `limit` omitted earlier matching events. |

`skipped_records`, `diagnostics`, and `matching_events_count` remain for
compatibility. A damaged or unsupported line occupies a physical position and is
counted diagnostically, but never appears in `events` or `events_count`.

The reader performs one streaming pass. It never uses `read()`, `readlines()`, or
an in-memory copy of the journal. A `deque(maxlen=limit)` bounds event storage, so
memory is `O(limit)` and scan time is `O(number of physical lines)`.

`next_position` is a tail/continuation cursor, not reverse pagination: it points
to the last line inspected even when `limit` retained only the newest matches.
Passing it as `after_position` later returns valid events from newly appended
physical lines.

Example result:

```json
{"events":[{"schema_version":1,"event_id":"7f8d37d8-9055-4f69-bd31-9c30f4ac9344","watch_id":"text_appeared","timestamp":"2026-07-30T10:01:00+00:00","event_type":"text_appeared","source":"browser_observer","profile":"text_appeared","payload":{}}],"returned_count":1,"matched_count":3,"events_count":1,"matching_events_count":3,"valid_events_count":5,"legacy_events_count":1,"invalid_events_count":1,"unsupported_events_count":0,"filters":{"profile":"text_appeared","event_types":["text_appeared"],"since":null,"until":null,"site":null,"url_prefix":null,"limit":1,"after_event_id":null,"after_position":null},"next_position":7,"truncated":true}
```

Event data and filter values are privacy-cleaned again before return. URLs lose
userinfo, fragments, and sensitive query values; nested event secrets are
redacted. Validation errors do not echo raw URL filters or credential-like
cursors.

## Status and dry-run

`browser_watch_status` returns its contract in `CommandResult.data`. Watch records
are never encoded into `details` or another multi-line string.

Top-level fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `active_count` | integer | Number of records in `active_watches`. |
| `completed_count` | integer | Number of records in `completed_watches`. |
| `valid_events_count` | integer | Valid v1 plus normalized legacy events in the journal. |
| `legacy_events_count` | integer | Valid legacy records; a subset of `valid_events_count`. |
| `invalid_events_count` | integer | Damaged or structurally invalid records. |
| `unsupported_events_count` | integer | Records using a schema version newer than supported. |
| `active_watches` | array | Structured active watch objects. |
| `completed_watches` | array | Structured completed watch objects, including repeated runs of one profile. |
| `profiles` | array | Currently available configured profile names. |

`events_count` remains a compatibility alias for `valid_events_count`.

Each watch object contains:

| Field | Type | Meaning |
| --- | --- | --- |
| `watch_id` | string | Watch identifier. |
| `profile` | string | Configured profile name. |
| `source` | string | Real event source; currently `background_watch`. |
| `status` | string | Current or final lifecycle status. |
| `started_at` | string or null | UTC ISO 8601 start time. |
| `completed_at` | string or null | UTC ISO 8601 completion time. |
| `elapsed_seconds` | number | Current elapsed duration for the in-memory run. |
| `last_event_type` | string or null | Last stored event type. |
| `last_event_at` | string or null | UTC ISO 8601 time of the last stored event. |
| `events_count` | integer | Events stored by this in-memory watch run. |
| `suppressed_events` | integer | Combined debounce and rate-limit suppressions. |
| `suppressed_duplicates` | integer | Debounce suppressions. |
| `suppressed_rate_limited` | integer | Per-minute rate-limit suppressions. |
| `active_filters` | object | Sanitized profile observation configuration. |
| `limits` | object | `interval_ms`, `timeout_seconds`, `debounce_seconds`, and `max_events_per_minute`. |
| `last_error` | string or null | Sanitized last error. |
| `stop_reason` | string or null | Sanitized completion/stop reason. |
| `current_url` | string or null | Sanitized current URL when known. |

Known optional watch fields are always present and use JSON `null` when the value
is unavailable. Browser identity is not returned because the current clean
Playwright provider does not expose a reliable browser name. Status uses the real
`source` value instead.

Example:

```json
{"active_count":1,"completed_count":1,"valid_events_count":4,"legacy_events_count":1,"invalid_events_count":2,"unsupported_events_count":0,"active_watches":[{"watch_id":"text_appeared","profile":"text_appeared","source":"background_watch","status":"running","started_at":"2026-07-30T10:00:00+00:00","completed_at":null,"elapsed_seconds":60.0,"last_event_type":"text_appeared","last_event_at":"2026-07-30T10:01:00+00:00","events_count":1,"suppressed_events":0,"suppressed_duplicates":0,"suppressed_rate_limited":0,"active_filters":{"mode":"text_appeared","area":"visible_viewport","region":{"type":"full"},"text_configured":true,"text_length":14,"url_allowlist":["https://example.com/"]},"limits":{"interval_ms":500,"timeout_seconds":300,"debounce_seconds":30,"max_events_per_minute":30},"last_error":null,"stop_reason":null,"current_url":"https://example.com/"}],"completed_watches":[{"watch_id":"viewport_change_full","profile":"viewport_change_full","source":"background_watch","status":"completed","started_at":"2026-07-30T09:00:00+00:00","completed_at":"2026-07-30T09:05:00+00:00","elapsed_seconds":300.0,"last_event_type":null,"last_event_at":null,"events_count":0,"suppressed_events":0,"suppressed_duplicates":0,"suppressed_rate_limited":0,"active_filters":{"mode":"viewport_change","area":"visible_viewport","region":{"type":"full"},"url_allowlist":["https://example.com/"]},"limits":{"interval_ms":500,"timeout_seconds":300,"debounce_seconds":30,"max_events_per_minute":30},"last_error":null,"stop_reason":"timeout","current_url":"https://example.com/"}],"profiles":["text_appeared","viewport_change_full"]}
```

`browser_watch_status` and `browser_watch_events` are read-only and execute even
when global dry-run is enabled. Dry-run still previews and blocks
`browser_watch_start`, `browser_watch_stop`, `browser_watch_poll_once`, and every
other modifying Browser Observer operation.

Normal completion reasons such as timeout, explicit stop, shutdown, and test
iteration limits are reported through `stop_reason`, not `last_error`.
The renderer uses the structured arrays to produce a compact summary. With no
active watches it explicitly reports that Browser Observer is not currently
running.
