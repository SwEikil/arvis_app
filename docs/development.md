# Розробка

[← Індекс документації](README.md) · [Головна сторінка](../README.md)

## Runtime і dependencies

Підтримується Python 3.11+. Основні packages визначені у
[`requirements.txt`](../requirements.txt):

```text
requests
rich
pydantic
python-dotenv
mcp>=1.27,<2
```

Voice/STT та browser vision/observer dependencies опційні й мають імпортуватися
ліниво. Не додавай Playwright browser binaries, Faster Whisper, OpenCV або NumPy
до baseline CI лише для звичайних unit tests.

## Локальна перевірка

З project root:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall -q .
.venv/bin/python main.py doctor --json
git diff --check
```

Якщо dependencies встановлені в system Python, префікс `.venv/bin/` можна
прибрати.

Проєкт використовує `unittest`, не pytest-specific features. Tests мають mock
Ollama HTTP, subprocesses, audio devices, `playerctl`, `wpctl`, tmux, Flatpak
apps, Playwright pages, OpenCV/NumPy boundaries та filesystem edge cases.

## GitHub Actions CI

Baseline workflow: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

На кожен `push` і `pull_request` він використовує Python 3.11 та запускає:

1. install `requirements.txt`;
2. `python -m unittest discover -s tests`;
3. `python -m compileall -q .`;
4. `python main.py doctor --json`.

Doctor запускається без `--strict`: optional warnings у clean CI не повинні
ламати build, але реальні required failures зберігають non-zero exit code.

## Правила змін

- Роби мінімальні targeted patches без зміни safety-моделі.
- Не об’єднуй parser, resolver, router і executor в один шар.
- Кожна нова action потребує whitelist, dry-run preview, safe executor,
  renderer response, readiness checks за потреби й tests.
- Не вимагай optional local systems для core tests.
- Не додавай machine paths, tokens, `.env`, runtime logs/screenshots або model
  files.
- Public browser observer лишається eyes/events only.
- Private machine-specific actions мають бути ignored і поза public code path.

Повні agent instructions: [`AGENTS.md`](../AGENTS.md).

## Оновлення документації

Коли змінюється user-visible command, env key, Doctor, voice, action, browser
або Minecraft behavior:

- онови відповідний specialized doc;
- перевір короткий overview у root README;
- не дублюй повний reference у README;
- перевір Markdown links і точний регістр filenames;
- відділяй app version від schema/contract versions.
