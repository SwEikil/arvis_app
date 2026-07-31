# Документація Arvis

[← Головна сторінка](../README.md)

Це навігаційний індекс документації Arvis v0.3.1. Версія застосунку зберігається
у [`VERSION`](../VERSION), історія baseline — у
[`CHANGELOG.md`](../CHANGELOG.md), а майбутні напрями — у
[`ROADMAP.md`](../ROADMAP.md).

## Getting started

- [`getting_started.md`](getting_started.md) — вимоги, установка, перший Doctor
  і запуск REPL.

## Configuration

- [`configuration.md`](configuration.md) — повний довідник `.env`, safe defaults,
  локальні команди та ignored runtime paths.

## Using Arvis

- [`commands.md`](commands.md) — slash-команди, action-групи, dry-run і короткі
  natural-language приклади.
- [`doctor.md`](doctor.md) — перевірки Doctor Mode, прапорці та exit codes.

## Architecture

- [`architecture.md`](architecture.md) — intent/action pipeline, runtime state,
  memory limitations, module map і safety boundaries.
- [`context_memory_audit.md`](context_memory_audit.md) — перевірений baseline
  conversation context, межі memory domains і target architecture.
- [`rolling_summary_contract.md`](rolling_summary_contract.md) — цільовий
  контракт bounded rolling summary, turn boundaries, privacy та failure
  semantics.

## Subsystems

- [`voice.md`](voice.md) — explicit one-shot microphone flow та optional STT.
- [`browser_vision_agent.md`](browser_vision_agent.md) — вузький experimental
  controlled-browser demo task.
- [`browser_observer.md`](browser_observer.md) — observation-only runtime,
  JSONL events, schema v1, filters і watcher status.
- [`minecraft_server.md`](minecraft_server.md) — safe managed server control,
  process detection і read-only diagnostics.
- [`mcp_context_servant.md`](mcp_context_servant.md) — bounded fact helper для
  coding agents.

## Development

- [`development.md`](development.md) — залежності, unit tests, compile check,
  Doctor і GitHub Actions CI.
- [`../AGENTS.md`](../AGENTS.md) — обов’язкові робочі правила для coding agents.

## Safety and extension boundaries

Public `arvis_app` може observe, detect, emit, log, notify та виконувати лише
вузькі whitelisted actions через Command Router. Generic clicking, farming,
anti-idle й machine-specific automation не належать до public repository.

Майбутні приватні сценарії мають жити у `.local_extensions/` або
`.runtime/local_extensions/`, бути disabled by default і споживати публічні
observer events без змішування з tracked public modules.
