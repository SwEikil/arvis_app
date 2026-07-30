# Voice input

[← Індекс документації](README.md) · [Головна сторінка](../README.md)

Voice layer опційний, disabled by default і працює лише як explicit one-shot
microphone input. Always-listening, wake word, speaker verification, audio daemon
і background recording не реалізовані.

## Команди

- `/voice status` — config і optional dependency status.
- `/voice warmup` — load/download configured STT model без recording/ducking.
- `/voice diagnose` — record, transcribe, normalize та показати resolver
  diagnostics без execution; diagnostic sample копіюється в ignored runtime
  debug path.
- `/voice test` — record і надрукувати recognized text без execution.
- `/voice once` — record, normalize й передати recognized text у той самий
  pipeline, що й typed command.

`/voice once` не обходить Intent Resolver або Command Router.

## Pipeline

```text
load config
  → dependency/preflight checks
  → STT warmup
  → optional audio ducking
  → explicit microphone recording
  → restore volume
  → transcription
  → correction/normalization
  → diagnostics or normal text pipeline
```

Arvis не записує desktop/system/browser/Spotify/YouTube output. Device names,
схожі на monitor, output, loopback або desktop audio, відхиляються.

## Конфігурація

Основний template:

```dotenv
ARVIS_VOICE_ENABLED=false
ARVIS_STT_BACKEND=faster_whisper
ARVIS_STT_MODEL=small
ARVIS_STT_DEVICE=auto
ARVIS_STT_COMPUTE_TYPE=auto
ARVIS_MIC_DEVICE=
ARVIS_VOICE_RECORD_SECONDS=6
ARVIS_VOICE_LANGUAGE=uk
ARVIS_VOICE_ALLOWED_LANGUAGES=uk,ru,en,no
ARVIS_VOICE_MIN_RMS=0.008
ARVIS_VOICE_MIN_PEAK=0.03
ARVIS_VOICE_DEBUG_SAVE_LAST=false
ARVIS_VOICE_DUCKING_ENABLED=true
ARVIS_VOICE_DUCK_PERCENT=15
ARVIS_VOICE_DUCK_RESTORE=true
```

Порожній `ARVIS_MIC_DEVICE` означає default microphone input. Для українських
команд лишай `ARVIS_VOICE_LANGUAGE=uk`; `auto` доречний лише для свідомого
mixed-language detection.

Повний env reference: [`configuration.md`](configuration.md).

## Audio ducking

Якщо ducking enabled, Arvis спочатку готує STT, потім best-effort зменшує
default sink volume лише на час recording і відновлює попередній стан до
transcription. Він не pause media, не mute/unmute чужий стан і не перехоплює
desktop audio.

Відсутній `wpctl` або ducking failure не повинні блокувати recording; користувач
отримує warning.

## Optional dependencies і debug audio

Voice packages імпортуються лише при voice command. Text mode та unit tests
мають працювати без Faster Whisper, sounddevice й NumPy.

Якщо `ARVIS_VOICE_DEBUG_SAVE_LAST=true`, останній sample копіюється у
`.runtime/voice_debug/last_voice.wav`. Цей path ignored і не повинен
публікуватися чи комітитися.

Рекомендований порядок ручної перевірки:

```text
/voice status
/voice warmup
/voice diagnose
/voice test
/voice once
```
