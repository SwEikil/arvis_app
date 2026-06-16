# Арвіс: локальна консольна оболонка

Перша стабільна текстова оболонка для локального AI-асистента Арвіса.

## Можливості

- REPL-чат у терміналі.
- Запити до Ollama через `/api/chat`.
- Модель за замовчуванням: `arvis`.
- Без streaming у першій версії.
- Активна історія діалогу в RAM, до 40 повідомлень.
- Placeholder для майбутнього rolling `session_summary`.
- Приховування типових thinking/reasoning блоків.
- Витягування `ACTION_INTENT` і `MEMORY_INTENT`.
- Intent Resolver v0.1 для природних команд, коли модель не дала коректний `ACTION_INTENT`.
- Command Router v0.1 для safe whitelist actions.
- Dry-run режим за замовчуванням: router показує, що зробив би, але нічого не виконує.

## Запуск

```bash
python main.py
```

Якщо використовуєш локальний venv:

```bash
.venv/bin/python main.py
```

## Doctor Mode

Doctor Mode перевіряє, чи локальна конфігурація Арвіса готова до запуску, не виконуючи destructive або invasive дій.

Запуск із project root:

```bash
python main.py doctor
```

Або з батьківської папки репозиторію:

```bash
python arvis_app/main.py doctor
```

З локального venv:

```bash
.venv/bin/python main.py doctor
```

Доступні прапорці:

- `--json` - machine-readable JSON report.
- `--verbose` - більше діагностичних деталей.
- `--strict` - режим для CI/dev checks: warnings дають exit code `1`, навіть якщо required checks пройшли.
- `--fix` - тільки safe fixes: створити локальні `logs/`, `.cache/`, `.runtime/` або safe `.env.example`, якщо їх нема.
- `--no-color` - вимкнути кольоровий text output; JSON output ніколи не містить ANSI colors.

У REPL можна виконати:

```text
/doctor
```

Статуси:

- `[OK]` - перевірка пройдена.
- `[WARN]` - не критично, але краще виправити.
- `[FAIL]` - required check не пройдений.
- `[INFO]` - необов'язкова інформація або disabled optional feature.

Приклад:

```text
[OK] Runtime: Python 3.14 found
[OK] Config: .env found
[WARN] Ollama: Ollama backend is offline or unreachable
Fix: Start Ollama or update OLLAMA_HOST in .env. Internet access is not required.
[INFO] Voice: STT backend is optional and not configured

Doctor summary:
- OK: 8
- Warnings: 1
- Failures: 0
- Info: 3
```

Doctor redacts secret-like values before printing text or JSON. Never commit `.env`, tokens, API keys, private paths, local logs, caches, runtime state, or model files. Keep only safe placeholders in `.env.example`.

## Voice Command Readiness v0.1

Voice input is optional and disabled by default. It is designed as a local, manual, one-shot microphone input layer for the existing text pipeline.

Commands:

- `/voice status` - show voice config and optional dependency status.
- `/voice test` - record a short microphone sample, run STT, and print recognized text without executing it.
- `/voice once` - record one short microphone command, print recognized text, then process it exactly like typed text.

Voice v0.1 does not implement wake word, speaker verification, always-listening background mode, audio daemon, hotkey push-to-talk, or desktop/system/browser/Spotify/YouTube audio capture. It records only from the configured microphone input.

Voice config is local-only in `.env`:

```bash
ARVIS_VOICE_ENABLED=false
ARVIS_STT_BACKEND=faster_whisper
ARVIS_STT_MODEL=small
ARVIS_STT_DEVICE=auto
ARVIS_STT_COMPUTE_TYPE=auto
ARVIS_MIC_DEVICE=
ARVIS_VOICE_RECORD_SECONDS=6
ARVIS_VOICE_LANGUAGE=auto
ARVIS_VOICE_DUCKING_ENABLED=true
ARVIS_VOICE_DUCK_PERCENT=15
ARVIS_VOICE_DUCK_RESTORE=true
```

Empty `ARVIS_MIC_DEVICE` uses the default microphone. Device names that look like monitor/output/loopback/desktop audio are rejected.

Voice audio ducking is enabled by default for `/voice test` and `/voice once`. Before recording, Arvis reads the default audio sink with `wpctl`, lowers it only if it is currently louder than `ARVIS_VOICE_DUCK_PERCENT`, and restores the previous volume afterward. It does not pause media, mute audio, unmute previously muted audio, or capture desktop/system audio. If `wpctl` is missing or ducking fails, voice recording continues and Arvis prints a warning.

Recommended first test:

```text
/voice status
/voice test
/voice once
```

If voice dependencies are missing, text mode still works. Voice dependencies are imported lazily only when a voice command is used.

## Налаштування

Публічний код не містить персональних локальних налаштувань. Імена користувачів, локальні папки, app launch commands і Minecraft server config задаються тільки локально через `.env`.

Створи локальний файл із шаблону:

```bash
cp .env.example .env
```

Потім відредагуй `.env` під свою машину. `.env` ніколи не треба комітити. `.env.example` - тільки template з placeholders.

Без `.env` Арвіс запускається з безпечними default values:

- `OLLAMA_HOST=http://127.0.0.1:11434`
- `ARVIS_MODEL=arvis`

Можна також передати env напряму:

```bash
OLLAMA_HOST=http://127.0.0.1:11434 ARVIS_MODEL=arvis python main.py
```

Підтримувані локальні env keys:

- `USER_NAME`
- `OLLAMA_HOST`
- `ARVIS_MODEL`
- `MUSIC_FOLDER`
- `DOWNLOADS_FOLDER`
- `STEAM_COMMAND`, `SPOTIFY_COMMAND`, `BRAVE_COMMAND`, `DISCORD_COMMAND`, `TELEGRAM_COMMAND`
- `ARVIS_VOICE_ENABLED`, `ARVIS_STT_BACKEND`, `ARVIS_STT_MODEL`, `ARVIS_STT_DEVICE`, `ARVIS_STT_COMPUTE_TYPE`, `ARVIS_MIC_DEVICE`, `ARVIS_VOICE_RECORD_SECONDS`, `ARVIS_VOICE_LANGUAGE`
- `MINECRAFT_SERVER_ENABLED`, `MINECRAFT_SERVER_KEY`, `MINECRAFT_SERVER_NAME`, `MINECRAFT_SERVER_CWD`, `MINECRAFT_SERVER_COMMAND`

Команди з env парсяться через `shlex.split()` і запускаються тільки як argv list з `shell=False`.

## Команди

- `/exit` або `/quit` - вихід.
- `/reset` - очистити активну історію.
- `/debug on` - увімкнути debug.
- `/debug off` - вимкнути debug.
- `/dryrun` - показати стан dry-run.
- `/dryrun on` - увімкнути dry-run.
- `/dryrun off` - вимкнути dry-run. Після цього виконуються тільки safe whitelist actions.
- `/reload` або `/restart` - перезапустити Python-процес Арвіса і підхопити оновлений код.
- `/doctor` - перевірити runtime, config, privacy safety, Ollama, actions і local storage.
- `/actions` - показати підтримувані safe desktop actions.
- `/voice status` - показати статус голосового режиму.
- `/voice test` - розпізнати тестовий голосовий зразок без виконання.
- `/voice once` - розпізнати одну голосову команду і передати її в text pipeline.
- `/history` - показати активну історію.
- `/summary` - показати поточний `session_summary`.
- `/help` - показати команди.

## Self Reload / Restart

`/reload` і `/restart` роблять одне й те саме: Арвіс зберігає мінімальний runtime state у `.runtime/reload_state.json`, після чого замінює поточний Python-процес через `os.execv()`.

Це оновлює тільки код самого Python app process. Reload не зупиняє Minecraft server, не зупиняє Ollama, не чіпає tmux і не виконує shell-команди.

Перед restart Арвіс best-effort зберігає:

- `dry_run`;
- `debug`;
- `session_summary`;
- active chat history, якщо її можна безпечно записати як JSON;
- command history, якщо її можна безпечно записати як JSON.

Якщо runtime state пошкоджений або його не вдалось прочитати, старт продовжується без падіння, а `.runtime/reload_state.json` видаляється.

Зміни в `Modelfile` reload не застосовує сам по собі. Якщо змінено модель, спочатку виконай:

```bash
ollama create arvis -f Modelfile
```

Після цього можна зробити `/reload` або `/restart`, щоб перезапустити консольний app process.

## Intent Pipeline v0.1

Арвіс має три шари для дій:

- `ACTION_INTENT` from model: основний structured намір, який модель може додати у відповідь.
- Intent Resolver fallback: окремий шар, який аналізує `user_text`, якщо `ACTION_INTENT` відсутній або router відмовив через unknown/ambiguous action чи target.
- Command Router: фінальний safety gate, який єдиний може запускати whitelist action.
- Action-aware Response Renderer: фінальний текст для користувача, який будується з `CommandResult`, якщо дія була оброблена.

Intent Resolver не виконує команди. Він тільки повертає candidate intent з `action`, `target`, `risk`, `need_confirmation`, `confidence`, `source`, `reason`. Якщо `confidence < 0.65`, дія не виконується і Арвіс просить коротке уточнення.

Resolver має deterministic heuristics для очевидних фраз і LLM fallback через Ollama для command-like текстів, які heuristics не зрозуміли. LLM resolver отримує тільки список allowed actions і має повертати JSON без raw commands.

### Action-aware Response Renderer v0.5

Якщо дія була розпізнана і Command Router повернув `CommandResult`, відповідь користувачу будується з router result, а не з raw model text. Це захищає UX від ситуацій, коли модель пише зайвий або неправильний текст перед дією, наприклад просить IP/domain для локального Minecraft server.

Правила:

- Router result має пріоритет над assistant text.
- Raw model answer не показується як основна відповідь для handled actions.
- У `/debug on` raw assistant message може показуватись окремою diagnostic panel.
- Diagnostic panels `ACTION INTENT`, `INTENT RESOLVER`, `COMMAND ROUTER`, `MEMORY INTENT` лишаються.
- Renderer не виконує команди і не змінює safety gate; він тільки формує текст.

Приклади:

- `minecraft_server_start` + `minecraft_server_already_running_unmanaged` -> `Сервер Minecraft server уже працює, сер...`
- `minecraft_server_status` unmanaged -> `Minecraft server працює, сер, але він запущений не через Арвіса/tmux...`
- `volume_up` executed -> `Гучність збільшено, сер.`
- dangerous blocked -> `Ні, сер. Це небезпечна дія, я її не виконуватиму.`

Панель `INTENT RESOLVER` показує:

- source;
- matched heuristic;
- action;
- target;
- confidence;
- reason;
- debug warning, якщо reason згадує allowed action, але action field порожній;
- чи буде intent передано в router.

Resolver v0.3 спочатку блокує destructive phrases, потім перевіряє like/favorite, seek, repeat/shuffle, media, volume, app launch, Minecraft і тільки після цього context repair та LLM fallback. Це не дає фразам типу `додай до вподобаного` помилково спрацьовувати як `volume_up`.

### Natural Commands

Приклади фраз, які resolver розуміє без `ACTION_INTENT`:

- `Арвіс, це слабовато якось. Додай ще гучності` -> `volume_up`
- `Поверни звук` -> `volume_unmute`
- `Яка гучність?` -> `volume_status`
- `Постав звук на 30` -> `volume_set`, params `level_percent=30`
- `Вруби споті` -> `open_app`, target `spotify`
- `Постав це на паузу` -> `music_pause`
- `Що зараз грає?` -> `media_status`
- `зупини сервер`, `вимкни сервер` -> `minecraft_server_stop`, target `default`
- `надави наступну`, `скипни` -> `music_next`
- `попередню`, `поверни минулу пісню`, `включи минулу пісню`, `верни попередню` -> `music_previous`
- `зніми з паузи`, `віднови`, `давай далі` -> `music_play`
- `відмотай назад`, `відмотай назад на 10 секунд` -> `media_seek_backward`
- `додай до вподобаного`, `мені подобається ця пісня` -> `music_like_current` (recognized but unsupported without Spotify API)
- `Ще трохи тихіше` -> `volume_down`
- `Підніми майн сервер` -> `start_minecraft_server`, target `minecraft_server`

### Context Commands

Main зберігає останні 10 command results у RAM: `user_text`, normalized action, normalized target, executed, counter.

Контекстні правила v0.1:

- `ще`, `ще раз`, `давай ще` повторюють попередні `volume_up`, `volume_down`, `music_next`, `music_previous`.
- Після `music_pause`: `віднови`, `продовж`, `зніми з паузи`, `зроби нормально`, `поверни як було`, `поверни назад` -> `music_play`.
- Після `music_play`: `постав назад на паузу`, `знову пауза`, `назад` -> `music_pause`.
- `назад`, `поверни назад`, `як було`, `не те`, `забудь` роблять obvious reverse:
  - `volume_mute` -> `volume_unmute`
  - `volume_down` -> `volume_up`
  - `volume_up` -> `volume_down`
  - `music_pause` -> `music_play`
  - `music_play` -> `music_pause`
  - `music_next` -> `music_previous`

Якщо reverse неочевидний, resolver не виконує дію і просить уточнення.

## Command Router v0.4

Router приймає тільки structured `ActionIntent`: або розпарсений з відповіді моделі, або створений Intent Resolver. Він не виконує raw shell-команди з відповіді моделі.

Правила безпеки:

- Виконується тільки `risk: "safe"`.
- Якщо `need_confirmation: true`, дія не виконується у v0.1.
- `medium`, `dangerous`, невідомі та не-whitelist дії не виконуються.
- Для системних дій використовуються тільки `subprocess.run([...], shell=False)` або `subprocess.Popen([...], shell=False)`.
- Відкриття файлів, URL, package manager, install scripts, shell snippets, Python dynamic execution APIs і shell mode не використовуються.

Якщо модель помилково позначила очевидну whitelist-команду як risky, main може викликати Intent Resolver для repair. Але якщо user text містить destructive keywords (`видали`, `format`, `sudo`, `rm -rf`, `запусти команду`, `виконай bash` тощо), дія блокується і не ремонтується у safe action.

Router outcome status показує причину результату, а не просто "небезпечно/небезпечно". Safe action може не виконатись з інших причин:

- `executed` - команда реально виконана.
- `dry_run` - dry-run увімкнений, команда тільки показана.
- `blocked_dangerous` - dangerous/destructive action або destructive user text; `is_safety_block=True`.
- `blocked_confirmation_required` - action потребує confirmation, а confirmation execution ще не підтримується.
- `unsupported` - safe action розпізнана, але реалізація ще не додана.
- `not_configured` - action safe, але потребує явної конфігурації.
- `unknown_action` - action не входить у whitelist.
- `unknown_target` - target не входить у whitelist.
- `ambiguous` - router не зміг однозначно визначити параметр або напрям.
- `command_failed` - whitelist command була запущена, але системний інструмент повернув помилку.

Приклади:

- `додай до вподобаного` -> `music_like_current`, `status=unsupported`, `reason_code=spotify_api_required`, не safety block.
- `minecraft_server_start` без `tmux` -> `status=not_configured`, `reason_code=tmux_missing`.
- `minecraft_server_logs` без `logs/latest.log` -> `status=not_configured`, `reason_code=minecraft_log_not_found`.
- `видали всі файли` -> `status=blocked_dangerous`, `is_safety_block=True`.

Після кожного `ACTION_INTENT` показується окрема панель `COMMAND ROUTER`:

- стан dry-run;
- чи була дія виконана;
- status;
- reason_code;
- is_safety_block;
- original action і normalized action;
- original target і normalized target;
- params, якщо вони є;
- повідомлення router;
- details, якщо є.

`ActionIntent` підтримує optional `params`. Якщо модель не передала `params`, код працює як раніше.

### Підтримувані actions

Media:

- `music_play_pause`
- `music_next`
- `music_previous`
- `music_play`
- `music_pause`
- `play_music_by_mood`
- `media_seek_forward`
- `media_seek_backward`
- `music_repeat_track`
- `music_repeat_playlist`
- `music_repeat_off`
- `music_shuffle_on`
- `music_shuffle_off`
- `music_shuffle_toggle`
- `music_like_current`
- `media_status`

Volume:

- `volume_up`
- `volume_down`
- `volume_mute`
- `volume_unmute`
- `volume_status`
- `volume_set`

Apps:

- `open_app`
- `launch_app`

Minecraft:

- `minecraft_server_status`
- `minecraft_server_start`
- `minecraft_server_stop`
- `minecraft_server_restart`
- `minecraft_server_logs`
- `minecraft_server_diagnostics`
- `minecraft_server_metrics`
- `start_minecraft_server`

### Action aliases

Модель може видати не точну whitelist-назву, а синонім. Router нормалізує `action` і `target` перед whitelist-check.

App launch aliases нормалізуються до `open_app`:

- `launch_application`
- `launch_app`
- `open_application`
- `open_app`
- `start_application`
- `start_app`
- `run_application`
- `run_app`

Volume aliases:

- `adjust_volume` + `lower`, `down`, `quieter`, `тихіше`, `нижче` -> `volume_down`
- `adjust_volume` + `up`, `higher`, `louder`, `голосніше`, `вище` -> `volume_up`
- `adjust_volume` + `mute`, `muted`, `silence`, `вимкни звук` -> `volume_mute`
- `adjust_volume`, `change_volume`, `set_volume`, `volume` inspect target first, then original user text.
- `decrease_volume`, `lower_volume`, `volume_decrease`, `volume_lower` -> `volume_down`
- `increase_volume`, `raise_volume`, `volume_increase`, `volume_higher` -> `volume_up`
- `restore_audio`, `restore_sound`, `restore_volume`, `unmute_audio`, `unmute_sound`, `unmute_volume`, `enable_audio`, `enable_sound`, `turn_on_sound`, `sound_on`, `volume_restore` -> `volume_unmute`
- `mute_audio`, `mute_sound`, `mute_volume`, `disable_audio`, `disable_sound`, `turn_off_sound`, `sound_off` -> `volume_mute`

Explicit mute behavior:

- `volume_mute` uses `wpctl set-mute @DEFAULT_AUDIO_SINK@ 1`.
- `volume_unmute` uses `wpctl set-mute @DEFAULT_AUDIO_SINK@ 0`.
- Explicit commands like `вимкни звук` and `поверни звук` do not use toggle mute.

User-text volume phrases:

- Down: `тихіше`, `тише`, `потихіше`, `зроби тихіше`, `зменш гучність`, `зменши звук`, `знизь гучність`, `приглуши`, `приглуши звук`, `занадто гучно`, `занадто голосно`, `lower`, `quieter`, `decrease volume`, `volume down`
- Up: `гучніше`, `голосніше`, `додай гучності`, `додай ще гучності`, `додай звук`, `додай ще`, `зроби голосніше`, `зроби гучніше`, `підніми гучність`, `прибав`, `прибав звук`, `слабовато`, `слабувато`, `замало`, `ще гучності`, `ще голосніше`, `louder`, `volume up`, `increase volume`
- Mute: `вимкни звук`, `вируби звук`, `відключи звук`, `без звуку`, `зам'ють`, `замуть`, `mute`, `sound off`
- Unmute: `поверни звук`, `увімкни звук`, `включи звук`, `верни звук`, `звук назад`, `поверни аудіо`, `увімкни аудіо`, `unmute`, `sound on`, `restore sound`, `restore audio`

Volume params:

- `volume_up` і `volume_down` використовують `params.step_percent`.
- Default: `5`.
- Якщо user text містить число, використовується перше число.
- Clamp: `1..50`.
- Приклади: `зроби гучніше` -> `5%+`, `зроби гучніше на 30` -> `30%+`, `зроби тихіше на 15` -> `15%-`.

Якщо user text містить чітку volume-фразу, вона має пріоритет над нечіткою action від моделі. Наприклад, `це слабовато, додай ще гучності` нормалізується до `volume_up`, навіть якщо модель дала неідеальну action.

Якщо модель видає `adjust_volume` + `music`, `audio`, `sound`, `system`, `browser`, `brave` або `player`, але напрям не знайдено ні в target, ні в тексті користувача, router не вгадує дію і нічого не виконує.

У v0.1 per-app volume не підтримується. Наприклад, `зроби браузер тихіше` змінює default audio sink через `wpctl`, а в details буде вказано, що per-app volume буде додано пізніше.

Media aliases:

- Pause: `pause_playback`, `pause_media`, `pause_music`, `pause_track`, `pause_song`, `stop_playback`, `stop_media`, `stop_music`, `pause_browser_activity`, `pause_browser` -> `music_pause`
- Play/resume: `resume_playback`, `resume_media`, `resume_music`, `continue_playback`, `continue_media`, `continue_music`, `unpause`, `unpause_media`, `restore_playback`, `restore_music`, `play_current`, `play_current_track`, `play_media`, `play_music` -> `music_play`
- Toggle: `toggle_playback`, `toggle_media`, `toggle_music`, `play_pause`, `media_play_pause` -> `music_play_pause`
- Next: `play_next_track`, `next_track`, `next_song`, `skip_track`, `skip_song`, `skip_next`, `media_next`, `music_next_track`, `play_next_song`, `go_next`, `switch_track_next` -> `music_next`
- Previous: `play_previous_track`, `previous_track`, `previous_song`, `prev_track`, `prev_song`, `media_previous`, `music_previous_track`, `play_previous_song`, `go_previous`, `switch_track_previous` -> `music_previous`
- Seek: `seek_forward`, `skip_forward`, `fast_forward` -> `media_seek_forward`; `seek_backward`, `skip_back`, `skip_backward`, `rewind` -> `media_seek_backward`; `seek_media` + `forward`, `ahead`, `вперед` -> `media_seek_forward`; `seek_media` + `backward`, `back`, `назад` -> `media_seek_backward`
- Repeat: `repeat_song`, `repeat_track`, `loop_track` -> `music_repeat_track`; `repeat_playlist`, `loop_playlist` -> `music_repeat_playlist`; `repeat_off`, `loop_off` -> `music_repeat_off`
- Shuffle: `shuffle_on`, `shuffle_off`, `shuffle`, `shuffle_toggle`, `toggle_shuffle` -> shuffle actions
- Like/save: `like_current`, `like_current_song`, `save_current_song`, `favorite_current_song` -> `music_like_current`
- Minecraft start: `start_minecraft_server`, `start_server`, `server_start`, `minecraft_start`, `launch_server`, `launch_minecraft_server` -> `minecraft_server_start`
- Minecraft stop: `stop_server`, `server_stop`, `stop_minecraft_server`, `minecraft_stop`, `shutdown_server`, `shutdown_minecraft_server` -> `minecraft_server_stop`
- Minecraft restart: `restart_server`, `server_restart`, `restart_minecraft_server`, `minecraft_restart`, `reboot_server` -> `minecraft_server_restart`
- Minecraft status: `server_status`, `check_server`, `check_minecraft_server`, `minecraft_status`, `get_server_status` -> `minecraft_server_status`

Minecraft/server intent priority:

- Фрази з `сервер`, `майн сервер`, `майнкрафт сервер`, `minecraft server`, `mc server` перевіряються до media actions.
- `зупини сервер` і `зупини майн сервер` йдуть у `minecraft_server_stop`, а не в `music_pause`.
- `зупини музику`, `зупини відео`, `постав на паузу` лишаються media actions.
- Якщо модель дала `stop_server` + `Minecraft server` з `risk=medium`/`need_confirmation=true`, router нормалізує це до configured safe `minecraft_server_stop`, якщо user text не містить dangerous OS keywords.

Media target aliases:

- `spotify`, `music`, `media`, `current_media`, `current_track`, `current_song`, `active_player`, `player`, `browser`, `brave`, `youtube`, `video`

Media targets do not use app launch whitelist. For media actions, router normalizes the action and `actions/media.py` selects an available MPRIS player through `playerctl -l`.

User-text media phrases:

- Next: `наступну`, `наступний трек`, `наступна пісня`, `давай наступну`, `перемкни`, `далі`, `давай далі`, `скипни`, `пропусти`, `next`, `skip`, `next track`
- Previous: `попередню`, `попередній трек`, `попередня пісня`, `назад трек`, `верни трек`, `previous`, `prev`, `previous track`
- Pause: `пауза`, `постав на паузу`, `постав це на паузу`, `зупини`, `зупини музику`, `зупини відео`, `стопни`, `pause`
- Play: `віднови`, `продовж`, `продовж музику`, `продовж відео`, `зніми з паузи`, `поверни відтворення`, `увімкни назад`, `play`, `resume`, `continue`
- Seek forward: `перемотай вперед`, `мотай вперед`, `промотай вперед`, `проскочи вперед`, `перемотай на 10 секунд`, `вперед на 30`, `skip forward`, `seek forward`
- Seek backward: `перемотай назад`, `мотай назад`, `відмотай назад`, `назад на 10 секунд`, `поверни на 5 секунд`, `skip back`, `seek backward`
- Repeat: `постав пісню на повтор`, `повторюй цю пісню`, `зацикли пісню`, `постав плейлист на повтор`, `повторюй плейлист`, `вимкни повтор`, `repeat song`, `repeat playlist`, `repeat off`
- Shuffle: `увімкни shuffle`, `увімкни перемішування`, `перемішай пісні`, `вимкни shuffle`, `вимкни перемішування`, `перемкни shuffle`, `shuffle`
- Like/save: `мені подобається ця пісня`, `додай до вподобаного`, `додай цю пісню до вподобаного`, `лайкни цю пісню`, `додай у вподобане`, `додай у лайкнуті`, `додай в liked songs`, `збережи цю пісню`, `like this song`, `save current song`

Volume heuristics do not trigger on a bare `додай`. Для `volume_up` потрібен явний audio context: `гучність`, `звук`, `гучніше`, `голосніше`, `слабовато`, `додай гучності`, `додай звук` тощо. Тому `додай до вподобаного` йде в `music_like_current`, а не в `volume_up`.

Media params:

- `media_seek_forward` і `media_seek_backward` використовують `params.seconds`.
- Default: `5`.
- Якщо user text містить число, використовується перше число.
- Clamp: `1..300`.
- Приклади: `перемотай вперед` -> `5` seconds, `перемотай вперед на 30 секунд` -> `30`, `назад на 15 секунд` -> `15`.

Repeat/shuffle/seek залежать від підтримки поточного MPRIS player. Якщо `playerctl` або player не підтримує команду, Арвіс показує clear error у details і не падає.

`music_like_current` у v0.4 тільки розпізнається як safe unsupported action. Реальний like/save потребує Spotify Web API + OAuth, тому router повертає `executed: false`, `status=unsupported`, `reason_code=spotify_api_required` і не робить GUI automation.

Приклади:

- `launch_application` + `Spotify` -> `open_app` + `spotify`
- `adjust_volume` + `lower` -> `volume_down`
- `adjust_volume` + `music` + user text `Арвіс, зроби тихіше` -> `volume_down`
- `restore_audio` + `system_sound` + user text `Арвіс, поверни звук` -> `volume_unmute`
- unknown safe action + user text `Арвіс, додай ще гучності` -> `volume_up`
- `play_next_track` + `spotify` -> `music_next`
- user text `надави наступну` -> `music_next`
- user text `зроби гучніше на 30` -> `volume_up`, `params.step_percent=30`
- user text `перемотай вперед на 30 секунд` -> `media_seek_forward`, `params.seconds=30`
- user text `постав пісню на повтор` -> `music_repeat_track`
- user text `увімкни shuffle` -> `music_shuffle_on`
- user text `додай цю пісню до вподобаного` -> `music_like_current`, unsupported until Spotify API is configured
- user text `статус майн сервера` -> `minecraft_server_status`, target `default`
- user text `запусти майн сервер` -> `minecraft_server_start`, target `default`
- user text `перезапусти майн сервер` -> `minecraft_server_restart`, target `default`
- `pause_browser_activity` + `active_tab` -> `music_pause` + `brave`

### Whitelist apps

Дозволені target keys:

- `steam`
- `spotify`
- `brave`
- `discord`
- `telegram`

Router сам нормалізує типові українські та англійські назви, наприклад `стім` -> `steam`, `телега` -> `telegram`, `брейв` -> `brave`.

Модель не може передати довільну команду запуску. Вона може просити тільки `action` і `target`, а router сам вирішує, чи це дозволено.

### Minecraft server

Minecraft Server Manager optional. Public repo містить тільки generic support; реальна назва сервера, cwd і start command мають бути задані локально в `.env`.

Мінімальний локальний config:

```dotenv
MINECRAFT_SERVER_ENABLED=true
MINECRAFT_SERVER_KEY=default
MINECRAFT_SERVER_NAME=My Minecraft Server
MINECRAFT_SERVER_CWD=/absolute/path/to/server
MINECRAFT_SERVER_COMMAND=./start.sh
```

Якщо Minecraft config disabled, missing або incomplete, router повертає `status=not_configured` і нічого не сканує/запускає. Репозиторій не містить реальної назви, path або command приватного сервера.

Target aliases `minecraft_server`, `minecraft server`, `майнкрафт сервер`, `сервер майнкрафт`, `майн сервер`, `mc server` нормалізуються до generic key `default`, якщо локальна `.env` не задає інший key.

Поведінка:

- `minecraft_server_status` перевіряє configured tmux session і process candidates всередині server directory.
- `minecraft_server_start` не запускає дубль: якщо session/process уже є, повертає `status=already_running`.
- Якщо сервер працює поза Arvis/tmux, status пояснює unmanaged state: `control_available: False`, stop/restart недоступні, сервер треба один раз зупинити вручну і потім стартувати через Арвіса.
- Якщо unmanaged server уже працює, start повертає `reason_code=minecraft_server_already_running_unmanaged` і не запускає другий процес.
- Запуск іде тільки через `tmux new-session -d -s SESSION -c SERVER_DIR ...configured command...`, де command береться з `.env` і парситься через `shlex.split()`.
- Якщо `tmux` відсутній: `status=not_configured`, `reason_code=tmux_missing`. Арвіс не встановлює tmux автоматично.
- Якщо configured start script відсутній: `status=not_configured`, `reason_code=minecraft_start_script_missing`.
- `minecraft_server_stop` для managed tmux session відправляє `stop` через `tmux send-keys`; `kill`, `killall`, `pkill` у v0.1 не використовуються.
- Unmanaged сервери не зупиняються і не рестартяться автоматично: stop повертає `reason_code=minecraft_server_unmanaged`, restart повертає `reason_code=minecraft_server_unmanaged_restart`.
- `minecraft_server_restart` робить graceful stop + start, або просто start, якщо сервер не запущений.
- `minecraft_server_logs` читає останні 40 рядків `logs/latest.log`, не весь файл.
- `minecraft_server_diagnostics` показує, що саме Арвіс бачить у tmux, start script і `/proc` process candidates.
- `minecraft_server_metrics` показує read-only snapshot CPU/RAM для server Java processes і ignored PrismLauncher client processes.

Process detection v0.4:

- `managed_server`: існує configured tmux session, і strong Java server process у server directory належить цьому managed запуску; він не показується як unmanaged.
- `unmanaged_server`: tmux session немає, але знайдений strong Java server process у server directory. Це блокує duplicate start, але stop/restart лишаються unsupported.
- `ignored_client`: PrismLauncher/client Java process, наприклад `prismlauncher --launch`, `prismrun --launch`, `org.prismlauncher.EntryPoint`, `minecraft-1.20.1-client.jar`, `NewLaunch.jar`, `/app/bin/prismrun`, `/usr/bin/bwrap --args`. Він не вважається сервером.
- `weak_cwd`: процес має cwd всередині server directory, але не схожий на Minecraft Java server, наприклад `bash`, `python`, `tmux`, або Java без Minecraft/server marker. Weak candidate не вважається running server і не блокує start.
- Duplicate detection: якщо знайдено більше одного server Java process, details містять `duplicate_server_processes_detected: True` і warning `Multiple Minecraft server Java processes detected.`
- Ambiguous detection: якщо знайдені тільки weak candidates, status повертає `status=ambiguous`, `reason_code=minecraft_process_detection_ambiguous`, а details містять `process_candidates` з `pid`, `ppid`, `comm`, `cwd`, `cmdline_short`, `classification`, `match_strength`, `match_reasons`, CPU/RAM fields.

Metrics:

- `minecraft_server_metrics` показує `running`, `managed_by_tmux`, `server_pids`, `cpu_percent`, `memory_rss_mb`, `memory_rss_gb`, `duplicate_server_processes_detected`, `client_processes_detected`, command short, cwd і uptime, якщо доступно.
- Метрики читаються через `/proc` і `ps` без shell pipeline; action read-only і не змінює процеси.
- `tmux` сам майже не навантажує CPU. Якщо після запуску через Арвіса система гріється, найчастіші причини: dedicated server Java process, одночасно відкритий PrismLauncher client, chunk loading, моди, генерація світу або resource processing.
Read-only actions `minecraft_server_status`, `minecraft_server_logs`, `minecraft_server_diagnostics` і `minecraft_server_metrics` працюють навіть при `/dryrun on`. Start/stop/restart у dry-run тільки показують, що було б зроблено.

Prompt note: для Minecraft server фраз Арвіс має використовувати локальний Minecraft Server Manager і не питати IP/domain.

Приклади фраз:

- `статус майн сервера`, `статус сервера`, `перевір майн сервер`, `чи працює майн сервер`
- `запусти майн сервер`, `підніми майн сервер`, `start minecraft server`
- `зупини майн сервер`, `вимкни майнкрафт сервер`
- `перезапусти майн сервер`, `restart minecraft server`
- `покажи логи майн сервера`, `server logs`
- `діагностика майн сервера`, `що арвіс бачить у процесах сервера`, `покажи процеси майн сервера`, `server diagnostics`
- `скільки пам'яті хаває сервер`, `скільки ресурсів їсть сервер`, `навантаження майн сервера`, `cpu майн сервера`, `ram майн сервера`, `minecraft server metrics`, `server performance`

Ручна перевірка:

1. `/dryrun`
2. `статус майн сервера`
3. `покажи логи майн сервера`
4. `покажи процеси майн сервера`
5. `навантаження майн сервера`
6. `/dryrun off`
7. `запусти майн сервер`
8. `статус майн сервера`
9. `зупини майн сервер`

## Поточні обмеження

- `ACTION_INTENT` виконується тільки через Command Router і тільки для safe whitelist actions.
- `MEMORY_INTENT` тільки показується, але не зберігається.
- `play_music_by_mood` поки не підбирає playlist за mood: v0.1 тільки запускає playback на доступному player.
- Medium/dangerous confirmations ще не реалізовані.
- SQLite-пам'ять, voice і wake word не реалізовані в цій версії.
