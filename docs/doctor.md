# Doctor Mode

[← Індекс документації](README.md) · [Головна сторінка](../README.md)

Doctor перевіряє локальну готовність Arvis без destructive або invasive repair.

## Запуск

```bash
python main.py doctor
python main.py doctor --json
python main.py doctor --verbose
python main.py doctor --strict
python main.py doctor --fix
python main.py doctor --no-color
```

З venv:

```bash
.venv/bin/python main.py doctor --json
```

У REPL доступний `/doctor`. Machine-readable JSON треба запускати через CLI.

## Що перевіряється

- Python 3.11+ і core project files.
- Required Python packages та imports.
- `.env`, `.env.example`, відомі keys і safe placeholder values.
- Secret/path redaction і gitignore coverage.
- Ollama URL/readiness.
- Optional voice configuration/dependencies.
- Desktop tools та parseable app commands.
- Minecraft configuration і readiness.
- Writable `logs/`, `.cache/`, `.runtime/`.
- Tracked secrets/local model files.

Doctor не вимагає, щоб optional Ollama, microphone, browser, apps або Minecraft
server були запущені для загального success.

## Status і exit codes

- `OK` — перевірка пройдена.
- `WARN` — optional або локальна проблема, яка не ламає звичайний Doctor.
- `FAIL` — required check не пройдений.
- `INFO` — стан disabled/optional компонента.

Звичайний Doctor повертає non-zero при `FAIL`. `--strict` також перетворює
warnings на non-zero, тому baseline CI навмисно запускає не-strict
`doctor --json`.

## Flags

- `--json` — machine-readable report без ANSI escapes.
- `--verbose` — додаткові diagnostic details.
- `--strict` — warnings роблять exit code non-zero.
- `--fix` — тільки обмежені safe fixes.
- `--no-color` — вимикає color у text output.

`--fix` може створити safe local directories (`logs/`, `.cache/`, `.runtime/`)
або placeholder-style `.env.example`, якщо його немає. Він не встановлює
packages, не стартує services, не редагує персональний `.env` і не виконує
довільні shell commands.

## Privacy

Text і JSON reports мають редагувати secret-like values та персональні paths.
Не публікуй повний локальний report без перегляду й ніколи не коміть `.env`,
tokens, runtime state, logs, screenshots або model files.
