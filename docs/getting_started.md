# Початок роботи

[← Індекс документації](README.md) · [Головна сторінка](../README.md)

## Вимоги

- Python 3.11 або новіший.
- Локальний Ollama, якщо потрібен REPL-чат.
- Chat-модель, доступна в Ollama. Default name у Arvis — `arvis`, але його можна
  змінити через `ARVIS_MODEL`.

Desktop tools (`playerctl`, `wpctl`, Flatpak apps, tmux), voice dependencies і
browser dependencies опційні. Їх відсутність не повинна ламати text mode або
звичайні unit tests.

## Установка

З project root:

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Відредагуй `.env` під свою машину. Не додавай туди секрети, які не потрібні
Arvis, і ніколи не коміть цей файл.

Основні runtime dependencies:

- `requests`;
- `rich`;
- `pydantic`;
- `python-dotenv`;
- `mcp` для standalone MCP Context Servant.

## Перший запуск

Спершу перевір конфігурацію:

```bash
.venv/bin/python main.py doctor
```

Warnings про offline Ollama або disabled optional компоненти не є загальним
failure у звичайному Doctor mode. Після перевірки:

```bash
.venv/bin/python main.py
```

Arvis покаже REPL і список slash-команд. Command Router стартує в dry-run, тому
навіть розпізнані modifying actions спочатку лише показують preview.

## Мінімальна Ollama-конфігурація

Safe defaults:

```dotenv
OLLAMA_HOST=http://127.0.0.1:11434
ARVIS_MODEL=arvis
```

Якщо твоя локальна модель має інше ім’я, зміни `ARVIS_MODEL`. Arvis не створює
і не завантажує Ollama-модель автоматично.

## Швидка перевірка REPL

```text
/doctor
/dryrun
/actions
/history
/help
```

Для реального виконання safe whitelist action треба явно ввести `/dryrun off`.
Medium/dangerous, unknown, ambiguous або confirmation-required intent усе одно
не виконується.

## Запуск без venv

Якщо dependencies уже встановлені для системного Python:

```bash
python main.py doctor
python main.py
```

Помилка імпорту на кшталт `No module named 'dotenv'` означає, що обраний Python
не має packages з `requirements.txt`; це не помилка Doctor.

Далі: [конфігурація](configuration.md), [команди](commands.md) і
[Doctor Mode](doctor.md).
