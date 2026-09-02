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

- `codex` — профіль сумісності. За вимкненого lifecycle він публікує 25
  інструментів; за ввімкненого — 30.
  Якщо список дозволених коренів не налаштований, доступ обмежується
  `ARVIS_MCP_PROJECT_ROOT`, а за відсутності цієї змінної — робочим каталогом
  сервера.
- `chatgpt` працює fail-closed. Він потребує явного значення
  `ARVIS_MCP_ALLOWED_ROOTS`, не публікує `memory_append` і має 24 інструменти
  без lifecycle або 29 із lifecycle. Різниця рівно в один інструмент між
  профілями є навмисною.

За замовчуванням safe-command control вимкнений і наведені counts не
змінюються. Після явного локального opt-in обидва профілі отримують рівно один
додатковий control tool `safe_command_run`.

Safe Git control також вимкнений за замовчуванням. Валідний master opt-in додає
три інструменти: preflight, exact-path stage і staged commit. Окремі локальні
opt-ins додають по одному інструменту для push current branch та destructive
rewrite unpushed identity. Тому counts збільшуються на `3`–`5` лише відповідно
до локально валідованої policy, валідної MCP access config і хоча б одного
наявного writable root. Некоректна чи неповна конфігурація не реєструє жодного
Safe Git tool.

Обидва профілі публікують такі read-only інструменти:

- `project_map` — обмежена карта безпечних текстових файлів, розмірів, типів і
  розширень.
- `grep_project` — обмежений пошук у безпечних текстових файлах.
- `read_file_excerpt` — обмежений уривок рядків одного безпечного текстового
  файлу.
- `git_status_summary` — обмежений результат фіксованих безпечних команд Git.
- `git_inspect` — branch, повний HEAD, tracked files, bounded reachable commits
  і дедуплікований bounded audit paths, яких торкалась reachable history.
- `task_brief` — компактні пошукові підказки для задачі.
- `memory_read` — обмежене читання з `.arvis_mcp_memory/`.
- `system_info` — OS, architecture, kernel, desktop/session, Plasma/Qt, atomic
  status і підтримувані backend перевірки пакунків без ідентифікації машини.
- `system_metrics` — один обмежений best-effort snapshot завантаження CPU,
  пам'яті, NVIDIA GPU, кореневого сховища й uptime без ідентифікації машини.
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
| Інструменти читання | `true` | `false` | `true` | `false` |
| `memory_append` | `false` | `false` | `false` | `false` |
| Build/test і lifecycle control | `false` | `false` | `false` | `false` |
| `safe_command_run` (якщо локально ввімкнено) | `false` | `true` | `false` | `true` |
| `safe_git_preflight` | `true` | `false` | `true` | `true` |
| `safe_git_stage_paths` | `false` | `true` | `true` | `false` |
| `safe_git_commit_staged` | `false` | `true` | `false` | `false` |
| `safe_git_push_current` | `false` | `true` | `true` | `true` |
| `safe_git_rewrite_unpushed_identity` | `false` | `true` | `false` | `true` |

Усі базові інструменти читання мають `openWorldHint=false`. Зокрема,
`package_search` і частина `package_info`, що перевіряє репозиторій, викликають
`rpm-ostree search --cache-only`: вони не оновлюють метадані та не звертаються
до репозиторію через мережу. Відсутність кешованих метаданих повертається як
контрольована помилка без непомітного переходу до online-команди. Майбутній
мережевий adapter потребуватиме явної зміни контракту й
`openWorldHint=true`. Safe-command recipes також мають `openWorldHint=true`,
бо локально дозволений executable може звертатися до host або мережі. Opt-in `safe_git_preflight` уже має
`openWorldHint=true`, бо перевіряє live head pinned remote.

## Локальна safe-command policy

`safe_command_run` є opt-in adapter над generic safe-command core. Публічний
репозиторій не містить machine-specific recipes, executable paths або готових
команд. Локальний адміністратор зберігає policy JSON поза tracked файлами та
вмикає adapter тільки в ignored `.env.local`, `.env` або environment процесу:

```dotenv
ARVIS_SAFE_COMMAND_CONTROL_ENABLED=true
ARVIS_SAFE_COMMAND_CONFIG=/absolute/path/to/safe-commands.json
ARVIS_SAFE_COMMAND_HOST_CONTROL_ENABLED=false
```

Самої наявності `ARVIS_SAFE_COMMAND_CONFIG` недостатньо: без exact `true` у
`ARVIS_SAFE_COMMAND_CONTROL_ENABLED` tool не реєструється. Config path має бути
абсолютним, ніколи не надходить з MCP input і не повертається в schema чи
result. Policy завантажується один раз під час старту сервера; після її зміни
сервер треба перезапустити. Відсутній або некоректний config не валить MCP
server і не реєструє tool: сам master opt-in без ефективно завантаженої policy
недостатній.

Recipe config — довірена локальна межа безпеки. Той, хто може редагувати цей
файл, фактично авторизує semantics executable, argv, cwd, access і лімітів.
MCP-клієнт, ChatGPT і Codex через цей tool не можуть читати, редагувати,
вибирати шлях або reload цієї policy. Їм доступні тільки `recipe_name`, bounded
string `params` і опційний `project_root`; shell, command text, executable,
argv, env та execution-policy fields відсутні.

Access береться лише з довіреної recipe. Будь-який переданий або потрібний
`project_root` проходить загальний `ARVIS_MCP_ALLOWED_ROOTS`; `workspace_write`
додатково потребує `ARVIS_MCP_WRITABLE_ROOTS`. MCP input не може підвищити
read-only recipe до write. Recipe з `host_control` лишається забороненою, доки
локальний адміністратор окремо не встановить
`ARVIS_SAFE_COMMAND_HOST_CONTROL_ENABLED=true`; клієнт не може перевизначити
цей прапорець. Результат містить лише bounded/redacted execution fields і не
розкриває policy чи її шлях.

## Локальна Safe Git policy

Safe Git MCP tools є вузьким adapter над `safe_git_control.py`, а не shell чи
generic Git endpoint. Усі значення довіреної policy задає тільки локальний
адміністратор в ignored `.env.local`, `.env` або environment процесу. Публічний
шаблон містить лише вигадані placeholders:

```dotenv
ARVIS_SAFE_GIT_CONTROL_ENABLED=true
ARVIS_SAFE_GIT_REMOTE_NAME=origin
ARVIS_SAFE_GIT_EXPECTED_REMOTE_URL=https://git.example.invalid/owner/repository.git
ARVIS_SAFE_GIT_PUBLIC_NAME=Public Contributor
ARVIS_SAFE_GIT_PUBLIC_EMAIL=contributor@example.invalid
ARVIS_SAFE_GIT_PUSH_ENABLED=false
ARVIS_SAFE_GIT_HISTORY_REWRITE_ENABLED=false
```

Без exact `true` у `ARVIS_SAFE_GIT_CONTROL_ENABLED` жоден Safe Git tool не
реєструється. Після master opt-in policy має повністю пройти локальну валідацію:
remote name, exact expected push URL і fixed public identity обов'язкові;
некоректна чи неповна policy не реєструє Safe Git tools.
`ARVIS_SAFE_GIT_PUSH_ENABLED` і
`ARVIS_SAFE_GIT_HISTORY_REWRITE_ENABLED` приймають тільки `true`/`false`, за
відсутності дорівнюють `false`, а за некоректного значення роблять policy
недоступною. Push і rewrite tools не реєструються без власного `true`.

MCP caller може передати тільки такі мінімальні параметри:

- `safe_git_preflight`: опційний `project_root`;
- `safe_git_stage_paths`: точний список `paths` і опційний `project_root`;
- `safe_git_commit_staged`: bounded `subject` і опційний `project_root`;
- `safe_git_push_current`: опційний `project_root`;
- `safe_git_rewrite_unpushed_identity`: опційний `project_root`.

Executable, remote name/URL, public identity, branch, refspec, enable flags і
довільні Git arguments відсутні в schemas. Preflight проходить
`ARVIS_MCP_ALLOWED_ROOTS`; stage, commit, push і rewrite додатково потребують
writable authorization із загальної MCP access policy. Для `chatgpt` це явний
`ARVIS_MCP_WRITABLE_ROOTS`; compatibility profile `codex` зберігає свій
наявний default writable-root contract. Engine повторно перевіряє repository
top-level, resolved git-dir, resolved git-common-dir, поточну branch, pinned
remote URL і всі operation-specific ліміти. Перед write він також перевіряє,
що git-dir, common-dir та symbolic links усередині mutable metadata не виходять
за authorized writable roots. Write operations додатково вимагають звичайний
direct `.git` directory: linked worktrees, symlinked `.git` і separate/external
git-dir відхиляються навіть тоді, коли broad writable root охоплює обидва шляхи.
Перевірка metadata bounded до 200 000 entries; більший repository fail-closed.

Stage працює лише з exact currently changed paths. Commit використовує лише
поточний staged diff, bounded one-line subject, fixed public identity, вимкнені
hooks і signing. Push може лише fast-forward поточну same-named branch до
pinned remote. Rewrite є явно destructive та обмежений лінійними unpushed
commits; він не виконує push. Force, tags, arbitrary refspec/branch, amend,
reset, checkout, rebase, hooks і signing не підтримуються.

Remote policy підтримує plain HTTPS URL без embedded credentials; SSH, scp-like,
`ext::`, довільні remote helpers і URL rewrite rules відхиляються. Remote
commands отримують exact pinned URL, а не repo-selected helper. Repository і
worktree config не можуть задавати credential helpers, askpass, SSH commands,
remote upload/receive commands, external diff/merge drivers, HTTP proxy/TLS/
resolve transport overrides (включно з URL-scoped та included config) або інші process
trampolines. Hooks, pagers, editors, fsmonitor, auto-maintenance і signing
нейтралізуються fixed settings. Content clean/smudge/process filters не
підтримуються: якщо такий driver є в effective Git config, Safe Git fail-closed
до status/stage. Partial-clone/promisor repositories та alternate-refs commands
також не підтримуються; submodule recursion і recursive push вимкнені. Лише для
exact pinned standard HTTPS `github.com` remote дозволена вже налаштована
host-level credential helper policy. Для інших hosts helper list скидається в
command scope; interactive prompt/askpass і credentials у URL не підтримуються.
Absolute local remotes можна перевіряти як pinned local state, але
`safe_git_push_current` для них не реєструється: локальний receive-pack може
запускати hooks, а publish підтримує лише pinned HTTPS.

Sensitive policy fields, exact remote URL, public identity та абсолютні
локальні paths не повертаються через MCP result, error або repr. Зміна local
policy потребує перезапуску MCP server. Doctor перевіряє opt-ins і синтаксичну
повноту policy, але не виконує Git operations і не показує приватні значення.

## Контракти read-only перевірки системи

Системні інструменти не залежать від `project_root`; список дозволених коренів
файлової системи й надалі стосується лише інструментів контексту проєкту. Ці
інструменти повертають вибрані технічні факти, а не загальний API хоста.

| Інструмент | Вхідні дані та ліміти | Основний результат | Контрольовані випадки недоступності |
| --- | --- | --- | --- |
| `system_info` | без аргументів | вибрані факти про OS/runtime і можливості backend | опційні значення Plasma/Qt дорівнюють `null` |
| `system_metrics` | без аргументів; один snapshot із коротким CPU sampling window | CPU usage/load/temperature, RAM/swap, список NVIDIA GPU, використання логічного root storage target, uptime, стани backend і warning-коди | недоступні backend або окремі hardware metrics стають `null`/порожнім списком і не скасовують доступні секції |
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

### Snapshot системних метрик

`system_info` лишається невеликим інструментом статичних фактів про OS/runtime.
Поточне навантаження відокремлене в `system_metrics`; виклик не запускає daemon
чи monitoring loop і повертає лише один snapshot:

```text
cpu
  usage_percent, logical_cpus, load.{1m,5m,15m}, temperature_c
memory
  total_bytes, used_bytes, available_bytes, free_bytes, used_percent
swap
  total_bytes, used_bytes, free_bytes, used_percent
gpu[]
  vendor, model, utilization_percent, temperature_c, power_w, power_limit_w,
  vram_total_bytes, vram_used_bytes, vram_free_bytes, vram_used_percent
storage.root
  total_bytes, used_bytes, free_bytes, used_percent
uptime_seconds
backends, warnings
```

RAM, swap, VRAM і сховище повертаються в bytes. Нульове значення метрики не
змішується з недоступністю: unsupported або нерозпізнане поле має `null`.
`backends` окремо показує доступність фіксованих джерел, а `warnings` містить
лише короткі стабільні коди без stderr чи traceback. Відмова одного джерела не
скасовує інші секції результату.

Джерела даних навмисно вузькі й read-only:

- CPU usage — дві обмежені вибірки aggregate-рядка `/proc/stat` із коротким
  контрольованим інтервалом; load average — локальний OS interface.
- RAM і swap — тільки дозволені числові поля `/proc/meminfo`; uptime — перше
  числове поле `/proc/uptime`.
- CPU temperature — bounded scan CPU-specific driver names у
  `/sys/class/hwmon`; неідентифіковані hardware sensors не вгадуються.
- NVIDIA — довірений системний `nvidia-smi` з фіксованим
  `--query-gpu=name,utilization.gpu,temperature.gpu,power.draw,power.limit,memory.total,memory.used,memory.free`
  і CSV `noheader,nounits`. Підтримується кілька GPU. Якщо executable відсутній,
  команда завершується помилкою або окреме поле має `N/A`/`Not Supported`,
  решта snapshot лишається доступною.
- Сховище — тільки aggregate використання одного локально налаштованого target.
  За замовчуванням використовується `/`. Якщо Arvis/MCP працює в окремому
  filesystem namespace або `/` не відповідає host filesystem, користувач може
  задати абсолютний `ARVIS_SYSTEM_METRICS_STORAGE_PATH` в ignored `.env.local`,
  `.env` чи environment процесу. Наприклад:

  ```dotenv
  ARVIS_SYSTEM_METRICS_STORAGE_PATH=/path/to/filesystem
  ```

  Arvis не вгадує target через перелік mountpoints. Відсутній, relative або
  недоступний configured path дає `root_storage_unavailable`, не скасовуючи
  решту snapshot.

`system_metrics` не приймає command, argv, шлях чи URL від клієнта. Він не
повертає process list, command lines, environment, hostname, username,
мережеві дані, UUID/serial дисків, configured storage path, перелік mountpoints
або вміст користувацьких файлів.

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

`ARVIS_MCP_WRITABLE_ROOTS` — окремий explicit allowlist для операцій, які
можуть змінити workspace: `dotnet build/test` і `workspace_write` Codex agent.
Для `chatgpt` він порожній за замовчуванням. Це дозволяє додати parent workspace
для bounded/redacted читання private handoff, не надаючи write access усьому
parent root. У compatibility-профілі `codex` відсутнє значення успадковує
read roots; production-конфігурації рекомендовано задавати його явно.
Саме відсутня змінна зберігає цей `codex` default. Якщо змінна присутня, але
порожня, містить порожній segment або некоректний path, access config fail-closed
і не розширює write access до read roots.

Шляхи канонізуються до авторизації. Виходи через абсолютний шлях, parent (`..`), корінь
файлової системи або symlink відхиляються. `chatgpt` ніколи не переходить до
домашнього каталогу чи всієї файлової системи, якщо список дозволених коренів
відсутній.

Публічний приклад із placeholders:

```dotenv
ARVIS_MCP_PROFILE=chatgpt
ARVIS_MCP_PROJECT_ROOT=/path/to/arvis
ARVIS_MCP_ALLOWED_ROOTS=/path/to/arvis:/path/to/another-project
ARVIS_MCP_WRITABLE_ROOTS=/path/to/arvis
```

Реальні значення зберігай лише в ignored `.env.local`, `.env` або середовищі
процесу. Не копіюй локальні шляхи, імена користувачів, ідентифікатори клієнта,
credentials Tunnel чи токени до tracked коду або прикладів.

## Безпека та ліміти

- Інструменти MCP не редагують вихідний код і не виконують довільні shell
  commands. Project build/test дозволяють лише фіксовані `dotnet build` і
  `dotnet test` для project files усередині дозволеного root.
- Перевірка Git використовує фіксований список команд, `shell=False`, timeout
  і bounded lists. `git_inspect` відхиляє випадок, коли Git намагається піднятися
  до repository top-level поза вибраним project root.
- Системна перевірка використовує фіксовану форму argv, `shell=False`,
  вимкнений stdin, локальний timeout 5 секунд, timeout репозиторію 12 секунд,
  ліміти stdout 64 KiB і stderr 8 KiB. Ввід моделі потрапляє лише до валідованих
  позицій назви, запиту чи URI та не може додати flags.
- NVIDIA snapshot використовує той самий fixed-command runner зі строгішими
  лімітами stdout 32 KiB і stderr 4 KiB; stderr ніколи не повертається клієнту.
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
ARVIS_MCP_WRITABLE_ROOTS = "/absolute/path/to/arvis_app"
```

Приклад для CLI:

```bash
codex mcp add arvis_context \
  --env ARVIS_MCP_PROFILE=codex \
  --env ARVIS_MCP_PROJECT_ROOT=/absolute/path/to/arvis_app \
  --env ARVIS_MCP_ALLOWED_ROOTS=/absolute/path/to/arvis_app \
  --env ARVIS_MCP_WRITABLE_ROOTS=/absolute/path/to/arvis_app \
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

## Project verification tools

Project-scoped API доповнює базові `project_map`, `read_file_excerpt`,
`grep_project` і `git_status_summary` такими операціями:

- `project_state` і `git_diff` повертають структурований changed-file state та
  bounded staged/unstaged diff; `git_inspect` додає independent acceptance data:
  branch, full HEAD, tracked files, bounded reachable history і history path audit;
- `build_project` і `test_project` запускають лише `dotnet` над валідованим
  `.sln`/`.csproj`/`.fsproj`/`.vbproj` у дозволеному root;
- `validate_manifest` і `validate_mod_artifact` перевіряють SMAPI manifest та
  ZIP, включно з traversal, EntryDll і AI/private artifacts;
- `stardew_environment`, `smapi_log_excerpt` і `smapi_mod_status` знаходять
  локальну інсталяцію через config/Steam metadata та повертають тільки bounded,
  redacted diagnostics.

Machine-specific `dotnet`, Stardew і SMAPI log paths задаються лише через
ignored local environment. Build/test успадковують мінімальний allowlist env,
використовують `shell=False`, мають timeout і обмежений output.

## Codex agent lifecycle

Lifecycle tools реєструються лише коли локально ввімкнено
`ARVIS_CODEX_AGENT_CONTROL_ENABLED=true`. API складається з п'яти операцій:
`codex_agent_create`, `codex_agent_status`, `codex_agent_result`,
`codex_agent_close` і `codex_agent_show`. Деталі CLI ізольовані у worker;
клієнт не передає команду, flags, terminal або executable.

Agent state root обов'язково має бути фізично поза project workspace. Worker
зберігає request, bounded events, stderr, status і final result локально.
`handoff_from` приймає тільки terminal predecessor зі збереженим result.
Для одноразового імпорту від агента, який ще не керувався цим lifecycle,
`handoff_text` приймає окремий bounded handoff; status підтверджує його через
`handoff_received`.
Predecessor не закривається автоматично: caller спочатку створює successor,
перевіряє `task_received` та `workspace_accessible`, і лише потім викликає
`close` для predecessor. Помилка successor не видаляє predecessor state.

Visible mode окремо opt-in через `ARVIS_CODEX_VISIBLE_TERMINAL_ENABLED=true` і
параметр `visible=true` у `codex_agent_create`; наявний agent можна показати
через `codex_agent_show(agent_id)`. Arvis запускає тільки фіксований Konsole
argv із власним helper, тому API не є process launcher. Якщо Konsole вже
працює, Arvis використовує штатні `--force-reuse --new-tab` із тим самим fixed
helper argv; небезпечний `Session.runCommand` DBus API не вмикається й не
викликається. Model не передає terminal flags або command. Якщо Konsole не
працює, запускається нове Konsole window з fixed helper argv. Після reuse
request Arvis read-only перевіряє, чи launcher PID зареєстрував окремий Konsole
DBus service, і повертає фактичний `terminal_target`. Деякі локальні Konsole
налаштування ігнорують reuse; у такому разі чесно повідомляється `new_window`,
а небезпечна DBus policy не послаблюється.

Codex CLI не надає concurrent TUI attach до активного `codex exec`. Тому під
час первинного run terminal є read-only viewer того самого JSONL stream. Після
завершення worker helper запускає `codex resume --include-non-interactive` з
точним збереженим session ID: це послідовне інтерактивне продовження тієї самої
session, а не другий конкурентний agent. Під час цієї фази користувач може
вводити текст. Status явно показує `visibility_state`, `same_session`,
`user_interaction`, `session_id` і `result_scope`; після виходу з TUI final
answer з тієї самої session оновлює lifecycle result/handoff.
