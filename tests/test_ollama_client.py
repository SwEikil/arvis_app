from __future__ import annotations

import unittest
from unittest.mock import Mock
from unittest.mock import patch

from ollama_client import OllamaClient


class OllamaClientFormatTests(unittest.TestCase):
    def _response(self) -> Mock:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"message": {"content": "ok"}}
        return response

    def test_normal_chat_does_not_request_structured_output(self) -> None:
        with patch("ollama_client.requests.post", return_value=self._response()) as post:
            output, error = OllamaClient(host="http://ollama.invalid").chat([])

        self.assertEqual((output, error), ("ok", None))
        self.assertNotIn("format", post.call_args.kwargs["json"])

    def test_explicit_response_format_is_added_to_payload(self) -> None:
        with patch("ollama_client.requests.post", return_value=self._response()) as post:
            output, error = OllamaClient(host="http://ollama.invalid").chat(
                [],
                response_format="json",
            )

        self.assertEqual((output, error), ("ok", None))
        self.assertEqual(post.call_args.kwargs["json"]["format"], "json")


if __name__ == "__main__":
    unittest.main()
