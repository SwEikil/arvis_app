from __future__ import annotations

from datetime import datetime
from datetime import timezone
import json
from pathlib import Path
import tempfile
import unittest

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
        self.assertEqual(record["page_url"], "https://example.com/?token=REDACTED&view=ok")
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
        self.assertNotIn("region", record)
        self.assertNotIn("coordinates", record)
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
        }

        cleaned = sanitize_event_data({**sensitive, **safe}, key="payload")

        self.assertIsInstance(cleaned, dict)
        assert isinstance(cleaned, dict)
        for key in sensitive:
            with self.subTest(key=key):
                self.assertEqual(cleaned[key], "REDACTED")
        for key, value in safe.items():
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

    def test_combined_filters_use_and_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            rows = [
                _event(1, profile="one", source="background_watch", event_type="text_appeared", page_url="https://sub.example.com/a"),
                _event(2, profile="one", source="poll_once", event_type="text_appeared", page_url="https://sub.example.com/a"),
                _event(3, profile="one", source="background_watch", event_type="viewport_changed", page_url="https://sub.example.com/a"),
                _event(4, profile="one", source="background_watch", event_type="text_appeared", page_url="https://other.example/a"),
            ]
            _write_rows(path, rows)

            result = read_event_log(
                path,
                EventQuery(
                    profile="one",
                    source="background_watch",
                    event_type="text_appeared",
                    domain="example.com",
                    url="https://sub.example.com/",
                    from_time=datetime(2026, 7, 23, 10, 0, 1, tzinfo=timezone.utc),
                    to_time=datetime(2026, 7, 23, 10, 0, 1, tzinfo=timezone.utc),
                    limit=10,
                ),
            )

        self.assertEqual([event["event_id"] for event in result.events], ["event-1"])

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
        self.assertEqual(result.skipped_records, 8)
        self.assertEqual(result.events[0]["event_id"], "event-4")
        self.assertEqual(valid_count, 1)

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
        self.assertEqual(event["schema_version"], 0)
        self.assertTrue(event["legacy"])
        self.assertTrue(str(event["event_id"]).startswith("legacy-1-"))
        self.assertEqual(event["coordinates"], {"bbox": {"x": 1}, "center": {"x": 2}})
        self.assertEqual(event["page_url"], "https://example.com/?token=REDACTED&safe=1")

    def test_unknown_version_and_event_type_do_not_break_reading(self) -> None:
        payload = _event(1, event_type="future_event")
        payload["schema_version"] = 99

        normalized = normalize_event_record(payload, 1)

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized["schema_version"], 99)
        self.assertEqual(normalized["event_type"], "future_event")

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

    def test_empty_and_large_logs_use_bounded_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            empty = read_event_log(path, EventQuery(limit=7))
            _write_rows(path, [_event(index) for index in range(5000)])
            large = read_event_log(path, EventQuery(limit=7))

        self.assertEqual(empty.events, [])
        self.assertEqual(large.valid_events_count, 5000)
        self.assertEqual(len(large.events), 7)
        self.assertEqual(large.events[0]["event_id"], "event-4993")

    def test_query_validation_covers_limits_dates_and_cursor(self) -> None:
        query, error = parse_event_query(
            {
                "profile": "one",
                "source": "background_watch",
                "event_type": "text_appeared",
                "domain": "Example.com",
                "url": "https://example.com/path?token=secret",
                "from": "2026-07-23T10:00:00Z",
                "to": "2026-07-23T11:00:00+00:00",
                "limit": "100",
                "after_event_id": "event-1",
            },
            "one",
        )

        self.assertIsNone(error)
        assert query is not None
        self.assertEqual(query.domain, "example.com")
        self.assertEqual(query.url, "https://example.com/path?token=REDACTED")
        self.assertEqual(query.limit, 100)
        for params in (
            {"limit": 0},
            {"limit": 101},
            {"from": "2026-07-23T10:00:00"},
            {"from": "2026-07-24T10:00:00Z", "to": "2026-07-23T10:00:00Z"},
            {"domain": "https://example.com"},
            {"after_event_id": "bad cursor"},
            {"unknown": "value"},
        ):
            with self.subTest(params=params):
                invalid_query, invalid_error = parse_event_query(params, "observer")
                self.assertIsNone(invalid_query)
                self.assertIsNotNone(invalid_error)


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
        "page_url": page_url,
        "metadata": {},
    }


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
