# Аудит Context & Memory Core

[← Индекс документации](README.md) · [Архитектура](architecture.md) ·
[Roadmap](../ROADMAP.md)

## Статус документа

Этот документ фиксирует состояние Arvis v0.3.1 на commit
`e452b49e11e19c3c34e815ffec397a30df1e3512` (`v0.3.1`) и задаёт границы для
будущего Context & Memory Core. Аудит не меняет runtime-поведение и не объявляет
описанные ниже target-компоненты реализованными.

Факты проверены напрямую в runtime-коде, тестах и документации. Отдельных
`tests/test_intent_parser.py` и `tests/test_arvis_mcp_server.py` в baseline нет:
main pipeline частично проверяется в `tests/test_main.py`, MCP helpers и import
smoke test — в `tests/test_project_context.py`. Прямых unit tests для
`MEMORY_INTENT`, `build_context_messages()` и history trimming в текущем наборе
тестов не найдено.

## Текущее состояние в одном абзаце

Arvis имеет ограниченную RAM-историю разговора, пустой placeholder для будущего
rolling summary, короткую историю маршрутизированных команд и одноразовый
reload snapshot. Модельный `MEMORY_INTENT` только парсится и отображается. Это не
рабочая долговременная user memory. Отдельная `.arvis_mcp_memory/` принадлежит
MCP workflow coding agents и не участвует в обычном Arvis chat.

## Текущий conversation context flow

Успешный обычный text turn проходит так:

```text
user input
  → active_history.append({"role": "user", "content": user_text})
  → build_context_messages(active_history, session_summary)
  → optional session_summary as a system message
  → last 40 active messages, including the current user message
  → Ollama /api/chat
  → parse_assistant_response(raw_response)
  → Intent Resolver / Command Router / Response Renderer
  → active_history.append(assistant history text)
  → trim_history_with_summary_placeholder()
```

### Создание и формат

`main.main()` создаёт при старте процесса:

```text
active_history: list[dict[str, str]] = []
session_summary = ""
command_history: list[dict[str, object]] = []
```

Запись active history имеет только ключи `role` и `content`. Нормальный pipeline
создаёт roles `user` и `assistant`. При restore функция `_valid_active_history()`
проверяет лишь то, что список состоит из словарей, а оба значения являются
строками. Allowlist roles отсутствует: вручную подготовленный reload snapshot
может восстановить произвольный string role, после чего context builder передаст
его Ollama без дополнительной проверки.

### Добавление user message и вызов Ollama

`process_user_text()` добавляет user message до построения model context. Поэтому
current user message уже является последней записью `active_history` и отдельно
не добавляется. `build_context_messages()` берёт последние
`MAX_HISTORY_MESSAGES == 40` записей. Если перед turn история уже содержит 40
messages, новый user message временно делает её длиной 41, а самый старый message
не попадает в этот Ollama request.

`OllamaClient.chat()` отправляет этот список как `messages` в `/api/chat` вместе
с model name, `stream: false` и `keep_alive: "30s"`. Приложение не добавляет в
обычный chat request отдельный system prompt: единственный создаваемый здесь
system message — непустой `session_summary`. В репозитории также нет Modelfile
или другого tracked model-level system prompt; внешний Ollama model template не
является частью этого audit scope.

### Поведение при Ollama error

При connection, timeout, HTTP, JSON или response-shape error клиент возвращает
error string. `process_user_text()` показывает `OLLAMA ERROR`, удаляет только что
добавленный последний user message через `active_history.pop()` и возвращает
неизменённые `session_summary` и command counter. Parser, resolver, router,
assistant append и trimming в этом path не запускаются.

### Какой assistant text сохраняется

После parsing и action pipeline `render_final_response()` формирует видимый
ответ. Если был получен любой final router result — включая dry-run, block,
unsupported или execution result — в assistant history сохраняется именно
`final_response`. Raw model message в этом случае не становится conversation
history, хотя в debug mode он может быть показан отдельной панелью.

Если final router result отсутствует, в history сохраняется `parsed.message`.
Это совпадает с `final_response` для текущего renderer path без router result.
Structured `ACTION_INTENT` и `MEMORY_INTENT` отдельно в active history не
записываются.

### Trimming

Trimming вызывается один раз после успешного assistant append. Если длина не
больше 40, ничего не меняется. Иначе вычисляется точное число overflow entries,
самые старые записи передаются в `update_session_summary()`, а затем удаляются:

```text
overflow_count = len(active_history) - 40
overflow_messages = active_history[:overflow_count]
del active_history[:overflow_count]
```

Удаление message-based, а не turn-based: функция не гарантирует сохранение
целой пары user/assistant. В обычном устойчивом потоке после полного turn часто
удаляются две записи, но контракт этого не требует.

### Voice path

Только `/voice once` может войти в conversation flow. После explicit one-shot
recording, transcription и quality/language checks распознанный текст передаётся
в тот же callback `process_user_text()`, поэтому становится обычным user message
и использует тот же summary, Ollama, parser, router и trimming path. `/voice
test` и `/voice diagnose` не вызывают text pipeline и не добавляют transcript в
active history.

## Аудит `active_history`

### Назначение

Сейчас `active_history` даёт Ollama последние сообщения текущего разговора и
поддерживает `/history`. Она не оценивает tokens, не извлекает relevance, не
разделяет диалог и технические ответы и не является долговременным хранилищем.

### Lifecycle

| Событие | Реальное поведение v0.3.1 |
| --- | --- |
| Startup | Создаётся пустой list в RAM; затем может быть восстановлен из reload snapshot. |
| User turn | User message добавляется до Ollama request. |
| Ollama error | Последний user message удаляется; turn не сохраняется. |
| Successful turn | Добавляется assistant message, затем история сокращается до 40 entries. |
| `/reset` | Вызывается только `active_history.clear()`. |
| `/reload`, `/restart` | Текущий list сериализуется в reload snapshot и восстанавливается с лимитом 40. |
| `/exit`, EOF, interrupt | История не сохраняется; процесс завершает её lifetime. |
| Crash без подготовленного reload | История теряется. |

### Ограничения

- Лимит равен 40 messages, а не tokens или characters.
- Размер `content` не оценивается и не ограничивается.
- Overflow summary не обновляется, поэтому ранний контекст теряется.
- Relevance filtering отсутствует: все последние entries равноправны.
- Action-aware rendered text может занимать history даже тогда, когда для
  будущего разговора важна только короткая семантика результата.
- Диалог пользователя и технический/action context хранятся в одном формате.
- Trimming не понимает turns и может удалить половину пары.
- Restore принимает произвольные string roles и content без role allowlist или
  size bound.

### Неясные semantics, которые нельзя исправлять в рамках аудита

Текущее `/reset` очищает только active history. Оно не очищает
`session_summary`, `command_history` или их counters. Следовательно, если summary
был восстановлен как непустой, он продолжит входить system message после
`/reset`; короткие команды также могут продолжить использовать старый command
context. Код не определяет, является ли это желаемой будущей семантикой.

После обычного выхода active history не сохраняется. Reload persistence
существует только для переноса состояния через замену процесса и отличается от
долговременной памяти отсутствием durable lifecycle, retrieval, user-facing
management и memory policy.

## Аудит `session_summary`

- Начальное значение — пустая строка в `main.main()`.
- Значение живёт как локальная RAM-переменная REPL и передаётся между функциями
  через return values.
- `/summary` показывает значение в панели; для пустой строки выводится явный
  placeholder.
- `build_context_messages()` добавляет непустой summary первым message с role
  `system` и префиксом `Короткий підсумок попередньої розмови:`.
- Отдельного application-level system message в normal chat context нет.
- `/reload` и `/restart` записывают строку в reload snapshot; startup принимает
  любое string значение без ограничения длины и восстанавливает его.
- `update_session_summary(current_summary, old_messages)` должен был бы
  обрабатывать overflow, но сейчас безусловно возвращает `current_summary` и не
  читает `old_messages`.
- Updater вызывается только при успешном turn, когда assistant append поднял
  длину active history выше 40.
- При overflow старые messages удаляются даже несмотря на то, что summary не
  изменился.
- Лимита summary по tokens или characters нет. Непустое значение может
  разрастись только через внешне подготовленный reload state или будущую
  реализацию, но текущая граница этого не предотвращает.
- Нет защиты от prompt injection внутри summary, secret/path filtering,
  language allowlist, schema, format validation или provenance.

Таким образом, `session_summary` — подготовленная integration boundary, а не
рабочий rolling conversation summary.

## Аудит reload state

`.runtime/reload_state.json` следует классифицировать как **transport snapshot
между перезапусками процесса**, а не как долговременную память пользователя.

### Запись и поля

Файл best-effort создаётся только при `/reload` или `/restart` до вызова
`os.execv()`. REPL path передаёт все следующие поля:

| Поле | Содержимое |
| --- | --- |
| `dry_run` | Текущий router mode. |
| `debug` | Текущий debug flag. |
| `session_summary` | Текущая строка summary. |
| `active_history` | Текущие conversation entries. |
| `command_history` | Короткий action context. |
| `command_counter` | Счётчик принятых command history entries. |

`save_reload_state()` позволяет не передавать три последних optional state
группы, но normal `/reload` передаёт их. JSON записывается напрямую через
`Path.write_text()`.

### Одноразовое чтение и failure behavior

`load_reload_state()` возвращает dict или `None`, а в `finally` пытается удалить
файл независимо от результата чтения. Поэтому корректный, non-object, invalid
JSON, invalid UTF-8 и read-error snapshot являются one-shot, если unlink
успешен. Invalid JSON не ломает startup и покрыт unit test.

Если процесс аварийно завершится до `/reload`, snapshot не создаётся. Crash или
failed `os.execv()` после успешной записи может оставить файл до следующего
startup; startup тогда прочитает и удалит его. Crash во время неатомарной записи
может оставить partial JSON, который следующий startup отбросит.

### Restore validation и limits

`restore_runtime_state()` валидирует поля независимо, поэтому возможен partial
restore:

- `dry_run` и `debug` должны быть bool;
- `session_summary` должен быть string, без size/content validation;
- `active_history` должен быть list словарей со string `role` и `content`; весь
  список отклоняется при одной invalid entry, roles не allowlisted, после
  validation берутся последние 40 entries;
- `command_history` должен быть list словарей; внутренние поля и их значения не
  валидируются, после validation берутся последние 10 entries;
- `command_counter` принимается как non-negative `int`; при отсутствии
  подходящего значения используется максимальный integer counter из
  восстановленной command history.

### Storage safety

Запись не atomic: нет temporary file, `fsync()` и `os.replace()`. Код не задаёт
явно restrictive mode для уже существующей или новой `.runtime` directory и
самого файла; фактические permissions зависят от umask и существующего path.
Snapshot ignored через `.runtime/`, но может содержать полный недавний диалог,
summary и исходный `user_text` из command history. Поэтому в нём может оказаться
чувствительный текст, несмотря на временное назначение.

## Аудит `MEMORY_INTENT`

Текущий pipeline:

```text
raw model response
  → remove internal reasoning
  → case-insensitive MEMORY_INTENT marker extraction
  → balanced JSON object extraction
  → json.loads() and object check
  → MemoryIntent(**payload)
  → ParsedAssistantResponse.memory_intent
  → unconditional MEMORY INTENT diagnostic panel when present
```

Parser сначала извлекает `ACTION_INTENT`, затем из оставшегося текста —
`MEMORY_INTENT`. Marker block удаляется из видимого assistant message. Decode и
Pydantic failures превращают intent в `None`; warnings показываются только при
debug.

### Реальный контракт модели

| Свойство | Состояние v0.3.1 |
| --- | --- |
| Поля | Обязательные `type: str` и `content: str`. |
| `type` enum/allowlist | Нет; принимается произвольная строка. |
| Максимальная длина `content` | Нет. |
| Empty/whitespace validation | Нет специальной проверки. |
| Sanitization/secret filtering | Нет. |
| Deduplication/conflict handling | Нет. |
| Confidence | Нет поля. |
| Source/provenance | Нет поля. |
| Timestamp | Нет поля. |
| Stable identifier | Нет поля. |
| Create/update/delete semantics | Не определены. |
| Router/storage writer | Отсутствуют. |

`MEMORY_INTENT` является недоверенным model output. Успешный Pydantic parsing
проверяет только минимальную форму и не превращает payload в безопасную
операцию. Сейчас intent никуда не маршрутизируется и не сохраняется. Будущий
`MemoryIntentValidator` и `MemoryRouter` должны независимо повторно проверять
тип, content, policy, secrets и requested operation; parsing нельзя считать
storage authorization.

## Аудит command history

`command_history` — bounded action context, а не conversation transcript и не
user memory. После router result `record_command_history()` записывает entry
только для allowlisted action и пропускает safety block или требование
confirmation. Dry-run результат тоже может быть записан.

Entry содержит counter, исходный `user_text`, normalized action/target, params и
`executed`. List обрезается до 10 entries. Resolver читает последние подходящие
поля для repeat/reverse heuristics; bounded LLM fallback включает последние пять
entries в собственный system prompt. `/reset` command history не очищает.
`/reload` переносит последние 10 entries, обычный exit их теряет.

## Аудит MCP project memory

`.arvis_mcp_memory/` — локальная project-scoped память для standalone MCP Context
Servant и coding-agent workflow.

### Контракт

- Project root выбирается из tool argument, `ARVIS_MCP_PROJECT_ROOT` или current
  working directory.
- Допустимы только точные filenames: `architecture.md`, `commands.md`,
  `decisions.md`, `facts.md`, `known_bugs.md`, `task_history.md`.
- `memory_read()` читает prefix с default limit 12,000 characters; caller limit
  bounded диапазоном 200..50,000. Missing file возвращает `exists: false`.
- `memory_append()` требует non-empty text, обрезает stripped note до 4,000
  characters и append-only добавляет UTC timestamp с точностью до секунды и
  sanitized `source` длиной до 80 characters.
- Memory directory создаётся с requested mode `0700`; append file mode отдельно
  не закреплён и зависит от umask/existing file.
- Filename allowlist и resolved-parent check блокируют path traversal и
  произвольные paths.
- `.arvis_mcp_memory` явно исключена из `project_map`, `grep_project` и direct
  safe project path access. Доступ к ней идёт только через memory helpers.
- Directory находится в `.gitignore`.
- Update, delete, stable record identifiers, deduplication, relevance retrieval,
  per-record expiry и compaction отсутствуют.
- Normal Arvis REPL не импортирует MCP memory helpers и не инъецирует их
  содержимое в Ollama chat.

**MCP project memory ≠ user memory.** MCP memory должна оставаться привязанной к
project root и coding-agent workflow. Её не следует объединять с будущей user
memory в один storage или автоматически передавать в пользовательский chat.

## Классификация состояния

| Сущность | Scope | Lifetime | Persistence | Writer | Reader | Назначение |
| --- | --- | --- | --- | --- | --- | --- |
| `active_history` | Один conversation process | Process; переносится через explicit reload | RAM + optional one-shot snapshot | Main text pipeline | Normal Ollama context, `/history` | Последние 40 conversation messages |
| `session_summary` | Текущий conversation | Process; переносится через explicit reload | RAM + optional one-shot snapshot | Сейчас никто не обновляет; future summarizer | Normal Ollama context, `/summary` | Будущий сжатый старый диалог |
| `command_history` | Короткий action context | Process; переносится через explicit reload | RAM + optional one-shot snapshot | Router pipeline после допустимого result | Deterministic resolver и его bounded LLM fallback | Repeat/reverse repair последних команд |
| Reload state | Process transport | Одно чтение после restart | One-shot ignored JSON file | `runtime_state.save_reload_state()` | Startup restore | Перенос runtime state через `os.execv()` |
| User memory | Один local owner, reserved `profile_id: "default"` | Long-term | Не реализована | Future Memory Router | Future Memory Retriever / Context Builder | Устойчивые факты и предпочтения пользователя |
| MCP project memory | Один project root | Long-term local | Ignored append-only Markdown files | MCP client через `memory_append` | Coding agents через `memory_read` | Project facts, decisions и task notes |

## Supporting state, которое не является user memory

- **Command history** хранит только bounded контекст action repair. Автоматически
  превращать команды в предпочтения нельзя: dry-run, случайная или одноразовая
  команда не означает устойчивый факт.
- **Reload snapshot** — технический one-shot transport. В нём нет consent,
  relevance, expiry или durable memory semantics.
- **Browser Observer journal** в
  `.runtime/browser_observer/events.jsonl` содержит observation events для
  query/subscriber/debug workflow. Событие страницы не является фактом о
  пользователе и может содержать чувствительный page context.
- **Doctor reports** — on-demand диагностический вывод о runtime и config. Он не
  должен становиться profile fact или попадать в prompt автоматически.
- **Runtime logs** описывают subsystem activity и failures. Это telemetry для
  диагностики, а не подтверждённые предпочтения.
- **Voice debug WAV** — optional сырой audio sample в ignored runtime path. Это
  особо чувствительный debug artifact, а не memory input.
- **Action history** как отдельный generic store в baseline не реализована;
  ближайший механизм — `command_history`, а browser task events являются debug
  telemetry. Будущий action audit log также нельзя автоматически повышать до
  user memory.
- **Raw model responses** живут как локальные значения во время turn и могут
  показываться в debug. Они недоверенные и не должны сохраняться целиком; в
  active history попадает только parsed message или action-aware rendered text.

## Три независимых memory domains

### A. Conversation context

Содержит recent messages, rolling summary текущей беседы и, при отдельном
контракте, текущую цель разговора. Этот domain ограничен по размеру и времени и
не является вечной памятью. Conversation summarization не должна автоматически
создавать durable user facts.

### B. User memory

Содержит только устойчивые факты и предпочтения, полезные в будущих разговорах:

- preference;
- profile fact;
- recurring constraint;
- long-term goal;
- relationship или named-entity context;
- explicit instruction to remember.

User memory не должна хранить passwords, access tokens, cookies, private keys,
одноразовые коды, полный raw dialogue, случайные временные эмоции без явной
ценности, технический command output или project content, относящийся к MCP
memory.

### C. Project/Coding-agent memory

Содержит architecture decisions, known bugs, project facts, task history и
command conventions. Она остаётся привязанной к project root и обслуживается
MCP Context Servant независимо от user chat.

## Target architecture

User memory pipeline:

```text
Model MEMORY_INTENT
        ↓
MemoryIntentValidator
        ↓
MemoryPolicy / secret filtering
        ↓
MemoryRouter
        ↓
UserMemoryStore
        ↓
MemoryRetriever
        ↓
ContextBuilder
        ↓
Ollama request
```

Conversation summary pipeline:

```text
overflow messages + previous summary
        ↓
ConversationSummarizer
        ↓
SummaryValidator
        ↓
bounded session_summary
        ↓
ContextBuilder
```

Project memory остаётся отдельной:

```text
coding agent
        ↓
MCP memory_read / memory_append
        ↓
.arvis_mcp_memory/
```

Ни один из трёх pipelines не должен напрямую писать в storage другого domain.

## Границы будущих компонентов

### `ConversationSummarizer`

Получает previous summary и overflow messages, возвращает bounded candidate
summary. Не пишет user memory, не маршрутизирует и не выполняет actions. Ошибка
summarization должна оставлять определённый safe fallback, а не блокировать
основной REPL без контракта.

### `SummaryValidator`

Проверяет size, форму, допустимые roles/sources и отсутствие инструкций,
маскирующихся под system policy. Он должен рассматривать summary как
недоверенное сжатое содержимое разговора и ограничивать secret/path leakage.

### `MemoryIntentValidator`

Не доверяет model output. Нормализует operation/type/content, применяет size
limits, отклоняет пустой или неоднозначный intent, блокирует secrets и unsafe
categories и добавляет контролируемую provenance. Его результат — data
candidate, не authorization на запись.

### `MemoryPolicy`

Определяет разрешённые категории, confirmation requirements, redaction,
retention и правила для conflict/duplicate. Policy должна быть детерминированной
и тестируемой без Ollama.

### `MemoryRouter`

Принимает только validated operation и решает `create`, `update`, `delete` или
`no-op`. Не зависит напрямую от Rich/REPL rendering и не принимает raw model JSON
как store command.

### `UserMemoryStore`

Использует local ignored storage, atomic writes, stable identifiers, versioned
schema и понятное corruption handling. Store не отвечает за prompt selection и
не смешивает данные с `.arvis_mcp_memory/`.

### `MemoryRetriever`

Выбирает только релевантные записи по текущему запросу и bounded metadata,
применяет жёсткий count/character или token budget и не возвращает всю базу в
каждый prompt.

### `ContextBuilder`

Собирает model context слоями:

```text
system instructions
conversation goal
rolling summary
relevant user memories
recent active history
current user message
```

Текущий `build_context_messages()` реализует лишь optional summary и последние
active messages; current user message уже находится внутри active history.
Будущий builder должен либо принимать current message отдельно, либо исключать
его из переданной history, чтобы не дублировать. Summary и memories должны быть
явно отделены как untrusted contextual data от system instructions. Итоговый
список сохраняет существующий `list[dict[str, str]]` contract для
`OllamaClient.chat()`.

## Сравнение storage для первой User Memory Store

| Критерий | Один versioned JSON-файл | JSONL / event log | SQLite |
| --- | --- | --- | --- |
| Простота | Самый простой read/inspect/backup для малой базы | Простая append, но сложнее materialized current state | Больше store/repository кода и SQL contracts |
| Atomic updates | Temp file + flush/fsync + `os.replace()` дают atomic snapshot | Одна append обычно проста, но partial tail всё равно надо обрабатывать | Transactions и WAL дают сильную atomicity |
| Update/delete | Прямая замена записи в snapshot | Нужны tombstones/events и replay/compaction | Нативные `UPDATE`/`DELETE` |
| Corruption recovery | Рискует весь snapshot; нужны validation и backup/quarantine | Можно отбросить damaged tail, но replay сложнее | Хорошая устойчивость, но нужна обработка DB errors |
| Search/relevance | Linear scan приемлем для bounded v1 | Без индекса также replay/linear scan | Индексы и queries доступны сразу |
| Миграции | Явный top-level `schema_version` и малые pure migrations | Versioning каждого event и materialization | `PRAGMA user_version`/migration scripts |
| Тестирование | Простые temp-directory fixtures и deterministic equality | Нужны replay, duplicate, tombstone и compaction tests | Нужны transactional/schema fixtures |
| Зависимости | Только Python stdlib | Только Python stdlib | `sqlite3` входит в stdlib, внешняя dependency не нужна |
| Приватность | Легко задать single-file permissions и ignored path | Несколько старых значений остаются в log до compaction | Старые pages/WAL требуют отдельной lifecycle policy |
| Соответствие текущему масштабу | Хорошее для десятков/сотен bounded records и одного process writer | Полезно для audit trail, но усложняет delete/forget | Сильный вариант при большем объёме, concurrency и сложных queries |

### Рекомендация

Для первой версии выбрать один небольшой versioned JSON snapshot в отдельном
ignored user-memory path, с одним process writer, strict schema validation,
stable record IDs, size/count limits, atomic temp-file replacement и
quarantine/backup при corruption. Это соответствует масштабу Arvis и делает
create/update/delete и тесты понятными без event-sourcing complexity.

SQLite следует пересмотреть, когда появятся реально измеренные требования к
большому числу записей, индексированным queries или нескольким writers. JSONL не
рекомендуется как canonical current-state store первой версии: privacy-correct
delete и conflict resolution потребуют tombstones и compaction. Ни один storage
не создаётся на этапе аудита.

## Privacy и security gaps

| Риск | Будущая mitigation |
| --- | --- |
| Model-generated memory intent | Считать candidate недоверенным; independent validation и policy, а mutation разрешать только при подходящем explicit user intent или отдельном confirmation flow. |
| Prompt injection в summary или memory | Delimit contextual data, запретить policy-like instructions, применять validators и не давать context право вызывать actions. |
| Случайное сохранение secrets | Deterministic secret patterns, high-entropy checks, denylist categories, redaction и safe no-op. |
| Абсолютные paths | Path detector/redaction; хранить только если категория явно разрешена и пользователь подтвердил необходимость. |
| Слишком личная информация | Category policy, minimization, explicit preview/consent и простой review/delete UX. |
| Бесконтрольный рост | Count/size limits, bounded fields, retention и deterministic eviction/review policy. |
| Дубли | Нормализованный fingerprint и semantic key внутри категории до create. |
| Конфликтующие записи | Stable subject/category key, показать old/new, explicit replace или сохранить conflict для review. |
| Устаревшие записи | В v1 хранить до explicit update/delete и обновлять `updated_at`; TTL, automatic expiry и temporary memory отложены. |
| Повреждённый storage | Atomic replace, schema validation, last-known-good backup/quarantine и safe empty/read-only fallback. |
| Отправка всей памяти в Ollama | Relevance selection и строгий per-request budget; default deny для unrelated entries. |
| Смешение project и user memory | Раздельные paths, schemas, APIs, writers и context routes; MCP memory не импортировать в normal chat. |
| Очистка без явного разрешения | Exact `/memory forget <id>` разрешает только scoped delete; ambiguous natural-language delete требует preview/clarification, а bulk clear — отдельного confirmation. |
| Попадание local memory в git | Dedicated ignored directory, Doctor/git check и tests, запрещающие tracked memory artifacts. |
| Memory в debug/log output | Redacted summaries/IDs по умолчанию; полный content только в explicit local review command. |

## Accepted decisions for the first implementation

Следующие решения приняты для первой версии Context & Memory Core. Они задают
target semantics будущих этапов и не меняют поведение baseline v0.3.1 в рамках
этого документационного этапа.

### `/reset` начинает новую conversation session

Будущий `/reset` должен:

- очистить `active_history`;
- очистить `session_summary`;
- очистить `command_history`;
- сбросить command counter;
- создать новый conversation/session identifier;
- оставить user memory без изменений;
- оставить MCP project memory без изменений;
- сохранить текущие dry-run и debug settings.

Baseline v0.3.1 пока вызывает только `active_history.clear()`. Описанное выше —
целевая семантика будущей реализации, а не текущее runtime-поведение.

### User-memory scope

Первая версия рассчитана на одного локального владельца Arvis. Отдельный
пользовательский UI и полноценные мультипрофили не реализуются. Versioned
storage schema может зарезервировать нейтральное поле:

```json
{
  "profile_id": "default"
}
```

Поле не должно усложнять первый store, routing или UI. Project-scoped MCP memory
остаётся отдельной системой с отдельным path и API.

### Явная memory-команда является разрешением на конкретную запись

Явные формулировки пользователя считаются разрешением на одну конкретную
create-операцию, например:

```text
запомни, что...
помни, что...
remember that...
```

После validation и policy checks второй бессмысленный вопрос confirmation для
такой create-операции не нужен. При этом:

- model-generated `MEMORY_INTENT` сам по себе никогда не является разрешением;
- обычный разговор не пишет в storage;
- inferred preferences не сохраняются автоматически;
- ambiguous memory request приводит к clarification или safe no-op;
- validator/router рассматривает `MEMORY_INTENT` только при подходящем explicit
  user intent.

### Automatic preference capture отключён

Arvis первой версии не сохраняет молча preferences, profile facts или эмоции из
обычного разговора. Automatic suggestions можно рассмотреть позже, но suggestion
не должна записываться без явного действия пользователя.

### Conflict handling требует подтверждённого update

Противоречащая новая информация не перезаписывает старую молча:

```text
new candidate
  → retrieve possible conflict
  → show existing record
  → ask whether to update
  → confirmed update
```

При подтверждённом update сохраняется stable record ID, меняется
нормализованное содержимое и обновляется `updated_at`. Две одновременно активные
противоречащие записи не создаются. Полный audit history прежних значений в
первую версию не входит.

### User-memory retention постоянный

Записи первой версии существуют до явного изменения или удаления. TTL,
automatic expiry, temporary memories и очистка по давности не реализуются.
Кратковременные сведения должны отклоняться policy и не попадать в user memory.

### Raw `MEMORY_INTENT` скрыт в обычном UI

Raw model payload:

- не показывается в обычном пользовательском интерфейсе;
- может показываться в explicit debug/trace mode;
- не записывается напрямую;
- передаётся validator/router только при подходящем explicit user intent.

После успешной explicit memory-команды Arvis показывает короткий человеческий
результат, а не raw JSON intent. Baseline v0.3.1 сейчас показывает распарсенный
`MEMORY_INTENT` без условия debug; будущая реализация должна изменить это
отдельным протестированным runtime patch.

### Delete/forget semantics

Целевой минимальный UX:

```text
/memory list
/memory search <text>
/memory show <id>
/memory forget <id>
```

Точная команда `/memory forget <id>` сама разрешает удаление одной конкретной
записи и не требует второго confirmation. Natural-language delete, например
`забудь, что я люблю ...`, сначала ищет запись; неоднозначный match требует
показать candidates и уточнить выбор. Массовая очистка всей user memory требует
отдельного явного confirmation. `/reset` user memory не удаляет.

### Rolling-summary lifetime ограничен conversation session

Rolling summary:

- существует только в текущей conversation session;
- сохраняется через `/reload`;
- не является долговременной user memory;
- очищается при `/reset`;
- не восстанавливается после обычного завершения Arvis и нового запуска.

Future explicit session persistence не входит в первую реализацию.

### Conversation/session identifier — случайный UUID

Каждая новая conversation session получает случайный UUID:

- новый запуск создаёт новый ID;
- `/reset` создаёт новый ID;
- `/reload` сохраняет существующий ID;
- ID не является user memory;
- ID не обязан передаваться Ollama;
- ID может использоваться в runtime state, diagnostics и будущей explicit
  session persistence.

## Deferred decisions

Для первой реализации отложены:

- полноценные пользовательские профили;
- automatic memory suggestions;
- temporary memory и TTL;
- audit history всех прежних значений;
- сохранение беседы после обычного выхода;
- восстановление старых conversation sessions;
- embedding/vector retrieval;
- переход с JSON storage на SQLite;
- несколько concurrent writers.

Эти решения не блокируют Rolling Summary Contract или последующие bounded
компоненты первой версии.

## Последовательность реализации

```text
Stage 2: Rolling Summary Contract
Stage 3: Rolling Summary Implementation
Stage 4: User Memory Schema and Store
Stage 5: Memory Validation and Policy
Stage 6: Memory Router
Stage 7: Retrieval and Context Builder
Stage 8: REPL Memory Commands
Stage 9: Hardening, migrations and documentation
```

### Stage 2: Rolling Summary Contract

- **Цель:** определить input/output, budget, overflow selection, failure fallback,
  trust boundary, language/format, UUID lifecycle и принятые semantics `/reset`,
  reload и normal exit.
- **Предполагаемые файлы:** новый contract doc, этот audit doc и при
  необходимости `docs/architecture.md`/`docs/commands.md`; runtime-код не менять.
- **Необходимые tests:** описать acceptance cases для pair boundaries, oversized
  content, invalid roles, summarizer failure, reload, reset и session UUID;
  executable tests появятся с implementation.
- **Не входит:** user-memory schema/storage, Memory Router, memory commands,
  summarizer implementation и новые env keys.

### Stage 3: Rolling Summary Implementation

- **Цель:** реализовать bounded summary update и безопасный conversation-context path
  для overflow без потери messages до подтверждённого summary result.
- **Предполагаемые файлы:** `main.py`, `runtime_state.py`, новый небольшой
  conversation summary module и специализированные unit tests.
- **Необходимые tests:** update/no-overflow, turn boundary, model error, invalid
  summary, size bound, injection-like output, `/reset`, reload restore, normal
  exit и exact context ordering с mocked Ollama.
- **Не входит:** durable user memory, `MEMORY_INTENT` mutation, memory commands.

### Stage 4: User Memory Schema and Store

- **Цель:** versioned record schema и ignored atomic JSON store со stable IDs,
  reserved `profile_id: "default"`, corruption handling и CRUD API без model
  integration.
- **Предполагаемые файлы:** новый user-memory schema/store module, `.gitignore`,
  Doctor storage check при необходимости, store tests и storage contract doc.
- **Необходимые tests:** create/read/update/delete, stable ID update, atomic
  replacement failure, malformed/unknown schema version, limits, permissions,
  ignored path и deterministic serialization.
- **Не входит:** automatic writes, retrieval в prompts, REPL commands,
  multiprofile UI, SQLite и concurrent writers.

### Stage 5: Memory Validation and Policy

- **Цель:** deterministic validation, category allowlist, explicit-intent gate,
  secret/path filtering, minimization и conflict candidates.
- **Предполагаемые файлы:** новые validator/policy modules, memory schemas и
  focused tests.
- **Необходимые tests:** explicit/implicit intent, empty/oversized content,
  tokens, keys, cookies, OTP, absolute paths, short-lived facts, false positives,
  duplicates и conflicts.
- **Не входит:** storage mutation orchestration, context retrieval, UI,
  automatic suggestions и TTL.

### Stage 6: Memory Router

- **Цель:** преобразовывать только validated operation в
  create/update/delete/no-op с принятыми authorization и conflict rules.
- **Предполагаемые файлы:** новый router/service module, main integration
  boundary, human-readable outcome rendering и router tests.
- **Необходимые tests:** explicit create без второго confirmation, raw intent
  rejection, confirmed conflict update, exact-ID delete, ambiguous delete,
  idempotency, store failure и отсутствие writes при no-op.
- **Не входит:** relevance retrieval и полный REPL command set.

### Stage 7: Retrieval and Context Builder

- **Цель:** bounded relevance selection и ordered context layers
  system/goal/summary/memories/history/current message без duplication.
- **Предполагаемые файлы:** retriever, dedicated context builder, `main.py`,
  Ollama boundary tests и architecture docs.
- **Необходимые tests:** category/subject relevance, unrelated exclusion,
  stable ordering, hard budget, exact roles/order, untrusted-data delimiters,
  current message exactly once и no MCP-memory import.
- **Не входит:** vector/embedding dependency, memory management UI, SQLite.

### Stage 8: REPL Memory Commands

- **Цель:** реализовать list/search/show/forget и natural-language memory flows с
  redaction, clarification и отдельным confirmation только для bulk clear.
- **Предполагаемые файлы:** `main.py` command handling, memory service, response
  rendering, `docs/commands.md` и command tests.
- **Необходимые tests:** list/show redaction, search, exact-ID forget без второго
  confirmation, ambiguous natural-language match, cancel, bulk confirmation,
  corrupt store и separation from `/reset`.
- **Не входит:** background capture, automatic suggestions, profile UI, MCP
  commands.

### Stage 9: Hardening, migrations and documentation

- **Цель:** закрепить final contracts, privacy model, backup/recovery и explicit
  schema migration procedure.
- **Предполагаемые файлы:** specialized memory docs, docs index, architecture,
  commands, configuration/Doctor docs при фактической необходимости и migration
  tests/code только для реально выпущенных schema versions.
- **Необходимые tests:** supported upgrade fixture, unknown future version,
  interrupted migration, backup restore, git-ignore safeguards и no-secret docs
  fixtures.
- **Не входит:** speculative migrations, SQLite migration, session archive,
  multiprofile или concurrent-writer support.

## Вывод

Baseline уже имеет удобные integration boundaries, но ни один из них нельзя
считать готовой долговременной user memory. Следующий безопасный шаг — сначала
зафиксировать Rolling Summary Contract, не связывая его с `MEMORY_INTENT` или
`.arvis_mcp_memory/`, и только затем реализовать bounded conversation summary с
отдельными failure и privacy guarantees.
