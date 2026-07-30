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

## Core і локальні папки

| Key | Default / example | Призначення |
| --- | --- | --- |
| `USER_NAME` | `your_name` у template | Локальне ім’я користувача. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | URL локального Ollama API. |
| `ARVIS_MODEL` | `arvis` | Назва Ollama chat-моделі. |
| `MUSIC_FOLDER` | `/path/to/music` | Опційна локальна папка музики. |
| `DOWNLOADS_FOLDER` | `/path/to/downloads` | Опційна локальна папка downloads. |

Без `.env` core використовує safe defaults для `OLLAMA_HOST` та `ARVIS_MODEL`.
Machine paths лишаються порожніми.

## App і website launch

| Key | Safe template example |
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

## Browser

| Key | Default | Призначення |
| --- | --- | --- |
| `ARVIS_BROWSER_DEBUG_SAVE` | `false` | Debug screenshots/events вузького Browser Vision Agent. |
| `ARVIS_BROWSER_OBSERVER_HEADFUL` | `false` | Видимий Chromium лише для локального observer debugging. |

Browser Observer profiles не створюються з natural language. Tracked safe
examples лежать у [`examples/watch_profiles/`](../examples/watch_profiles/), а
локальні profiles/templates і runtime output — під `.runtime/browser_observer/`.
Точний формат: [`browser_observer.md`](browser_observer.md).

## Voice

| Key | Template default | Призначення |
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

## Minecraft server

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

## Local і generated paths

Gitignore захищає:

- `.env`, `.env.local`;
- `.runtime/`, logs і caches;
- Python virtualenv/cache files;
- `.arvis_mcp_memory/` і `.codex/`;
- `models/`, `ollama-models/` і типові model file extensions;
- `.local_extensions/` і `.runtime/local_extensions/`.

Не клади tracked examples всередину ignored directories. Public templates мають
містити тільки placeholders і safe example values.
