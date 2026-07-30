# Minecraft Server Manager

[← Індекс документації](README.md) · [Головна сторінка](../README.md)

Minecraft integration опційна. Public repository містить generic manager;
реальні server name, cwd і start command задаються лише в локальному `.env`.

## Конфігурація

```dotenv
MINECRAFT_SERVER_ENABLED=true
MINECRAFT_SERVER_KEY=default
MINECRAFT_SERVER_NAME=My Minecraft Server
MINECRAFT_SERVER_CWD=/absolute/path/to/server
MINECRAFT_SERVER_COMMAND=./start.sh
```

Якщо integration disabled, missing або incomplete, router повертає
`status=not_configured` і не сканує/не запускає server. Command розбирається
через `shlex.split()` і запускається з `shell=False`.

## Actions

- `minecraft_server_status`;
- `minecraft_server_start`;
- `minecraft_server_stop`;
- `minecraft_server_restart`;
- `minecraft_server_logs`;
- `minecraft_server_diagnostics`;
- `minecraft_server_metrics`;
- compatibility alias `start_minecraft_server`.

Status, logs, diagnostics і metrics read-only та можуть працювати в dry-run.
Start/stop/restart у dry-run повертають preview.

## Managed lifecycle

- Start перевіряє configured tmux session і server process candidates, щоб не
  створити duplicate.
- Managed start використовує `tmux new-session` із configured cwd і argv.
- Stop надсилає graceful `stop` лише у managed tmux session.
- Restart робить managed graceful stop + start або start, якщо server не running.
- `kill`, `killall` і `pkill` не використовуються.
- Якщо tmux або start script відсутній, повертається `not_configured` із точним
  reason code; Arvis не встановлює tools автоматично.

## Managed і unmanaged processes

- `managed_server` — configured tmux session і strong Java server process,
  пов’язаний із managed launch.
- `unmanaged_server` — strong Java server process у server directory без managed
  tmux session.
- `ignored_client` — Minecraft/PrismLauncher client process, який не можна
  приймати за dedicated server.
- `weak_cwd` — process у server directory без достатніх server markers.

Unmanaged server блокує duplicate start, але Arvis не зупиняє й не рестартить
його. Треба один раз зупинити process вручну, а потім запустити через Arvis.
Weak-only detection повертає `ambiguous`, а не вигадує running state.

Кілька strong server processes позначаються як duplicate detection warning.
Client/launcher detection залишається окремою від server detection.

## Logs, diagnostics і metrics

- Logs читають лише останні 40 рядків `logs/latest.log`.
- Diagnostics показують sanitized tmux/start-script/process view.
- Metrics читають bounded CPU/RAM/process data через `/proc` і `ps` без shell
  pipeline.
- Client processes можуть відображатися окремо, але не враховуються як server.

Відсутні файли або unavailable process details повертають structured status, а
не запускають repair.

## Приклади

```text
статус майн сервера
запусти майн сервер
зупини майн сервер
перезапусти майн сервер
покажи логи майн сервера
діагностика майн сервера
скільки ресурсів їсть сервер
```

Рекомендована ручна перевірка:

```text
/dryrun
статус майн сервера
покажи логи майн сервера
діагностика майн сервера
навантаження майн сервера
/dryrun off
запусти майн сервер
статус майн сервера
зупини майн сервер
```
