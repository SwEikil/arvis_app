# Arvis Roadmap

Roadmap показує напрям розвитку без дат, строків або обіцянок релізів. Поточна
версія застосунку визначена у [`VERSION`](VERSION), а вже реалізоване описано у
[`CHANGELOG.md`](CHANGELOG.md).

## Implemented

Baseline Arvis v0.3.1 містить:

- Terminal REPL.
- Ollama integration.
- Intent parsing.
- Intent Resolver.
- Command Router.
- Response Renderer.
- Doctor Mode.
- Reload/restart.
- Voice one-shot.
- Safe desktop actions.
- Minecraft Server Manager.
- Browser Observer.
- MCP Context Servant.

## Next

### Context & Memory Core

- Rolling conversation summary.
- Окрема user memory.
- Memory Router.
- Relevant context builder.
- Memory management commands.
- Privacy and secret filtering.

Поточні `session_summary` і `MEMORY_INTENT` — підготовлені межі, а не готова
довготривала пам’ять.

## Planned

1. Voice Diagnostics v0.2.
2. Local Extension Host.
3. Notifications.
4. Developer Agent Bridge.
5. Supervised Orchestrator.

Ці назви позначають майбутні етапи чи підсистеми, а не поточну app version.

## Deferred

- Always-listening voice.
- Wake word.
- Speaker verification.
- Cross-process Browser Observer daemon.
- Automatic watcher restore.
- Autonomous agent execution.

## Out of scope for public repository

- Generic auto-clicking.
- Farming and anti-idle.
- Arbitrary computer control.
- CAPTCHA/login/payment bypass.
- Private machine-specific automation.

Особисті сценарії можуть існувати тільки як disabled-by-default ignored local
extensions, які споживають публічні observation events і не потрапляють у
tracked public code.
