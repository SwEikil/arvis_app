# AGENTS.md

Instructions for AI agents working on this repository.

Arvis is a local-first Python console assistant built around Ollama, a Rich terminal UI, an intent pipeline, a safe command router, Doctor Mode, optional voice commands, and local desktop actions.

Keep changes small, tested, and local-first.

Use Python 3.11 or newer.

Run from the repository root:

```bash
python main.py
python main.py doctor
python main.py doctor --json
python main.py doctor --strict
```

With a local virtual environment:

```bash
.venv/bin/python main.py
.venv/bin/python -m unittest discover -s tests
```

Core files:

- `main.py` - REPL, slash commands, history, reload, voice entry points.
- `ollama_client.py` - Ollama API access.
- `intent_parser.py` - parses assistant messages and structured intents.
- `intent_resolver.py` - infers safe candidate intents; it must not execute actions.
- `command_router.py` - final whitelist and safety gate for actions.
- `response_renderer.py` - user-facing action results.
- `doctor.py` - local diagnostics, reports, redaction, and safe fixes.
- `runtime_state.py` - reload state handling.
- `actions/` - desktop, media, volume, and Minecraft action implementations.
- `voice_*` files - optional voice command pipeline.
- `tests/` - unittest coverage.

Important rules:

- Keep dry-run enabled by default.
- Keep action execution behind the command router.
- Keep the resolver separate from the router.
- Keep app and server targets whitelisted.
- Keep local machine settings in `.env`, not in tracked code.
- Keep `.env.example` limited to placeholders.
- Do not commit local runtime files, logs, caches, model files, or private config.
- Preserve secret/path redaction in Doctor Mode.
- Keep voice mode explicit and manual; do not add background recording.
- Keep user-facing REPL text close to the existing Ukrainian informal Arvis style.

When adding an env key, update `config.py`, `.env.example`, `doctor.py`, README, and tests.

When adding an action, add resolver/router support, dry-run preview, safe execution, response text, Doctor checks if needed, README docs, and tests.

Before a meaningful commit, run:

```bash
.venv/bin/python -m unittest discover -s tests
```

Prefer boring, reliable, testable safety over flashy features.
