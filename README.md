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

## Налаштування

За замовчуванням:

- `OLLAMA_HOST=http://127.0.0.1:11434`
- `ARVIS_MODEL=arvis`

Можна змінити через env:

```bash
OLLAMA_HOST=http://127.0.0.1:11434 ARVIS_MODEL=arvis python main.py
```

## Команди

- `/exit` або `/quit` - вихід.
- `/reset` - очистити активну історію.
- `/debug on` - увімкнути debug.
- `/debug off` - вимкнути debug.
- `/dryrun` - показати стан dry-run.
- `/dryrun on` - увімкнути dry-run.
- `/dryrun off` - вимкнути dry-run. Після цього виконуються тільки safe whitelist actions.
- `/history` - показати активну історію.
- `/summary` - показати поточний `session_summary`.
- `/help` - показати команди.

## Intent Pipeline v0.1

Арвіс має три шари для дій:

- `ACTION_INTENT` from model: основний structured намір, який модель може додати у відповідь.
- Intent Resolver fallback: окремий шар, який аналізує `user_text`, якщо `ACTION_INTENT` відсутній або router відмовив через unknown/ambiguous action чи target.
- Command Router: фінальний safety gate, який єдиний може запускати whitelist action.

Intent Resolver не виконує команди. Він тільки повертає candidate intent з `action`, `target`, `risk`, `need_confirmation`, `confidence`, `source`, `reason`. Якщо `confidence < 0.65`, дія не виконується і Арвіс просить коротке уточнення.

Resolver має deterministic heuristics для очевидних фраз і LLM fallback через Ollama для command-like текстів, які heuristics не зрозуміли. LLM resolver отримує тільки список allowed actions і має повертати JSON без raw commands.

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
- `Вруби споті` -> `open_app`, target `spotify`
- `Постав це на паузу` -> `music_pause`
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

## Command Router v0.2

Router приймає тільки structured `ActionIntent`: або розпарсений з відповіді моделі, або створений Intent Resolver. Він не виконує raw shell-команди з відповіді моделі.

Правила безпеки:

- Виконується тільки `risk: "safe"`.
- Якщо `need_confirmation: true`, дія не виконується у v0.1.
- `medium`, `dangerous`, невідомі та не-whitelist дії не виконуються.
- Для системних дій використовуються тільки `subprocess.run([...], shell=False)` або `subprocess.Popen([...], shell=False)`.
- Відкриття файлів, URL, package manager, install scripts, shell snippets, `eval`, `exec` і `shell=True` не використовуються.

Якщо модель помилково позначила очевидну whitelist-команду як risky, main може викликати Intent Resolver для repair. Але якщо user text містить destructive keywords (`видали`, `format`, `sudo`, `rm -rf`, `запусти команду`, `виконай bash` тощо), дія блокується і не ремонтується у safe action.

Після кожного `ACTION_INTENT` показується окрема панель `COMMAND ROUTER`:

- стан dry-run;
- чи була дія виконана;
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

Volume:

- `volume_up`
- `volume_down`
- `volume_mute`
- `volume_unmute`

Apps:

- `open_app`
- `launch_app`
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

`music_like_current` у v0.2 тільки розпізнається. Реальний like/save потребує Spotify Web API + OAuth, тому router повертає `executed: false` і не робить GUI automation.

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
- `pause_browser_activity` + `active_tab` -> `music_pause` + `brave`

### Whitelist apps

Дозволені target keys:

- `steam`
- `spotify`
- `brave`
- `discord`
- `telegram`
- `minecraft_server`

Router сам нормалізує типові українські та англійські назви, наприклад `стім` -> `steam`, `телега` -> `telegram`, `брейв` -> `brave`.

Модель не може передати довільну команду запуску. Вона може просити тільки `action` і `target`, а router сам вирішує, чи це дозволено.

### Minecraft server

У v0.1 Minecraft server навмисно не налаштований. Для майбутнього запуску потрібно явно додати запис у `MINECRAFT_SERVERS` в `actions/apps.py`, наприклад:

```python
MINECRAFT_SERVERS = {
    "main": {
        "name": "main",
        "cwd": "/absolute/path/to/server",
        "command": ["./start.sh"],
    }
}
```

Router не вигадує шлях, не запускає `.jar` або `.sh`, якщо шлях і команда не прописані явно в whitelist/config.

## Поточні обмеження

- `ACTION_INTENT` виконується тільки через Command Router і тільки для safe whitelist actions.
- `MEMORY_INTENT` тільки показується, але не зберігається.
- `play_music_by_mood` поки не підбирає playlist за mood: v0.1 тільки запускає playback на доступному player.
- Medium/dangerous confirmations ще не реалізовані.
- SQLite-пам'ять, voice і wake word не реалізовані в цій версії.
