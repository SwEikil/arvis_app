# AGENTS.md

## Scope

These instructions apply to the whole repository unless a more specific `AGENTS.md` exists in a subdirectory.

Use this file as the main guide for Codex/AI agents working on `SwEikil/arvis_app`.

## Project identity

Arvis is a local-first Python console assistant. It is designed to run on the user's own machine with Ollama and a terminal UI, so reliability and safety matter more than flashy automation.

Current implemented features:

- Terminal REPL chat with Rich UI.
- Ollama `/api/chat` client with default model name `arvis`.
- Active in-memory chat history with a placeholder for future rolling `session_summary`.
- Parsing of `ACTION_INTENT` and `MEMORY_INTENT` from model output.
- Intent Resolver fallback for natural commands when the model does not emit a usable action intent.
- Command Router as the final whitelist and safety gate.
- Dry-run mode enabled by default.
- Doctor Mode for runtime, config, privacy, Ollama, action readiness, storage, and git checks.
- Self reload/restart through `/reload` and `/restart` using `.runtime/reload_state.json`.
- Optional voice command layer v0.1 with explicit one-shot microphone input.
- Local desktop actions for media, volume, app launch, and Minecraft server management.

The project language in code identifiers is English. User-facing REPL text is mostly Ukrainian with the existing informal Arvis tone. Do not rewrite the app into formal corporate language.

## Runtime and commands

Use Python 3.11 or newer.

Common commands from the project root:

```bash
python main.py
python main.py doctor
python main.py doctor --json
python main.py doctor --strict
python main.py doctor --fix
```

With a local virtual environment:

```bash
.venv/bin/python main.py
.venv/bin/python main.py doctor
.venv/bin/python -m unittest discover -s tests
```

A useful compile check:

```bash
find . -name '*.py' -not -path './.venv/*' -print0 | xargs -0 .venv/bin/python -m py_compile
```

Do not require Ollama, audio devices, Flatpak apps, Spotify, Brave, or a Minecraft server to be running for normal unit tests. Tests should mock those boundaries.

## Dependencies

Runtime dependencies are intentionally small:

- `requests`
- `rich`
- `pydantic`
- `python-dotenv`

Optional voice/STT dependencies must be imported lazily and only when a voice command needs them. Text mode must still work if voice dependencies are missing.

## Architecture map

Important files and responsibilities:

- `main.py` - REPL loop, slash commands, history, reload handling, voice command entry points.
- `ollama_client.py` - Ollama API access.
- `intent_parser.py` - parsing model responses into assistant text and structured intents.
- `intent_resolver.py` - deterministic/LLM fallback resolver. It must not execute actions.
- `command_router.py` - final action safety gate and dispatcher. Only this layer may route actions to executors.
- `response_renderer.py` - user-facing action-aware responses.
- `runtime_state.py` - safe save/load/delete of reload state.
- `doctor.py` - local health checks, JSON/text reports, redaction, and safe `--fix` behavior.
- `config.py` - local configuration from environment/defaults.
- `actions/` - desktop, media, volume, and Minecraft action implementations.
- `voice_config.py`, `voice_input.py`, `voice_ducking.py`, `voice_text_normalizer.py` - optional voice pipeline.
- `tests/` - unittest-based coverage.

## Core safety rules

Safety is part of the product, not a temporary limitation.

Hard rules:

- Keep `CommandRouter(dry_run=True)` as the default.
- Keep action execution behind `CommandRouter`.
- Keep `IntentResolver` separate from the router. It can only return a candidate intent.
- Keep apps, action names, and Minecraft targets whitelisted.
- Keep risky or ambiguous inputs as safe no-ops or clarifications.
- Do not add broad computer-control paths that bypass the existing router/whitelist design.
- Do not fake confirmation support. If a flow needs confirmation, implement and test a real confirmation path first.
- Keep command previews available for dry-run.
- Keep local machine-specific behavior in `.env`, not in tracked code.
- Keep `.env.example` limited to placeholders.
- Do not commit local runtime files, logs, caches, model files, or private config.
- Preserve secret/path redaction in Doctor Mode.
- Keep voice mode explicit and manual; do not add background recording.
- Keep user-facing REPL text close to the existing Ukrainian informal Arvis style.

## Configuration rules

Important env areas:

- Ollama: `OLLAMA_HOST`, `ARVIS_MODEL`
- local user/app paths: `USER_NAME`, `MUSIC_FOLDER`, `DOWNLOADS_FOLDER`
- app launch commands: `STEAM_COMMAND`, `SPOTIFY_COMMAND`, `BRAVE_COMMAND`, `DISCORD_COMMAND`, `TELEGRAM_COMMAND`
- voice: `ARVIS_VOICE_*`, `ARVIS_STT_*`, `ARVIS_MIC_DEVICE`
- Minecraft server: `MINECRAFT_SERVER_*`

When adding an env key, update all relevant places together:

1. `config.py`
2. `.env.example`
3. `doctor.py` known-key/check logic
4. README documentation
5. tests

## Intent/action pipeline rules

The intended flow is:

1. User text enters `main.py`.
2. Ollama returns assistant text and optionally structured intent blocks.
3. `intent_parser.py` parses the response.
4. `CommandRouter` tries parsed `ACTION_INTENT` if present.
5. `IntentResolver` may repair or infer a safe candidate only when needed.
6. `CommandRouter` is still the final gate.
7. `response_renderer.py` renders the final user-facing result.

Do not collapse these layers into one large function. The separation is what keeps the app testable and safe.

When adding a new action:

1. Add the canonical action to allowed action sets.
2. Add aliases/phrases in resolver/router where needed.
3. Add a preview path for dry-run.
4. Add a whitelisted execute path.
5. Add action-aware response text.
6. Add Doctor readiness checks when external tools/config are needed.
7. Add unit tests for dry-run, execution mocks, unknown target, unsafe text, and ambiguous input.
8. Update README.

## Voice mode rules

Voice is optional and disabled by default. Keep it manual and local.

The current intended commands are:

- `/voice status`
- `/voice warmup`
- `/voice diagnose`
- `/voice test`
- `/voice once`

Voice flow should remain:

1. load config
2. check dependencies/preflight
3. warm up STT when needed
4. duck audio only during explicit microphone recording if enabled
5. restore volume
6. transcribe
7. correct/normalize text
8. for `/voice once`, send recognized text through the same text pipeline

Debug audio, when enabled locally, must stay under `.runtime/voice_debug/` and must not be committed.

## Minecraft server rules

Minecraft server control is safety-sensitive.

- Server target must stay whitelisted/configured.
- Do not start duplicate server processes.
- Read-only actions such as status/logs/diagnostics/metrics may inspect safely.
- Stop/restart should only control a server started in the managed way Arvis understands.
- If an unmanaged server is found, explain that it must be stopped manually once and then started through Arvis.
- Keep client/launcher detection separate from server detection. Do not mistake PrismLauncher or Minecraft client processes for the server.

## Doctor Mode rules

Doctor Mode is for local diagnostics, not invasive repair.

Keep these properties:

- text and JSON reports must redact secrets and personal paths
- JSON output must not contain ANSI escape codes
- missing Ollama should warn, not fail the whole project
- missing `.env` should be info, not fail
- `--strict` should make warnings produce non-zero exit
- `--fix` must remain safe and limited
- checks should be mockable and not require real desktop/audio/Ollama/Minecraft state

## Testing expectations

This project uses `unittest`, not pytest-specific features.

Before committing meaningful changes, run:

```bash
.venv/bin/python -m unittest discover -s tests
```

For syntax/import confidence, also run a compile check when practical:

```bash
find . -name '*.py' -not -path './.venv/*' -print0 | xargs -0 .venv/bin/python -m py_compile
```

Add tests for every behavior change. Mock external commands, subprocesses, Ollama HTTP calls, audio devices, `playerctl`, `wpctl`, `tmux`, Flatpak apps, and filesystem edge cases.

## Code style

- Prefer small, explicit functions over large rewrites.
- Keep dataclasses and return objects stable unless the change is intentional and tested.
- Preserve type hints.
- Prefer deterministic heuristics before LLM fallback.
- Keep user-facing output short and human-readable.
- Keep Ukrainian/Russian/English/Norwegian command phrase support when changing resolver logic.
- Do not remove existing informal Ukrainian wording just to make the app sound more formal.
- Avoid broad refactors when a targeted fix is enough.

## Documentation expectations

Update README when you change:

- user-visible commands
- env keys
- startup/test instructions
- Doctor output/flags
- voice behavior
- action names or safety behavior
- Minecraft server setup/behavior

Keep examples safe and local-only. Use placeholders, never real secrets or personal local paths.

## Current development direction

The project is moving toward a reliable local assistant that can gradually control safe desktop tasks. Prefer boring, testable safety over flashy features.

Good next-step features usually look like this:

- better resolver accuracy for natural Ukrainian/Russian/English commands
- more tests around ambiguous and unsafe inputs
- improved Doctor diagnostics
- safer action previews
- richer but local-only runtime state
- better voice normalization without background listening
