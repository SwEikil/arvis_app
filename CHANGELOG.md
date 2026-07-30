# Changelog

Усі помітні зміни Arvis документуються тут. Формат близький до
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), а версія застосунку
зберігається у [`VERSION`](VERSION).

## [0.3.1] - 2026-07-30

Перший цілісний public baseline Arvis.

### Core

- Додано terminal REPL із Rich UI та локальним Ollama `/api/chat` client.
- Реалізовано active in-memory history, parsing `ACTION_INTENT` і
  `MEMORY_INTENT`, Intent Resolver, Command Router та action-aware Response
  Renderer.
- Додано безпечне збереження мінімального runtime state для `/reload` і
  `/restart`.
- `session_summary` залишено явним placeholder до появи Context & Memory Core.

### Safety

- Dry-run увімкнено за замовчуванням.
- Command Router закріплено як фінальний whitelist і safety gate.
- Risky, ambiguous, unknown і confirmation-required inputs не виконуються.
- Public browser observation відокремлено від private local actions:
  observer бачить і створює events, але не клікає й не натискає клавіші.

### Doctor

- Додано перевірки Python runtime, dependencies, imports, config, privacy,
  Ollama, optional voice, desktop actions, storage, Minecraft і git.
- Додано text/JSON output, secret/path redaction, `--strict` і обмежений safe
  `--fix`.

### Voice

- Додано explicit one-shot microphone flow для `/voice warmup`, `/voice
  diagnose`, `/voice test` і `/voice once`.
- Optional STT/audio dependencies завантажуються ліниво; text mode не залежить
  від їх наявності.
- Реалізовано normalization та best-effort audio ducking тільки на час ручного
  запису.

### Desktop and server actions

- Додано whitelisted media, volume, app і website launch actions.
- Реалізовано Minecraft Server Manager зі status, logs, diagnostics, metrics,
  duplicate detection та обмеженим managed start/stop/restart через tmux.
- Unmanaged server processes не зупиняються й не рестартяться автоматично.

### Browser Observer

- Додано public observation-only core для DOM, text, viewport і template
  signals з config-backed URL/profile allowlists.
- Додано controlled Playwright poll-once та in-process watcher lifecycle без
  persistent profile, CDP attach чи cross-process daemon.
- Додано versioned JSONL event contract **schema v1**, privacy sanitization,
  diagnostics, filters і bounded streaming reads.
- Збережено окремий narrow experimental HumanBenchmark Aim task із hard limits
  і clean controlled-browser context.

### MCP Context Servant

- Додано standalone stdio fact helper для bounded project map, grep, excerpts,
  git status, task briefs і local ignored memory.
- Сервер не редагує source code, не виконує arbitrary shell і не замінює
  coding agent.

### Documentation and testing

- Створено структуровану документацію, єдину app version, changelog, roadmap і
  мінімальний GitHub Actions CI.
- Unit tests покривають core pipeline, safety routing, Doctor, voice,
  Minecraft, Browser Agent, Browser Observer та MCP context helpers із
  mock-границями для optional/external systems.
