# Arvis

Arvis — локальний консольний AI-асистент для Linux: чат через Ollama, безпечні
desktop actions, діагностика та контрольовані browser-сценарії. Проєкт
розвивається як надійний інструмент для власної машини, а не як автономний бот.

## Стан

Поточний public baseline — **Arvis v0.3.1**.

- Версія застосунку зберігається у [`VERSION`](VERSION).
- Реалізоване в baseline зібране у [`CHANGELOG.md`](CHANGELOG.md).
- Наступні напрями описані у [`ROADMAP.md`](ROADMAP.md).
- Детальна документація починається з [`docs/README.md`](docs/README.md).

Номери на кшталт Browser Observer schema v1 — це версії окремих форматів і
контрактів, а не версія всього Arvis.

## Що таке Arvis

Arvis працює локально у terminal REPL, надсилає діалог до Ollama `/api/chat` і
може перетворювати явні або природні команди на вузькі whitelisted actions.
Основна мова user-facing інтерфейсу — українська з неформальним тоном Арвіса;
ідентифікатори й технічні терміни лишаються англійськими.

Проєкт свідомо тримає окремими:

- відповідь моделі та structured intents;
- розпізнавання команди в Intent Resolver;
- фінальну перевірку й маршрутизацію в Command Router;
- виконання конкретної дозволеної дії;
- формування відповіді через Response Renderer.

Такий поділ дає змогу тестувати кожен safety boundary окремо.

## Поточні можливості

- Rich REPL-чат із локальною Ollama-моделлю.
- Активна історія до 40 повідомлень і мінімальний reload state.
- Парсинг `ACTION_INTENT` і `MEMORY_INTENT`.
- Deterministic/LLM Intent Resolver для команд українською, російською та
  англійською.
- Command Router як фінальний whitelist і safety gate.
- Dry-run за замовчуванням із preview дозволених дій.
- Action-aware Response Renderer.
- Doctor Mode для runtime, config, privacy, Ollama, actions, storage і git.
- `/reload` та `/restart` зі збереженням безпечного runtime state.
- Опційний ручний one-shot voice input без background listening.
- Media, volume, app/site launch і Minecraft Server Manager.
- Experimental Browser Vision Agent для одного вузького whitelisted demo task.
- Observation-only Browser Observer з JSONL events і in-process watcher lifecycle.
- Arvis MCP Context Servant як компактний stdio-сервіс фактів з ізоляцією
  коренів проєктів, окремими профілями доступу `codex` і read-only `chatgpt`,
  а також обмеженою read-only перевіркою OS/runtime, поточних системних
  метрик, RPM, Plasma, Qt і QML.

`session_summary` підтримує валідоване rolling-стиснення активної історії.
`MEMORY_INTENT` парситься і показується, проте ще не створює окрему довготривалу
пам'ять користувача.

## Швидкий старт

Потрібен Python 3.11+ та локальний Ollama з доступною chat-моделлю. За
замовчуванням Arvis використовує модель `arvis`; іншу можна задати через
`ARVIS_MODEL`.

```bash
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Відредагуй `.env` під свою машину, не додаючи його до git. Потім:

```bash
.venv/bin/python main.py doctor
.venv/bin/python main.py
```

Без `.env` застосунок використовує safe defaults для Ollama; більшість desktop,
voice, browser і Minecraft можливостей залишаться disabled, unavailable або
повернуть зрозумілий `not_configured`.

Повна інструкція: [`docs/getting_started.md`](docs/getting_started.md).

## Модель безпеки

- **Local-first.** Основний runtime і дані працюють на машині користувача.
- **Dry-run by default.** Новий `CommandRouter` стартує з `dry_run=True`.
- **Command Router is the final gate.** Resolver лише пропонує candidate intent і
  не виконує дій.
- **Whitelists, not arbitrary control.** Actions, apps, sites, browser tasks,
  observer profiles і Minecraft targets обмежені відомими наборами.
- **Public observer = eyes/events.** Він може detect, emit, log і notify.
- **Private extension = hands/actions.** Особисті click/keypress сценарії мають
  жити лише в ignored local extensions, поза public code path.
- **Доступ MCP визначається профілем.** Локальні корені та вибір профілю MCP
  зберігаються лише в ignored `.env`/`.env.local`; `chatgpt` без явного списку
  дозволених коренів працює fail-closed.
- **System MCP лише спостерігає.** Він має тільки фіксовані read-only запити до
  хоста, пакунків і QML: без shell, `sudo`, install/remove/update, оновлення
  репозиторіїв або запуску GUI.

Arvis не виконує raw shell із відповіді моделі, не приймає arbitrary URL для
browser tasks і не обходить login, CAPTCHA, payment, download або permission
flows.

Докладніше: [`docs/architecture.md`](docs/architecture.md) і
[`ROADMAP.md`](ROADMAP.md).

## Основні команди

| Команда | Що робить |
| --- | --- |
| `/help` | Показує всі REPL-команди. |
| `/doctor` | Перевіряє локальну готовність Arvis. |
| `/actions` | Показує підтримувані safe actions. |
| `/dryrun` | Показує стан dry-run. |
| `/dryrun on` | Вмикає preview-only режим. |
| `/dryrun off` | Дозволяє виконання лише safe whitelisted actions. |
| `/history` | Показує активну chat history. |
| `/summary` | Показує поточний placeholder `session_summary`. |
| `/voice status` | Показує voice config і optional dependencies. |
| `/voice once` | Записує одну команду й передає її в text pipeline. |
| `/reload` / `/restart` | Перезапускає Python process Arvis. |
| `/reset` | Очищає активну історію. |
| `/exit` / `/quit` | Завершує REPL. |

Кілька natural-language прикладів:

```text
відкрий ютуб
зроби гучніше на 10
що зараз грає
статус майн сервера
покажи статус спостереження
```

Повний довідник: [`docs/commands.md`](docs/commands.md).

## Огляд архітектури

```text
user text
  → Ollama response
  → intent_parser
  → Intent Resolver (candidate only)
  → Command Router (final whitelist/safety gate)
  → whitelisted executor or dry-run preview
  → Response Renderer
```

Browser observation має окрему публічну межу:

```text
observe → detect → emit structured event → log/notify
```

Повна карта модулів, runtime state та memory limitations:
[`docs/architecture.md`](docs/architecture.md).

## Документація

- [`docs/README.md`](docs/README.md) — індекс усієї документації.
- [`docs/getting_started.md`](docs/getting_started.md) — установка й перший запуск.
- [`docs/configuration.md`](docs/configuration.md) — `.env`, defaults і локальні межі.
- [`docs/commands.md`](docs/commands.md) — slash-команди, actions і приклади.
- [`docs/doctor.md`](docs/doctor.md) — Doctor Mode та exit codes.
- [`docs/voice.md`](docs/voice.md) — ручний voice pipeline.
- [`docs/browser_vision_agent.md`](docs/browser_vision_agent.md) — вузький
  experimental controlled-browser task.
- [`docs/browser_observer.md`](docs/browser_observer.md) — observation-only
  profiles, events і schema v1.
- [`docs/minecraft_server.md`](docs/minecraft_server.md) — конфігурація й
  безпечне керування сервером.
- [`docs/mcp_context_servant.md`](docs/mcp_context_servant.md) — fact helper для
  coding agents.
- [`docs/development.md`](docs/development.md) — тести, CI і правила розробки.

## Розробка

Основні перевірки з project root:

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall -q .
.venv/bin/python main.py doctor --json
git diff --check
```

Unit tests не повинні вимагати запущених Ollama, браузера, audio device,
Minecraft server або optional heavy dependencies. Межі зовнішніх систем
мокаються.

Докладніше: [`docs/development.md`](docs/development.md) та [`AGENTS.md`](AGENTS.md).

## План розвитку

Наступний великий напрям — **Context & Memory Core**: окрема пам'ять користувача,
Memory Router, побудова релевантного контексту, керування пам'яттю та privacy
filtering. Rolling conversation summary уже реалізований.

Без дат і обіцянок релізів: [`ROADMAP.md`](ROADMAP.md).
