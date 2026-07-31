# Команди Arvis

[← Індекс документації](README.md) · [Головна сторінка](../README.md)

## Slash-команди

| Команда | Дія |
| --- | --- |
| `/exit`, `/quit` | Завершити REPL і зупинити in-process browser watchers. |
| `/reset` | Почати нову conversation session: очистити history, summary і command context. |
| `/debug on`, `/debug off` | Увімкнути або вимкнути diagnostic panels. |
| `/dryrun` | Показати стан dry-run. |
| `/dryrun on`, `/dryrun off` | Увімкнути preview-only або дозволити safe execution. |
| `/reload`, `/restart` | Перезапустити Python process зі safe runtime state. |
| `/doctor [flags]` | Запустити локальні health checks. |
| `/actions` | Показати основні whitelisted actions і readiness. |
| `/voice status` | Показати voice config та dependencies. |
| `/voice warmup` | Підготувати STT model без запису. |
| `/voice diagnose` | Записати sample й показати correction/resolver diagnostics без execution. |
| `/voice test` | Розпізнати sample без виконання команди. |
| `/voice once` | Розпізнати один sample і передати текст у звичайний pipeline. |
| `/history` | Показати до 40 активних messages. |
| `/summary` | Показати validated rolling `session_summary` поточної session. |
| `/help` | Показати REPL-команди. |

У REPL `/doctor --json` підказує використати CLI:

```bash
python main.py doctor --json
```

Повні CLI flags описані у [`doctor.md`](doctor.md).

`/reset` створює новий session UUID, але зберігає поточні debug і dry-run
налаштування. `/reload` та `/restart` переносять той самий UUID, bounded history,
summary і command context через приватний one-shot snapshot. Звичайний exit не
створює durable conversation snapshot.

## Dry-run

Новий `CommandRouter` завжди стартує з dry-run. `/dryrun off` не вимикає safety:
виконуватися можуть лише safe whitelisted actions із валідними targets і
params. Read-only Browser Observer status/events та read-only Minecraft actions
можуть повертати реальні дані навіть у dry-run.

## Natural-language commands

Intent Resolver має bounded heuristics для українських, російських і англійських
фраз та обмежений Ollama fallback. Приклади:

```text
відкрий ютуб
Вруби споті
зроби гучніше на 10
поверни звук
що зараз грає
відмотай назад на 15 секунд
статус майн сервера
покажи логи майн сервера
перевір профіль спостереження text_appeared
покажи останні 10 подій спостереження
```

Resolver не виконує дію, не вигадує profile/URL/timezone і не обходить Router.
Низька confidence або неоднозначний параметр приводять до clarification/safe
no-op.

## Action groups

### Media

`music_play_pause`, `music_next`, `music_previous`, `music_play`,
`music_pause`, `play_music_by_mood`, `media_seek_forward`,
`media_seek_backward`, repeat/shuffle actions, `music_like_current` та
`media_status`.

- Seek defaults to 5 seconds і обмежується `1..300`.
- `play_music_by_mood` запускає доступний player, але ще не вибирає playlist за
  mood.
- `music_like_current` розпізнається як safe, але повертає `unsupported`, бо
  потребує Spotify Web API/OAuth; GUI automation не використовується.

### Volume

`volume_up`, `volume_down`, `volume_mute`, `volume_unmute`, `volume_status`,
`volume_set`.

- Default step — 5%, explicit step обмежується `1..50`.
- `volume_set` обмежується `0..100`.
- Explicit mute/unmute не використовує toggle.
- Per-app volume не реалізовано; action працює з default audio sink.

### Apps and sites

`open_app` / `launch_app` із whitelisted targets:

`spotify`, `steam`, `brave`, `discord`, `telegram`, `youtube`, `google`,
`github`, `chatgpt`.

Модель передає target key, а локальну argv-команду визначає config. Arbitrary
URLs і raw shell не приймаються.

### Minecraft

`minecraft_server_status`, `minecraft_server_start`, `minecraft_server_stop`,
`minecraft_server_restart`, `minecraft_server_logs`,
`minecraft_server_diagnostics`, `minecraft_server_metrics` і compatibility
alias `start_minecraft_server`.

Read-only actions доступні в dry-run; modifying actions потребують managed
configuration. Див. [`minecraft_server.md`](minecraft_server.md).

### Browser Vision Agent

`browser_task_run` має один narrow experimental target:
`humanbenchmark_aim`. Це не generic browser automation. Див.
[`browser_vision_agent.md`](browser_vision_agent.md).

### Browser Observer

- `browser_watch_start <profile>`;
- `browser_watch_stop <profile-or-watch-id>`;
- `browser_watch_status`;
- `browser_watch_events`;
- `browser_watch_poll_once <profile>`.

Profiles config-backed. Observer лише detect/emit/log/notify й не виконує click,
keypress або form submit. Filters і result contracts:
[`browser_observer.md`](browser_observer.md).

## Context repair

Arvis зберігає до 10 останніх command results у RAM. Короткі фрази на кшталт
`ще`, `ще раз` можуть повторити очевидні volume/media actions, а `назад` —
виконати лише однозначний reverse, наприклад `music_pause` → `music_play`.
Неочевидний reverse не виконується.

Це command context, а не довготривала user memory.

## Router outcomes

Основні status values:

- `executed` — action виконано;
- `dry_run` — показано preview;
- `blocked_dangerous` — небезпечна дія або destructive text;
- `blocked_confirmation_required` — потрібен неімплементований confirmation flow;
- `unsupported` — safe intent відомий, але executor не підтримується;
- `not_configured` — бракує явної локальної конфігурації;
- `unknown_action` / `unknown_target` — значення не у whitelist;
- `ambiguous` — параметр неможливо визначити безпечно;
- `command_failed` — дозволений external tool повернув failure.

Router result має пріоритет над raw model answer для handled actions. Debug mode
може окремо показати raw assistant response та diagnostic panels.
