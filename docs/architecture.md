# Архітектура

[← Індекс документації](README.md) · [Головна сторінка](../README.md)

## Intent/action pipeline

```text
user text
  → main.py
  → Ollama /api/chat
  → intent_parser.py
  → deterministic/LLM Intent Resolver
  → Command Router
  → whitelisted action preview/executor
  → Response Renderer
```

1. `main.py` приймає текст, підтримує REPL state і викликає Ollama.
2. `intent_parser.py` відділяє assistant message, `ACTION_INTENT` і
   `MEMORY_INTENT`.
3. `IntentResolver` шукає безпечний deterministic match або bounded LLM
   candidate. Він не виконує commands.
4. Parsed model intent може розглядатися, але safer final deterministic match
   має пріоритет.
5. `CommandRouter` нормалізує action/target/params і є фінальним whitelist та
   safety gate.
6. Executor викликається лише для дозволеної дії; у dry-run повертається preview.
7. `response_renderer.py` формує user-facing результат із фінального
   `CommandResult`.

Нормальний debug output для однієї команди показує один final routed intent і
один final router result.

## Safety boundary

```text
model/resolver candidate
        │
        ▼
Command Router
  ├─ dry-run preview
  ├─ safe whitelisted executor
  └─ block / clarify / not_configured
```

Hard rules:

- `CommandRouter(dry_run=True)` є default.
- Resolver не маршрутизує і не виконує дії.
- Raw shell, arbitrary executables і model-provided argv не виконуються.
- Apps, site targets, browser tasks, observer profiles і Minecraft targets
  whitelisted/config-backed.
- Confirmation не симулюється: якщо flow не реалізований, action блокується.
- External commands запускаються як argv із `shell=False`.

## Browser boundaries

Public Browser Observer:

```text
observe → detect → emit structured event → log/notify
```

Він працює з visible controlled-browser viewport, allowlisted URLs і configured
profiles. Public observer не клікає, не вводить текст і не керує normal browser
profile.

Experimental Browser Vision Agent — окремий вузький whitelisted task із clean
Playwright context і hard limits. Його не можна перетворювати на reusable
auto-click framework.

Майбутні особисті hands/actions належать тільки ignored local extensions:

```text
public observer event → private local decision → private local action
```

## Conversation context і memory

- Active history живе в RAM і обмежена 40 messages.
- `session_summary` уже передається в context builder, але updater поки
  placeholder і не створює rolling summary.
- `MEMORY_INTENT` парситься та може показуватися у diagnostic panel, але не
  зберігається як user memory.
- До 10 command results зберігаються в RAM для bounded repeat/reverse repair.

Отже, baseline не має SQLite memory, Memory Router або готової довготривалої
персональної пам’яті. Це наступний напрям у [`ROADMAP.md`](../ROADMAP.md).

## Reload/restart state

`/reload` і `/restart` best-effort записують
`.runtime/reload_state.json`, після чого замінюють поточний Python process через
`os.execv()`.

State може містити:

- `dry_run`;
- `debug`;
- placeholder `session_summary`;
- JSON-safe active history;
- JSON-safe command history і counter.

Пошкоджений state не ламає startup і видаляється. Reload зупиняє in-process
Browser Observer watchers, але не зупиняє Ollama, Minecraft server або tmux.
Public watchers не відновлюються автоматично після process restart.

## Module map

| Файл / каталог | Відповідальність |
| --- | --- |
| `main.py` | REPL, slash commands, history, reload і voice entry points. |
| `ollama_client.py` | Ollama `/api/chat` access. |
| `intent_parser.py` | Parsing assistant text та structured intents. |
| `intent_resolver.py` | Candidate intent resolution без execution. |
| `command_router.py` | Final safety gate і dispatcher. |
| `response_renderer.py` | Action-aware user response. |
| `runtime_state.py` | Safe reload state persistence. |
| `doctor.py` | Local diagnostics, redaction і safe fix. |
| `config.py` | Environment/default configuration. |
| `actions/` | Whitelisted media, volume, apps, Minecraft і browser modules. |
| `voice_*.py` | Optional voice config, input, ducking і normalization. |
| `arvis_mcp_server.py` | Standalone stdio MCP servant. |
| `project_context.py` | Bounded project facts/search/excerpts/git/memory helpers. |
| `tests/` | `unittest` coverage з mocked external boundaries. |

Документація subsystem contracts: [Browser Observer](browser_observer.md),
[Minecraft](minecraft_server.md), [Voice](voice.md) і
[MCP Context Servant](mcp_context_servant.md).
