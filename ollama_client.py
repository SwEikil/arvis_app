from typing import Any

import requests

from config import ARVIS_MODEL
from config import DEFAULT_ARVIS_MODEL
from config import DEFAULT_OLLAMA_HOST
from config import OLLAMA_HOST


class OllamaClient:
    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        timeout: int = 60,
    ) -> None:
        self.host = (host or OLLAMA_HOST or DEFAULT_OLLAMA_HOST).rstrip("/")
        self.model = model or ARVIS_MODEL or DEFAULT_ARVIS_MODEL
        self.timeout = timeout

    def chat(self, messages: list[dict[str, str]]) -> tuple[str | None, str | None]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "30s",
        }

        try:
            response = requests.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=self.timeout,
            )
        except requests.ConnectionError:
            return None, f"Ollama недоступна на {self.host}. Перевір, що сервіс запущений."
        except requests.Timeout:
            return None, "Запит до Ollama перевищив timeout. Спробуй ще раз."
        except requests.RequestException as exc:
            return None, f"Помилка запиту до Ollama: {exc}"

        if response.status_code == 404:
            return None, f"Модель або endpoint не знайдено. Перевір, що модель `{self.model}` існує в Ollama."

        if response.status_code >= 400:
            return None, _format_api_error(response, self.model)

        try:
            data = response.json()
        except ValueError:
            return None, "Ollama повернула не JSON-відповідь."

        message = data.get("message")
        if not isinstance(message, dict):
            return None, "Ollama повернула неочікувану структуру відповіді: немає `message`."

        content = message.get("content")
        if not isinstance(content, str):
            return None, "Ollama повернула неочікувану структуру відповіді: немає `message.content`."

        return content, None


def _format_api_error(response: requests.Response, model: str) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"Ollama повернула HTTP {response.status_code}: {response.text[:300]}"

    error_text = str(data.get("error") or data)
    if "not found" in error_text.lower() or "model" in error_text.lower():
        return f"Модель `{model}` не знайдена або недоступна в Ollama: {error_text}"
    return f"Ollama повернула HTTP {response.status_code}: {error_text}"
