# Arvis MCP Context Servant

[← Індекс документації](README.md) · [Головна сторінка](../README.md)

## Загальна поведінка MCP-сервера

Arvis MCP Context Servant надає MCP-сумісним coding agents компактні факти про
проєкт без зайвого витрачання контексту на перше дослідження. Це допоміжний
сервіс фактів, а не основний програміст. Клієнт сам вирішує, що змінювати,
безпосередньо перевіряє файли, редагує код і запускає тести.

Сервер працює окремо від звичайного Arvis REPL і зберігає наявний transport
`stdio`:

```bash
.venv/bin/python arvis_mcp_server.py
```

HTTP і SSE не публікуються. Встановлення та підключення OpenAI Secure MCP
Tunnel навмисно відкладені до окремого наступного етапу.

## Публічна політика та приватні значення

Публічний репозиторій містить механізм профілів, розбір політики, ізоляцію
коренів, redaction, ліміти, annotations, тести й загальну документацію. Він не
залежить від стану Codex, ідентифікаторів ChatGPT, персональних шляхів,
локальних токенів, приватних назв моделей або специфічних для машини припущень.

Вибраний профіль і конкретні корені є приватною конфігурацією машини.
Standalone server спочатку читає явно задане середовище процесу, а потім ignored
`.env.local` і `.env`, не замінюючи явно задані значення.

```text
ПУБЛІЧНИЙ КОД
    -> загальний loader конфігурації ARVIS_MCP_*
    -> ПРИВАТНІ .env.local/.env/середовище процесу
    -> конкретні корені машини та вибраний профіль
```

## Профілі доступу та інструменти

- `codex` — профіль сумісності, який публікує всі чотирнадцять інструментів.
  Якщо список дозволених коренів не налаштований, доступ обмежується
  `ARVIS_MCP_PROJECT_ROOT`, а за відсутності цієї змінної — робочим каталогом
  сервера.
- `chatgpt` працює fail-closed і лише для читання. Він потребує явного значення
  `ARVIS_MCP_ALLOWED_ROOTS` і не публікує `memory_append`.

Обидва профілі публікують такі read-only інструменти:

- `project_map` — обмежена карта безпечних текстових файлів, розмірів, типів і
  розширень.
- `grep_project` — обмежений пошук у безпечних текстових файлах.
- `read_file_excerpt` — обмежений уривок рядків одного безпечного текстового
  файлу.
- `git_status_summary` — обмежений результат фіксованих безпечних команд Git.
- `task_brief` — компактні пошукові підказки для задачі.
- `memory_read` — обмежене читання з `.arvis_mcp_memory/`.
- `system_info` — OS, architecture, kernel, desktop/session, Plasma/Qt, atomic
  status і підтримувані backend перевірки пакунків без ідентифікації машини.
- `binary_exists` — пошук executable за назвою без його запуску.
- `package_installed` — точний пошук у базі RPM хоста.
- `package_info` — дані про встановлений пакунок і, де підтримується, його
  наявність та опис у cache-only метаданих репозиторію.
- `package_search` — обмежений пошук за назвою або описом у наявному кеші
  rpm-ostree.
- `plasma_info` — контекст Plasma, KDE Frameworks, Qt і Wayland/X11.
- `qml_module_available` — пошук валідованого QML URI через системні корені
  імпорту Qt і метадані `qmldir`.

Профіль `codex` додатково публікує:

- `memory_append` — додавання однієї обмеженої нотатки до
  `.arvis_mcp_memory/`.

Офіційні annotations інструментів MCP задаються явно:

| Клас інструментів | Read-only | Destructive | Idempotent | Open world |
| --- | --- | --- | --- | --- |
| Тринадцять інструментів читання | `true` | `false` | `true` | `false` |
| `memory_append` | `false` | `false` | `false` | `false` |

Усі поточні інструменти читання мають `openWorldHint=false`. Зокрема,
`package_search` і частина `package_info`, що перевіряє репозиторій, викликають
`rpm-ostree search --cache-only`: вони не оновлюють метадані та не звертаються
до репозиторію через мережу. Відсутність кешованих метаданих повертається як
контрольована помилка без непомітного переходу до online-команди. Майбутній
мережевий adapter потребуватиме явної зміни контракту й
`openWorldHint=true`.

## Контракти read-only перевірки системи

Системні інструменти не залежать від `project_root`; список дозволених коренів
файлової системи й надалі стосується лише інструментів контексту проєкту. Ці
інструменти повертають вибрані технічні факти, а не загальний API хоста.

| Інструмент | Вхідні дані та ліміти | Основний результат | Контрольовані випадки недоступності |
| --- | --- | --- | --- |
| `system_info` | без аргументів | вибрані факти про OS/runtime і можливості backend | опційні значення Plasma/Qt дорівнюють `null` |
| `binary_exists` | назва executable, до 128 символів | `exists`, шлях з урахуванням privacy | некоректна назва |
| `package_installed` | точна назва RPM-пакунка, до 128 символів | installed/version/architecture/summary | RPM недоступний, timeout, помилка parser |
| `package_info` | точна назва RPM-пакунка, до 128 символів | встановлені дані та наявність у кешованому репозиторії | пакунок не знайдено, кеш недоступний; результат лише з установленими даними може бути частковим |
| `package_search` | ASCII-терміни назви/опису, до 160 символів; `limit` обмежується до 1–50 | дедупліковані назви та описи пакунків | backend/кеш недоступний, timeout, помилка parser |
| `plasma_info` | без аргументів | версії Plasma/KDE Frameworks/Qt і протокол сесії | недоступні чи некоректні опційні значення стають `null` |
| `qml_module_available` | dotted QML URI, до 200 символів | наявність, контекст Qt, оголошені версії типів, опційний RPM provider | некоректний URI, немає коренів імпорту Qt, некоректний `qmldir` |

Контрольовані помилки мають стабільні коди, зокрема `invalid_input`,
`backend_unavailable`, `executable_unavailable`, `package_not_found`, `timeout`,
`repository_metadata_unavailable` і `parser_failure`. Межа протоколу не повертає
Python traceback.

### Backend RPM і rpm-ostree

Перший підтримуваний backend орієнтований на Linux-системи сімейства RPM:

- `rpm --query` читає базу встановлених пакунків хоста. На rpm-ostree хості ця
  база описує завантажений образ і layered packages.
- Ostree boot marker разом із довіреним executable `rpm-ostree` вмикає пошук
  наявності пакунків у репозиторії. Використовується лише фіксований argv
  `search --cache-only`.
- Результат cache-only пошуку rpm-ostree надійно містить назву й опис пакунка,
  але не версію в репозиторії, architecture або ідентифікатор репозиторію. Ці
  поля мають явне значення `null` або вказані як unsupported, а не вгадуються.
- Традиційні mutable Fedora системи все одно отримують пошук установлених RPM.
  Пошук у репозиторіях там поки не підтримується: виклик DNF із гарантією
  відсутності оновлення metadata/cache/log виходить за межі цього read-only
  контракту.
- Debian, Arch, Flatpak, Homebrew і бази пакунків контейнерів поки не мають
  adapter для цих інструментів.

Жоден інструмент пакунків не виконує install, remove, upgrade, update, refresh,
layering, repo-enable/disable, `sudo` або transaction command. Це відповідає
transactional моделі rpm-ostree з офіційного
[посібника адміністратора rpm-ostree](https://coreos.github.io/rpm-ostree/administrator-handbook/).

### Plasma, Qt і QML

Версії Plasma та KDE Frameworks беруться з метаданих установлених RPM без
запуску GUI-процесів на кшталт `plasmashell`. Версія Qt перевіряється фіксованою
командою `qtpaths6 --qt-version`. Дані сесії надходять лише з вибраних ключів
`XDG_CURRENT_DESKTOP` і `XDG_SESSION_TYPE` після суворої фільтрації; повний
environment не повертається.

QML-пошук валідує ідентифікатор на кшталт `org.kde.kirigami`, перетворює його
сегменти на відносне розташування модуля й перевіряє лише системні корені
імпорту QML, відомі Qt. Наявність потребує відповідного оголошення модуля в
обмеженому файлі `qmldir`. Це відповідає документації Qt про
[визначення QML-модуля](https://doc.qt.io/qt-6/qtqml-modules-qmldir.html).
RPM provider повертається лише тоді, коли `rpm --query --file` підтверджує
власника; він не вгадується з назви каталогу. Користувацькі QML-шляхи з
environment не скануються.

## Список дозволених коренів

`ARVIS_MCP_ALLOWED_ROOTS` містить один або кілька дозволених коренів, розділених
системним path separator (`:` у Linux/macOS, `;` у Windows).
`ARVIS_MCP_PROJECT_ROOT` вибирає стандартний корінь і має залишатися всередині
цього списку. Аргумент `project_root` інструменту може вибрати лише дозволений
корінь або його нащадка.

Шляхи канонізуються до авторизації. Виходи через абсолютний шлях, parent (`..`), корінь
файлової системи або symlink відхиляються. `chatgpt` ніколи не переходить до
домашнього каталогу чи всієї файлової системи, якщо список дозволених коренів
відсутній.

Публічний приклад із placeholders:

```dotenv
ARVIS_MCP_PROFILE=chatgpt
ARVIS_MCP_PROJECT_ROOT=/path/to/arvis
ARVIS_MCP_ALLOWED_ROOTS=/path/to/arvis:/path/to/another-project
```

Реальні значення зберігай лише в ignored `.env.local`, `.env` або середовищі
процесу. Не копіюй локальні шляхи, імена користувачів, ідентифікатори клієнта,
credentials Tunnel чи токени до tracked коду або прикладів.

## Безпека та ліміти

- Інструменти MCP не редагують вихідний код і не виконують довільні shell
  commands.
- Перевірка Git використовує фіксований список команд, `shell=False` і timeout.
- Системна перевірка використовує фіксовану форму argv, `shell=False`,
  вимкнений stdin, локальний timeout 5 секунд, timeout репозиторію 12 секунд,
  ліміти stdout 64 KiB і stderr 8 KiB. Ввід моделі потрапляє лише до валідованих
  позицій назви, запиту чи URI та не може додати flags.
- Перевірка репозиторію працює лише з кешем і не може оновити чи змінити
  metadata/configuration. QML-перевірка читає обмежені файли `qmldir` і не
  приймає шлях файлової системи від клієнта.
- Очікувані помилки файлової системи та Git повертаються як контрольовані
  результати інструментів. Неочікувані помилки записуються до stderr без
  traceback клієнта чи secret contents.
- Шляхи проєкту повертаються відносними. Результати й керовані користувачем
  числові ліміти обмежені.
- Файли більші за 2 MiB не скануються. Один пошук обмежений 32 MiB і 20 000
  записів обходу. Regex-запити мають ліміти довжини та складності.
- Приватні/generated шляхи й типові secret files виключені, зокрема `.env*`
  (крім examples/templates), `.ssh`, `.aws`, `.gnupg`, `.runtime`, caches,
  virtualenvs, `node_modules`, `.git`, models, logs, private keys, credential
  stores, `.codex` і `.arvis_mcp_memory`.
- Очевидні credential values у доступному тексті редагуються: API keys,
  access/refresh tokens, authorization і cookie headers, passwords, client
  secrets, типові формати окремих tokens і private-key blocks. Звичайні
  comments, type annotations та identifier names на кшталт `token` лишаються
  корисними.
- MCP memory містить лише підказки. Перед редагуванням файли треба перевіряти
  безпосередньо.

Системні відповіді навмисно не містять hostname, username, home directory,
IP/MAC, даних мережевих інтерфейсів, machine ID, serial numbers, process lists
або повного середовища. `binary_exists` повертає шлях лише для executable зі
стандартного системного каталогу; несистемні шляхи приховуються. Розташування
QML так само повертається лише для системних шляхів.

## Приклад використання з Codex

Точні шляхи клієнтської конфігурації та підтримувані поля можуть відрізнятися
між версіями Codex. Реальну локальну конфігурацію зберігай поза публічним Git.

```toml
[mcp_servers.arvis_context]
command = ".venv/bin/python"
args = ["arvis_mcp_server.py"]
cwd = "/absolute/path/to/arvis_app"
startup_timeout_sec = 20
tool_timeout_sec = 60
enabled = true

[mcp_servers.arvis_context.env]
ARVIS_MCP_PROFILE = "codex"
ARVIS_MCP_PROJECT_ROOT = "/absolute/path/to/arvis_app"
ARVIS_MCP_ALLOWED_ROOTS = "/absolute/path/to/arvis_app"
```

Приклад для CLI:

```bash
codex mcp add arvis_context \
  --env ARVIS_MCP_PROFILE=codex \
  --env ARVIS_MCP_PROJECT_ROOT=/absolute/path/to/arvis_app \
  --env ARVIS_MCP_ALLOWED_ROOTS=/absolute/path/to/arvis_app \
  -- .venv/bin/python arvis_mcp_server.py
```

Якщо клієнт не може запустити MCP, запусти stdio server вручну з кореня
репозиторію, щоб побачити локальні startup diagnostics:

```bash
.venv/bin/python arvis_mcp_server.py
```

## Локальна/приватна конфігурація, яку не можна комітити

Безпечні локальні місця: `.env`, `.env.local`, ignored `.codex/config.toml`,
користувацька конфігурація клієнта поза репозиторієм і
`.arvis_mcp_memory/`. Не коміть реальні корені, usernames, tokens, secrets,
ідентифікатори підключення ChatGPT/MCP, локальну пам'ять або специфічні для
машини параметри.

Credentials OpenAI Secure MCP Tunnel також не належать цьому репозиторію.
Встановлення та підключення Tunnel не входять до поточного етапу hardening.
