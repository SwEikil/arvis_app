# Конфігурація

[← Індекс документації](README.md) · [Головна сторінка](../README.md)

Arvis читає локальні параметри з environment і `.env` через `python-dotenv`.
Tracked шаблон — [`.env.example`](../.env.example); реальний `.env` і
machine-specific values не належать до git.

```bash
cp .env.example .env
```

Команди з env розбираються через `shlex.split()` і передаються subprocess як
argv із `shell=False`.

## Ядро та локальні папки

| Ключ | Стандартне значення / приклад | Призначення |
| --- | --- | --- |
| `USER_NAME` | `your_name` у template | Локальне ім’я користувача. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | URL локального Ollama API. |
| `ARVIS_MODEL` | `arvis` | Назва Ollama chat-моделі. |
| `MUSIC_FOLDER` | `/path/to/music` | Опційна локальна папка музики. |
| `DOWNLOADS_FOLDER` | `/path/to/downloads` | Опційна локальна папка downloads. |

Без `.env` core використовує safe defaults для `OLLAMA_HOST` та `ARVIS_MODEL`.
Machine paths лишаються порожніми.

Standalone MCP Context Servant також читає ignored `.env.local` і `.env`, але
використовує тільки загальні параметри `ARVIS_MCP_*` та
`ARVIS_SYSTEM_METRICS_STORAGE_PATH`. Опційна safe-command integration також
читає локальні `ARVIS_SAFE_COMMAND_*`; її config path і recipe policy не
належать до tracked конфігурації. Вузький Safe Git adapter читає лише локальні
`ARVIS_SAFE_GIT_*`: master opt-in, pinned remote name та exact URL, фіксовані
public name/email і окремі opt-ins для push та history rewrite. Реальні
значення цієї policy, корені проєктів, storage target і локально вибраний
профіль не належать до публічної конфігурації. Повний опис:
[`mcp_context_servant.md`](mcp_context_servant.md).

`ARVIS_SYSTEM_METRICS_STORAGE_PATH` опційно вибирає один абсолютний filesystem
path для aggregate storage metrics; стандартне значення — `/`. Це корисно,
коли `/` усередині runtime namespace MCP не є filesystem хоста. Значення не є
аргументом MCP, не повертається клієнту й має зберігатися лише в ignored local
config. Інші read-only backend визначаються за OS хоста, довіреними executable
та ostree boot marker. Пошук у репозиторіях працює лише з уже наявним кешем
метаданих rpm-ostree і не вмикається локальним override.

## Запуск застосунків і сайтів

| Ключ | Безпечний приклад шаблону |
| --- | --- |
| `STEAM_COMMAND` | `steam` |
| `SPOTIFY_COMMAND` | `flatpak run com.spotify.Client` |
| `BRAVE_COMMAND` | `brave-browser` |
| `DISCORD_COMMAND` | `flatpak run com.discordapp.Discord` |
| `TELEGRAM_COMMAND` | `flatpak run org.telegram.desktop` |
| `YOUTUBE_COMMAND` | `xdg-open https://www.youtube.com/` |
| `GOOGLE_COMMAND` | `xdg-open https://www.google.com/` |
| `GITHUB_COMMAND` | `xdg-open https://github.com/` |
| `CHATGPT_COMMAND` | `xdg-open https://chatgpt.com/` |

Ці URLs зафіксовані whitelisted targets. Natural language не може передати
довільний URL. Локально можна замінити launcher, наприклад:

```dotenv
YOUTUBE_COMMAND=brave-browser https://www.youtube.com/
```

або:

```dotenv
YOUTUBE_COMMAND=flatpak run com.brave.Browser https://www.youtube.com/
```

## Браузер

| Ключ | Стандартне значення | Призначення |
| --- | --- | --- |
| `ARVIS_BROWSER_DEBUG_SAVE` | `false` | Debug screenshots/events вузького Browser Vision Agent. |
| `ARVIS_BROWSER_OBSERVER_HEADFUL` | `false` | Видимий Chromium лише для локального observer debugging. |

Browser Observer profiles не створюються з natural language. Tracked safe
examples лежать у [`examples/watch_profiles/`](../examples/watch_profiles/), а
локальні profiles/templates і runtime output — під `.runtime/browser_observer/`.
Точний формат: [`browser_observer.md`](browser_observer.md).

## Голос

| Ключ | Стандартне значення шаблону | Призначення |
| --- | --- | --- |
| `ARVIS_VOICE_ENABLED` | `false` | Вмикає explicit voice commands. |
| `ARVIS_STT_BACKEND` | `faster_whisper` | Обраний STT backend. |
| `ARVIS_STT_MODEL` | `small` | STT model. |
| `ARVIS_STT_DEVICE` | `auto` | CPU/GPU device selection. |
| `ARVIS_STT_COMPUTE_TYPE` | `auto` | Compute type для backend. |
| `ARVIS_MIC_DEVICE` | empty | Default input або явний microphone device. |
| `ARVIS_VOICE_RECORD_SECONDS` | `6` | Тривалість one-shot recording. |
| `ARVIS_VOICE_LANGUAGE` | `uk` | Основна мова STT. |
| `ARVIS_VOICE_ALLOWED_LANGUAGES` | `uk,ru,en,no` | Allowlist мов. |
| `ARVIS_VOICE_MIN_RMS` | `0.008` | Мінімальний RMS для speech sample. |
| `ARVIS_VOICE_MIN_PEAK` | `0.03` | Мінімальний peak для speech sample. |
| `ARVIS_VOICE_DEBUG_SAVE_LAST` | `false` | Копіює останній sample у ignored runtime. |
| `ARVIS_VOICE_DUCKING_ENABLED` | `true` | Best-effort ducking під час запису. |
| `ARVIS_VOICE_DUCK_PERCENT` | `15` | Відсоток volume під час ducking. |
| `ARVIS_VOICE_DUCK_RESTORE` | `true` | Відновлює попередню volume після запису. |

Порожній `ARVIS_MIC_DEVICE` використовує default microphone. Device names, схожі
на monitor/output/loopback/desktop audio, відхиляються. Докладніше:
[`voice.md`](voice.md).

## Сервер Minecraft

```dotenv
MINECRAFT_SERVER_ENABLED=false
MINECRAFT_SERVER_KEY=default
MINECRAFT_SERVER_NAME=My Minecraft Server
MINECRAFT_SERVER_CWD=/absolute/path/to/server
MINECRAFT_SERVER_COMMAND=./start.sh
```

Поки integration disabled або config incomplete, router повертає
`not_configured` і нічого не запускає. Реальні server name, directory і command
мають залишатися тільки в локальному `.env`. Поведінка:
[`minecraft_server.md`](minecraft_server.md).

## Локальні та згенеровані шляхи

Gitignore захищає:

- `.env`, `.env.local`;
- `.runtime/`, logs і caches;
- Python virtualenv/cache files;
- `.arvis_mcp_memory/` і `.codex/`;
- `models/`, `ollama-models/` і типові model file extensions;
- `.local_extensions/` і `.runtime/local_extensions/`.

Не клади tracked examples всередину ignored directories. Public templates мають
містити тільки placeholders і safe example values.
