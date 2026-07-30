# Browser Vision Agent

[← Індекс документації](README.md) · [Головна сторінка](../README.md)

Browser Vision Agent — experimental controlled-browser subsystem для одного
явного whitelisted demo task. Він відокремлений від observation-only
[Browser Observer](browser_observer.md).

## Реалізований task

- Action: `browser_task_run`.
- Target: `humanbenchmark_aim`.
- URL: `https://humanbenchmark.com/tests/aim`.
- Goal: до 30 підтверджених Aim Trainer targets.

Це не generic auto-clicker і не public framework для gameplay automation.

## Optional setup

```bash
.venv/bin/python -m pip install playwright opencv-python numpy
.venv/bin/python -m playwright install chromium
```

Ці heavy optional dependencies не входять до baseline `requirements.txt` і не
потрібні для core tests.

Приклад:

```text
/dryrun off
відкрий тренування аіма і порази 30 цілей
```

## Safety boundaries

- Використовується clean non-persistent Playwright Chromium context.
- Agent не attach-иться до normal Brave/Firefox profile.
- Arbitrary URLs не приймаються.
- System mouse і full desktop не контролюються.
- Allowed click region, confidence threshold, attempts, consecutive failures і
  runtime мають hard limits.
- Unexpected pages/windows, changed viewport, blocked signals або unstable
  browser state зупиняють task.
- Login, CAPTCHA, payment, purchase, download і permission flows заборонені.
- Attempts рахуються окремо від confirmed hits; click attempt не означає success.

Debug screenshots і per-iteration JSONL disabled by default. Локально:

```dotenv
ARVIS_BROWSER_DEBUG_SAVE=true
```

Generated files зберігаються під `.runtime/browser_debug/` і ignored by git.

## Не плутати з Browser Observer

Browser Observer не виконує click/keypress/form submit. Його public contract:

```text
observe → detect → emit structured event → log/notify
```

Точний observer contract і schema v1:
[`browser_observer.md`](browser_observer.md).
