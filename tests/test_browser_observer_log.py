from __future__ import annotations

from datetime import datetime
from datetime import timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

from actions.browser_observer_log import EventQuery
from actions.browser_observer_log import count_valid_events
from actions.browser_observer_log import event_to_v1_record
from actions.browser_observer_log import normalize_event_record
from actions.browser_observer_log import parse_event_query
from actions.browser_observer_log import read_event_log
from actions.browser_observer_log import sanitize_event_data
from actions.browser_observer_log import sanitize_text
from actions.browser_observer_log import sanitize_url


class BrowserObserverLogTests(unittest.TestCase):
    def test_url_sanitizer_removes_fragment_userinfo_and_sensitive_params(self) -> None:
        url = (
            "https://user:password@Example.com/path?"
            "safe=visible&TOKEN=one&access%5Ftoken=two&Api-Key=three&sessionId=four#private"
        )

        sanitized = sanitize_url(url)

        self.assertEqual(
            sanitized,
            "https://example.com/path?safe=visible&TOKEN=REDACTED&access_token=REDACTED&Api-Key=REDACTED&sessionId=REDACTED",
        )
        self.assertNotIn("user", sanitized)
        self.assertNotIn("password", sanitized)
        self.assertNotIn("#", sanitized)

    def test_url_sanitizer_handles_double_encoded_key_and_nested_url(self) -> None:
        nested = "https%3A%2F%2Fexample.com%2Fnext%3Fcode%3Dsecret%26view%3Dok%23fragment"
        sanitized = sanitize_url(
            f"https://example.com/?%2561ccess%255Ftoken=secret&redirect={nested}&safe=1"
        )

        self.assertIn("%2561ccess%255Ftoken=REDACTED", sanitized)
        self.assertIn("redirect=https%3A%2F%2Fexample.com%2Fnext%3Fcode%3DREDACTED%26view%3Dok", sanitized)
        self.assertIn("safe=1", sanitized)
        self.assertNotIn("secret", sanitized)
        self.assertNotIn("fragment", sanitized)

    def test_v1_record_omits_nulls_core_duplicates_credentials_and_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = event_to_v1_record(
                {
                    "event_id": "event-1",
                    "watch_id": "watch",
                    "profile": "profile",
                    "timestamp": "2026-07-23T10:00:00+00:00",
                    "event_type": "text_appeared",
                    "source": "background_watch",
                    "message": "found",
                    "confidence": None,
                    "region": None,
                    "bbox": None,
                    "center": None,
                    "screenshot_path": "/home/private/screenshots/event.png",
                    "page_url": "https://example.com/?token=secret&view=ok#fragment",
                    "metadata": {
                        "watch_id": "duplicate",
                        "profile": "duplicate",
                        "source": "duplicate",
                        "current_url": "https://example.com/?auth=secret",
                        "authorization": "Bearer secret",
                        "cookie": "session=secret",
                        "sequence": 4,
                    },
                    "payload": {"password": "secret", "count": 2},
                },
                root,
            )

        self.assertEqual(record["schema_version"], 1)
        UUID(str(record["event_id"]))
        self.assertEqual(record["page"], {"url": "https://example.com/?token=REDACTED&view=ok"})
        self.assertEqual(record["metadata"], {"sequence": 4})
        self.assertEqual(
            record["payload"],
            {
                "authorization": "REDACTED",
                "cookie": "REDACTED",
                "password": "REDACTED",
                "count": 2,
            },
        )
        self.assertNotIn("confidence", record)
        self.assertNotIn("page_url", record)
        self.assertNotIn("region", record)
        self.assertNotIn("screenshot_path", record)
        self.assertNotIn("secret", json.dumps(record).lower())

    def test_sensitive_key_variants_are_redacted_without_touching_safe_fields(self) -> None:
        sensitive = {
            "authorization": "secret-exact-auth",
            "cookie": "secret-exact-cookie",
            "password": "secret-exact-password",
            "authorization_header": "secret-snake-auth",
            "cookie_header": "secret-snake-cookie",
            "session_cookie": "secret-session-cookie",
            "credentials_blob": "secret-credentials",
            "authHeader": "secret-camel-auth",
            "setCookie": "secret-camel-cookie",
            "x-authorization": "secret-kebab-auth",
            "apiKey": "secret-api-key",
            "clientSecret": "secret-client",
        }
        safe = {
            "count": 2,
            "mode": "text_appeared",
            "keyboard_layout": "no",
            "tokenizer_version": "one",
            "session_count": 3,
            "headers": {
                "Content-Type": "application/json",
                "Authorization": "secret-header",
                "Set-Cookie": "secret-cookie",
            },
        }

        cleaned = sanitize_event_data({**sensitive, **safe}, key="payload")

        self.assertIsInstance(cleaned, dict)
        assert isinstance(cleaned, dict)
        for key in sensitive:
            with self.subTest(key=key):
                self.assertEqual(cleaned[key], "REDACTED")
        expected_safe = dict(safe)
        expected_safe["headers"] = {
            "Content-Type": "application/json",
            "Authorization": "REDACTED",
            "Set-Cookie": "REDACTED",
        }
        for key, value in expected_safe.items():
            with self.subTest(key=key):
                self.assertEqual(cleaned[key], value)
        self.assertNotIn("secret-", json.dumps(cleaned))

    def test_credential_headers_are_redacted_in_text(self) -> None:
        for value in (
            "Cookie: sid=secret-cookie",
            "Set-Cookie: sid=secret-set-cookie",
            "Authorization: Bearer secret-auth",
            "Proxy-Authorization: Basic secret-proxy",
            "headers={'Cookie': 'sid=secret-quoted'}",
            "authorization_header=secret-assignment",
        ):
            with self.subTest(value=value):
                cleaned = sanitize_text(value)
                self.assertIn("REDACTED", cleaned)
                self.assertNotIn("secret", cleaned)
        self.assertEqual(sanitize_text("Safe status: visible"), "Safe status: visible")

    def test_reader_redacts_sensitive_payload_before_returning_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            event = _event(1)
            event["payload"] = {
                "authorization_header": "secret-auth",
                "cookie_header": "secret-cookie",
                "session_cookie": "secret-session",
                "credentials_blob": "secret-credentials",
                "count": 2,
            }
            _write_rows(path, [event])

            result = read_event_log(path)

        payload = result.events[0]["payload"]
        self.assertEqual(
            payload,
            {
                "authorization_header": "REDACTED",
                "cookie_header": "REDACTED",
                "session_cookie": "REDACTED",
                "credentials_blob": "REDACTED",
                "count": 2,
            },
        )
        self.assertNotIn("secret", json.dumps(result.events))

    def test_filters_apply_before_limit_and_keep_chronological_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            rows = []
            for index in range(30):
                profile = "wanted" if index in {1, 8, 15, 22} else "other"
                rows.append(_event(index, profile=profile))
            _write_rows(path, rows)

            result = read_event_log(path, EventQuery(profile="wanted", limit=3))

        self.assertEqual([event["event_id"] for event in result.events], ["event-8", "event-15", "event-22"])
        self.assertEqual(result.matching_events_count, 4)
        self.assertEqual(result.valid_events_count, 30)
        self.assertTrue(result.truncated)
        self.assertEqual(result.next_position, 30)
        self.assertEqual(
            {
                key: result.as_dict()[key]
                for key in ("returned_count", "matched_count", "events_count", "truncated", "next_position")
            },
            {
                "returned_count": 3,
                "matched_count": 4,
                "events_count": 3,
                "truncated": True,
                "next_position": 30,
            },
        )

    def test_returned_events_are_sorted_chronologically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _write_rows(path, [_event(3), _event(1), _event(2)])

            result = read_event_log(path, EventQuery(limit=3))

        self.assertEqual([event["event_id"] for event in result.events], ["event-1", "event-2", "event-3"])

    def test_combined_filters_use_and_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            rows = [
                _event(1, profile="one", event_type="text_appeared", page_url="https://sub.example.com/a"),
                _event(2, profile="two", event_type="text_appeared", page_url="https://sub.example.com/a"),
                _event(3, profile="one", event_type="viewport_changed", page_url="https://sub.example.com/a"),
                _event(4, profile="one", event_type="text_appeared", page_url="https://other.example/a"),
            ]
            _write_rows(path, rows)

            result = read_event_log(
                path,
                EventQuery(
                    profile="one",
                    event_types=("text_appeared",),
                    site="example.com",
                    url_prefix="https://sub.example.com/",
                    since=datetime(2026, 7, 23, 10, 0, 1, tzinfo=timezone.utc),
                    until=datetime(2026, 7, 23, 10, 0, 2, tzinfo=timezone.utc),
                    limit=10,
                ),
            )

        self.assertEqual([event["event_id"] for event in result.events], ["event-1"])

    def test_event_types_accepts_one_or_many_exact_unknown_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _write_rows(
                path,
                [
                    _event(1, event_type="text_appeared"),
                    _event(2, event_type="future_extension"),
                    _event(3, event_type="viewport_changed"),
                ],
            )

            one = read_event_log(path, EventQuery(event_types=("future_extension",), limit=10))
            many = read_event_log(
                path,
                EventQuery(event_types=("text_appeared", "viewport_changed"), limit=10),
            )

        self.assertEqual([event["event_id"] for event in one.events], ["event-2"])
        self.assertEqual([event["event_id"] for event in many.events], ["event-1", "event-3"])

    def test_since_is_inclusive_and_until_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _write_rows(path, [_event(index) for index in range(4)])

            result = read_event_log(
                path,
                EventQuery(
                    since=datetime(2026, 7, 23, 10, 0, 1, tzinfo=timezone.utc),
                    until=datetime(2026, 7, 23, 10, 0, 3, tzinfo=timezone.utc),
                    limit=10,
                ),
            )

        self.assertEqual([event["event_id"] for event in result.events], ["event-1", "event-2"])

    def test_site_matches_hostname_and_subdomains_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _write_rows(
                path,
                [
                    _event(1, page_url="https://example.com/a"),
                    _event(2, page_url="https://deep.sub.example.com/a"),
                    _event(3, page_url="https://notexample.com/a"),
                ],
            )

            result = read_event_log(path, EventQuery(site="example.com", limit=10))

        self.assertEqual([event["event_id"] for event in result.events], ["event-1", "event-2"])

    def test_url_prefix_is_normalized_and_preserves_safe_query_fields(self) -> None:
        query, error = parse_event_query(
            {"url_prefix": "HTTPS://Example.com/path?token=secret&view=ok"},
            "observer",
        )
        self.assertIsNone(error)
        assert query is not None
        self.assertEqual(query.url_prefix, "https://example.com/path?token=REDACTED&view=ok")
        self.assertNotIn("secret", json.dumps(query.as_dict()))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            matching = _event(
                1,
                page_url="https://example.com/path?token=secret&view=ok&detail=1",
            )
            nonmatching = _event(2, page_url="https://example.com/other?view=ok")
            _write_rows(path, [matching, nonmatching])
            result = read_event_log(path, query)

        self.assertEqual([event["event_id"] for event in result.events], ["event-1"])
        serialized = json.dumps(result.as_dict())
        self.assertNotIn("secret", serialized)
        self.assertIn("view=ok", serialized)

    def test_corrupt_and_invalid_structures_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            invalid = [
                "{",
                "null",
                "[]",
                '"text"',
                json.dumps({"metadata": {}}),
                json.dumps({**_event(1), "metadata": []}),
                json.dumps({**_event(2), "timestamp": "not-a-time"}),
                json.dumps({**_event(3), "payload": []}),
            ]
            path.write_text("\n".join([*invalid, json.dumps(_event(4))]) + "\n", encoding="utf-8")

            result = read_event_log(path)
            valid_count = count_valid_events(path)

        self.assertEqual(result.valid_events_count, 1)
        self.assertEqual(result.invalid_events_count, 8)
        self.assertEqual(result.unsupported_events_count, 0)
        self.assertEqual(result.legacy_events_count, 0)
        self.assertEqual(result.skipped_records, 8)
        self.assertEqual(result.events[0]["event_id"], "event-4")
        self.assertEqual(valid_count, 1)

    def test_v1_required_fields_and_optional_fields_are_enforced(self) -> None:
        minimal = _event(1)
        minimal.pop("message")
        minimal.pop("metadata")
        missing_payload = dict(minimal)
        missing_payload.pop("payload")

        self.assertIsNotNone(normalize_event_record(minimal, 1))
        self.assertIsNone(normalize_event_record(missing_payload, 2))

        for field in (
            "schema_version",
            "event_id",
            "watch_id",
            "timestamp",
            "event_type",
            "source",
            "profile",
        ):
            with self.subTest(field=field):
                candidate = dict(minimal)
                candidate.pop(field)
                self.assertIsNone(normalize_event_record(candidate, 3))

    def test_legacy_event_is_normalized_without_rewriting_file(self) -> None:
        legacy = {
            "watch_id": "legacy_profile",
            "event_type": "legacy_type",
            "confidence": 0.5,
            "message": "legacy",
            "timestamp": "2026-07-23T10:00:00+00:00",
            "region": None,
            "bbox": {"x": 1},
            "center": {"x": 2},
            "screenshot_path": None,
            "metadata": {
                "profile": "legacy_profile",
                "source": "background_watch",
                "url": "https://example.com/?token=secret&safe=1",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            original = json.dumps(legacy)
            path.write_text(original + "\n", encoding="utf-8")

            result = read_event_log(path)

            self.assertEqual(path.read_text(encoding="utf-8"), original + "\n")
        event = result.events[0]
        self.assertEqual(event["schema_version"], 1)
        UUID(str(event["event_id"]))
        self.assertEqual(event["payload"]["bbox"], {"x": 1})
        self.assertEqual(event["payload"]["center"], {"x": 2})
        self.assertEqual(event["page"]["url"], "https://example.com/?token=REDACTED&safe=1")
        self.assertEqual(result.valid_events_count, 1)
        self.assertEqual(result.legacy_events_count, 1)
        self.assertEqual(result.invalid_events_count, 0)
        self.assertEqual(result.unsupported_events_count, 0)

    def test_reader_reports_valid_legacy_invalid_and_unsupported_counters(self) -> None:
        legacy = {
            "watch_id": "legacy",
            "event_type": "text_appeared",
            "message": "found",
            "timestamp": "2026-07-23T10:00:00+00:00",
            "region": None,
            "bbox": None,
            "center": None,
            "screenshot_path": None,
            "metadata": {},
        }
        future = _event(2)
        future["schema_version"] = 2
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(_event(1)),
                        json.dumps(legacy),
                        "{",
                        json.dumps([]),
                        json.dumps(future),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = read_event_log(path)

        self.assertEqual(result.valid_events_count, 2)
        self.assertEqual(result.legacy_events_count, 1)
        self.assertEqual(result.invalid_events_count, 2)
        self.assertEqual(result.unsupported_events_count, 1)
        self.assertEqual(result.skipped_records, 3)
        self.assertEqual(
            result.as_dict()["diagnostics"],
            {"valid": 2, "legacy": 1, "invalid": 2, "unsupported": 1},
        )

    def test_unknown_future_version_is_skipped_but_unknown_event_type_is_valid(self) -> None:
        payload = _event(1, event_type="future_event")
        future = dict(payload)
        future["schema_version"] = 99

        normalized = normalize_event_record(payload, 1)
        unsupported = normalize_event_record(future, 2)

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized["schema_version"], 1)
        self.assertEqual(normalized["event_type"], "future_event")
        self.assertIsNone(unsupported)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _write_rows(path, [payload, future])
            result = read_event_log(path)

        self.assertEqual(result.valid_events_count, 1)
        self.assertEqual(result.unsupported_events_count, 1)
        self.assertEqual(result.invalid_events_count, 0)

    def test_boolean_schema_version_is_invalid_not_version_one(self) -> None:
        boolean_version = _event(1)
        boolean_version["schema_version"] = True

        self.assertIsNone(normalize_event_record(boolean_version, 1))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _write_rows(path, [boolean_version])
            result = read_event_log(path)

        self.assertEqual(result.valid_events_count, 0)
        self.assertEqual(result.invalid_events_count, 1)
        self.assertEqual(result.unsupported_events_count, 0)

    def test_after_event_id_is_exclusive_and_missing_cursor_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _write_rows(path, [_event(index) for index in range(8)])

            result = read_event_log(path, EventQuery(after_event_id="event-3", limit=2))
            missing = read_event_log(path, EventQuery(after_event_id="missing", limit=2))

        self.assertEqual([event["event_id"] for event in result.events], ["event-6", "event-7"])
        self.assertEqual(result.matching_events_count, 4)
        self.assertTrue(result.cursor_found)
        self.assertFalse(missing.cursor_found)
        self.assertEqual(missing.events, [])
        self.assertEqual(result.next_position, 8)

    def test_after_position_uses_one_based_physical_lines_and_counts_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(
                json.dumps(_event(1)) + "\n{\n" + json.dumps(_event(3)) + "\n",
                encoding="utf-8",
            )

            result = read_event_log(path, EventQuery(after_position=1, limit=10))
            after_corrupt = read_event_log(path, EventQuery(after_position=2, limit=10))

        self.assertEqual([event["event_id"] for event in result.events], ["event-3"])
        self.assertEqual([event["event_id"] for event in after_corrupt.events], ["event-3"])
        self.assertEqual(result.next_position, 3)
        self.assertEqual(result.invalid_events_count, 1)
        self.assertEqual(result.valid_events_count, 2)
        self.assertEqual(result.matched_count, 1)

    def test_empty_and_large_logs_use_bounded_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            empty = read_event_log(path, EventQuery(limit=7))
            _write_rows(path, [_event(index) for index in range(5000)])
            large = read_event_log(path, EventQuery(limit=7))

        self.assertEqual(empty.events, [])
        self.assertEqual(empty.next_position, 0)
        self.assertEqual(large.valid_events_count, 5000)
        self.assertEqual(len(large.events), 7)
        self.assertEqual(large.events[0]["event_id"], "event-4993")

    def test_reader_iterates_stream_without_read_or_readlines(self) -> None:
        class IterationOnlyLog:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def __iter__(self):
                return iter([json.dumps(_event(1)) + "\n", "{\n", json.dumps(_event(2)) + "\n"])

            def read(self, *args: object) -> str:
                raise AssertionError("read() must not be used")

            def readlines(self, *args: object) -> list[str]:
                raise AssertionError("readlines() must not be used")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.touch()
            with patch.object(Path, "open", return_value=IterationOnlyLog()):
                result = read_event_log(path, EventQuery(limit=1))

        self.assertEqual([event["event_id"] for event in result.events], ["event-2"])
        self.assertEqual(result.next_position, 3)
        self.assertEqual(result.invalid_events_count, 1)

    def test_query_validation_and_normalized_contract(self) -> None:
        query, error = parse_event_query(
            {
                "profile": "one",
                "event_types": ["text_appeared", "future_extension", "text_appeared"],
                "site": "Example.com",
                "url_prefix": "https://example.com/path?token=secret&safe=1",
                "since": "2026-07-23T10:00:00+02:00",
                "until": "2026-07-23T11:00:00+00:00",
                "limit": 100,
                "after_event_id": "event-1",
            },
            "one",
        )

        self.assertIsNone(error)
        assert query is not None
        self.assertEqual(query.site, "example.com")
        self.assertEqual(query.url_prefix, "https://example.com/path?token=REDACTED&safe=1")
        self.assertEqual(query.event_types, ("text_appeared", "future_extension"))
        self.assertEqual(query.since.isoformat(), "2026-07-23T08:00:00+00:00")
        self.assertEqual(query.limit, 100)
        self.assertEqual(
            set(query.as_dict()),
            {
                "profile",
                "event_types",
                "since",
                "until",
                "site",
                "url_prefix",
                "limit",
                "after_event_id",
                "after_position",
            },
        )
        self.assertNotIn("secret", json.dumps(query.as_dict()))

    def test_all_invalid_filters_are_rejected_without_echoing_credentials(self) -> None:
        for params in (
            {"limit": 0},
            {"limit": 101},
            {"limit": True},
            {"limit": "10"},
            {"since": "2026-07-23T10:00:00"},
            {"since": "not-a-time"},
            {"since": "2026-07-23T10:00:00Z", "until": "2026-07-23T10:00:00Z"},
            {"since": "2026-07-24T10:00:00Z", "until": "2026-07-23T10:00:00Z"},
            {"event_types": []},
            {"event_types": ""},
            {"event_types": [True]},
            {"site": "https://example.com"},
            {"site": "example.com:443"},
            {"url_prefix": "file:///private"},
            {"after_position": -1},
            {"after_position": True},
            {"after_position": "1"},
            {"after_event_id": "event-1", "after_position": 0},
            {"after_event_id": "bad cursor"},
            {"authorization=filter-secret": "value"},
        ):
            with self.subTest(params=params):
                invalid_query, invalid_error = parse_event_query(params, "observer")
                self.assertIsNone(invalid_query)
                self.assertIsNotNone(invalid_error)
                self.assertNotIn("filter-secret", invalid_error or "")


def _event(
    index: int,
    profile: str = "profile",
    source: str = "background_watch",
    event_type: str = "text_appeared",
    page_url: str = "https://example.com/",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "event_id": f"event-{index}",
        "watch_id": profile,
        "profile": profile,
        "timestamp": f"2026-07-23T10:00:{index % 60:02d}+00:00",
        "event_type": event_type,
        "source": source,
        "confidence": 1.0,
        "message": "found",
        "page": {"url": page_url},
        "payload": {},
        "metadata": {},
    }


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
