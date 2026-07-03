from __future__ import annotations

from actions.apps import APP_WHITELIST
from actions.browser_agent import BROWSER_TASKS
from command_router import CommandResult
from intent_resolver import ResolvedIntent

WATCH_SAFETY_SHORT = "Режим спостереження: без кліків і натискань."


def render_final_response(
    assistant_message: str,
    command_result: CommandResult | None,
    resolver_result: ResolvedIntent | None = None,
    debug: bool = False,
) -> str:
    if command_result is None:
        return assistant_message

    action = command_result.normalized_action or command_result.action
    status = command_result.status
    reason_code = command_result.reason_code or ""

    if status == "dry_run":
        return _render_dry_run(action, command_result)

    if status == "blocked_dangerous" or command_result.is_safety_block:
        return "Ні, сер. Це небезпечна дія, я її не виконуватиму."

    minecraft_response = _render_minecraft(action, command_result)
    if minecraft_response is not None:
        return minecraft_response

    if status == "unsupported":
        if action == "music_like_current" or reason_code == "spotify_api_required":
            return "Лайк поточної пісні потребує Spotify API, сер. Він ще не налаштований."
        return f"Ця дія розпізнана, сер, але поки не підтримується: {reason_code or 'unsupported'}."

    if status == "not_configured":
        if action == "browser_task_run":
            return "Browser Agent треба спершу налаштувати: встанови playwright/opencv-python/numpy, сер."
        if action.startswith("browser_watch_"):
            details = _parse_details(command_result.details)
            reason = details.get("reason_code") or reason_code or ""
            if reason == "profile_start_url_missing":
                return "Профіль спостереження без start_url, сер. Poll-once не запускав."
            if reason == "playwright_missing":
                return "Browser Observer потребує Playwright, сер. Встанови optional dependency."
            if reason == "playwright_browser_missing":
                return "Playwright Chromium не встановлений, сер. Запусти playwright install chromium."
            return "Browser Observer ще не налаштований для poll-once, сер."
        return f"Цю дію треба спершу налаштувати, сер: {reason_code or command_result.message}."

    if status == "unknown_action":
        return "Не знаю такої дозволеної дії, сер. Нічого не виконував."

    if status == "blocked":
        if action == "browser_task_run":
            return "Browser task зупинено, сер: сторінка вийшла за межі дозволеного сценарію."
        if action.startswith("browser_watch_"):
            details = _parse_details(command_result.details)
            block_type = details.get("block_type") or _block_type_from_error(details.get("last_error") or command_result.message)
            if block_type == "page_signal":
                signal = details.get("signal") or _signal_from_error(details.get("last_error") or "")
                suffix = f": {signal}" if signal else ""
                return f"Observer зупинено через safety signal сторінки{suffix}. {WATCH_SAFETY_SHORT}"
            if block_type == "url_allowlist":
                return f"URL поза allowlist профілю. Browser не запускався або watch зупинено. {WATCH_SAFETY_SHORT}"
            return f"Observer заблоковано, сер. Browser не запускав або подію не зберігав. {WATCH_SAFETY_SHORT}"
        return "Дію зупинено, сер."

    if status == "unknown_target":
        target = command_result.normalized_target or command_result.original_target or ""
        if action == "browser_task_run":
            available_tasks = ", ".join(sorted(BROWSER_TASKS))
            return f"Browser task не в whitelist, сер: {target}. Доступні: {available_tasks}."
        if action.startswith("browser_watch_"):
            return f"Не знаю такого профілю спостереження, сер: {target}. Профілі беруться тільки з config files."
        available = ", ".join(sorted(APP_WHITELIST))
        return (
            f"Ціль не в whitelist, сер: {target}. Доступні: {available}. "
            f"Якщо хочеш додати {target.upper()}, задай {target.upper()}_COMMAND у локальному .env і додай target у whitelist."
        )

    if status == "ambiguous":
        return "Не до кінця зрозумів дію, сер. Нічого не виконував."

    if status == "already_running" and action.startswith("browser_watch_"):
        details = _parse_details(command_result.details)
        watch_id = details.get("watch_id") or command_result.normalized_target or ""
        return f"Спостереження вже працює, сер: {watch_id}."

    if status == "not_running" and action.startswith("browser_watch_"):
        details = _parse_details(command_result.details)
        watch_id = details.get("watch_id") or details.get("profile") or command_result.normalized_target or ""
        last_status = details.get("last_status") or details.get("status") or "not_running"
        last_error = details.get("last_error") or ""
        response = f"Немає активного спостереження {watch_id}, сер. Останній стан: {last_status}."
        if last_error:
            response = f"{response} Помилка: {last_error}."
        return response

    if status == "command_failed":
        if action.startswith("browser_watch_"):
            details = _parse_details(command_result.details)
            last_error = details.get("last_error") or details.get("error") or command_result.message
            return f"Browser Observer повернув помилку, сер: {last_error}."
        if action == "media_status":
            return "Не знайшов активний media player, сер."
        if action == "volume_status":
            details = _parse_details(command_result.details)
            raw = details.get("raw")
            if raw:
                return f"Не зміг нормально прочитати гучність, сер. Raw output: {raw}"
        return command_result.message

    if status == "executed":
        generic_response = _render_generic_executed(action, command_result)
        if generic_response is not None:
            return generic_response

    if status == "no_event" and action.startswith("browser_watch_"):
        return "Poll-once завершився, сер. Події не знайшов."

    return command_result.message


def _render_minecraft(action: str, result: CommandResult) -> str | None:
    if not action.startswith("minecraft_server_"):
        return None

    details = _parse_details(result.details)

    if action == "minecraft_server_start":
        if result.status == "already_running" and result.reason_code == "minecraft_server_already_running_unmanaged":
            return (
                "Minecraft server уже працює, сер. Він запущений не через Арвіса/tmux, "
                "тому я не запускав другий екземпляр."
            )
        if result.status == "already_running":
            return "Minecraft server уже працює, сер. Другий екземпляр не запускав."
        if result.status == "executed":
            return "Запустив Minecraft server, сер."

    if action == "minecraft_server_status":
        if result.status == "ambiguous" and result.reason_code == "minecraft_process_detection_ambiguous":
            return (
                "Знайшов процеси в папці Minecraft server, сер, але вони не схожі на запущений Minecraft Java server. "
                "Я не вважаю сервер запущеним."
            )
        if (
            result.status == "executed"
            and details.get("running") == "True"
            and details.get("managed_by_tmux") == "False"
            and details.get("control_available") == "False"
        ):
            return (
                "Minecraft server працює, сер, але він запущений не через Арвіса/tmux. "
                "Я можу бачити статус і читати логи, але stop/restart будуть доступні після запуску сервера через Арвіса."
            )
        if result.status == "executed" and details.get("running") == "True":
            return "Minecraft server працює, сер."
        if result.status == "executed":
            return "Minecraft server зараз не працює, сер."

    if action == "minecraft_server_stop":
        if result.status == "executed":
            return "Зупинив Minecraft server через команду stop, сер."
        if result.status == "not_running":
            return "Minecraft server уже не працює, сер."
        if result.status == "unsupported" and result.reason_code == "minecraft_server_unmanaged":
            return (
                "Сервер працює не під керуванням Арвіса/tmux, сер. "
                "Я не можу безпечно відправити йому stop. "
                "Зупиніть його вручну в поточній консолі, а потім запустіть через Арвіса."
            )

    if action == "minecraft_server_restart":
        if result.status == "executed":
            return "Перезапустив Minecraft server, сер."
        if result.status == "unsupported" and result.reason_code == "minecraft_server_unmanaged_restart":
            return "Не можу безпечно перезапустити unmanaged server, сер. Спершу зупиніть його вручну, потім запустіть через Арвіса."

    if action == "minecraft_server_logs" and result.status == "executed":
        return "Показую останні логи Minecraft server, сер."

    if action == "minecraft_server_diagnostics" and result.status == "executed":
        return "Показую діагностику процесів Minecraft server, сер."

    if action == "minecraft_server_metrics" and result.status == "executed":
        return "Показую навантаження Minecraft server, сер."

    return None


def _render_generic_executed(action: str, result: CommandResult) -> str | None:
    if action == "volume_up":
        return "Гучність збільшив, сер."
    if action == "volume_down":
        return "Гучність зменшив, сер."
    if action == "volume_mute":
        return "Звук вимкнув, сер."
    if action == "volume_unmute":
        return "Звук повернув, сер."
    if action == "volume_status":
        return _render_volume_status(result)
    if action == "volume_set":
        details = _parse_details(result.details)
        level = details.get("level_percent") or str((result.params or {}).get("level_percent", ""))
        return f"Поставив гучність на {level}%, сер."
    if action == "media_status":
        return _render_media_status(result)
    if action == "music_next":
        return "Перемкнув на наступний трек, сер."
    if action == "music_previous":
        return "Перемкнув на попередній трек, сер."
    if action == "music_pause":
        return "Поставив на паузу, сер."
    if action == "music_play":
        return "Відновив відтворення, сер."
    if action == "music_play_pause":
        return "Перемкнув play/pause, сер."
    if action in {"open_app", "launch_app"}:
        target = result.normalized_target or result.original_target or "app"
        return f"Запустив {_display_target(target)}, сер."
    if action == "browser_task_run":
        return _render_browser_task_executed(result)
    if action.startswith("browser_watch_"):
        return _render_browser_watch_executed(action, result)
    return None


def _render_dry_run(action: str, result: CommandResult) -> str:
    params = result.params or {}
    target = result.normalized_target or result.original_target or ""
    if action in {"open_app", "launch_app"}:
        return f"Dry-run, сер: я б запустив {_display_target(target)}, але реальна команда не виконувалась."
    if action == "browser_task_run":
        return f"Dry-run, сер: я б запустив browser task {_display_browser_task(target)}, але реальна дія не виконувалась."
    if action == "browser_watch_poll_once":
        return f"Dry-run, сер: я б один раз перевірив профіль спостереження {target}."
    if action == "browser_watch_start":
        return f"Dry-run, сер: я б запустив фонове спостереження {target}. {WATCH_SAFETY_SHORT}"
    if action == "browser_watch_stop":
        return f"Dry-run, сер: я б зупинив фонове спостереження {target}."
    if action == "browser_watch_status":
        return "Dry-run, сер: я б перевірив статус Browser Observer."
    if action == "browser_watch_events":
        return "Dry-run, сер: я б прочитав останні Browser Observer events."
    if action == "volume_up":
        return f"Dry-run, сер: я б збільшив гучність на {params.get('step_percent', 5)}%, але реальна команда не виконувалась."
    if action == "volume_down":
        return f"Dry-run, сер: я б зменшив гучність на {params.get('step_percent', 5)}%, але реальна команда не виконувалась."
    if action == "volume_mute":
        return "Dry-run, сер: я б вимкнув звук, але реальна команда не виконувалась."
    if action == "volume_unmute":
        return "Dry-run, сер: я б повернув звук, але реальна команда не виконувалась."
    if action == "volume_status":
        return "Dry-run, сер: я б перевірив поточну гучність, але реальна команда не виконувалась."
    if action == "volume_set":
        return f"Dry-run, сер: я б поставив гучність на {params.get('level_percent', 50)}%, але реальна команда не виконувалась."
    if action == "media_status":
        return "Dry-run, сер: я б перевірив, що зараз грає, але реальна команда не виконувалась."
    if action == "music_pause":
        return "Dry-run, сер: я б поставив на паузу, але реальна команда не виконувалась."
    if action == "music_play":
        return "Dry-run, сер: я б відновив відтворення, але реальна команда не виконувалась."
    if action == "music_next":
        return "Dry-run, сер: я б перемкнув на наступний трек, але реальна команда не виконувалась."
    if action == "music_previous":
        return "Dry-run, сер: я б перемкнув на попередній трек, але реальна команда не виконувалась."
    return f"Dry-run, сер: я б виконав дію {action}, але реальна команда не виконувалась."


def _render_media_status(result: CommandResult) -> str:
    details = _parse_details(result.details)
    status = (details.get("status") or "").strip().lower()
    artist = (details.get("artist") or "").strip()
    title = (details.get("title") or "").strip()

    if status and status != "playing":
        return "Зараз нічого не грає, сер."
    if artist and title:
        return f"Зараз грає: {artist} — {title}, сер."
    if title:
        return f"Зараз грає: {title}, сер."
    return "Плеєр знайдено, але metadata недоступна, сер."


def _render_volume_status(result: CommandResult) -> str:
    details = _parse_details(result.details)
    volume = details.get("volume_percent")
    muted = (details.get("muted") or "").lower() == "true"
    if not volume:
        raw = details.get("raw")
        if raw:
            return f"Не зміг нормально прочитати гучність, сер. Raw output: {raw}"
        return "Не зміг нормально прочитати гучність, сер."
    if muted:
        return f"Гучність зараз {volume}%, але звук вимкнений, сер."
    return f"Гучність зараз {volume}%, сер."


def _render_browser_task_executed(result: CommandResult) -> str:
    details = _parse_details(result.details)
    max_targets = _details_int(details, "max_targets", 30)
    attempted_clicks = _details_int(details, "attempted_clicks", 0)
    confirmed_hits = _details_int(details, "confirmed_hits", 0)
    elapsed_seconds = _details_float(details, "elapsed_seconds", 0.0)
    elapsed_text = details.get("elapsed_seconds") or f"{elapsed_seconds:.2f}"
    average_cycle = _average_cycle_ms(elapsed_seconds, confirmed_hits)
    final_site_result = _clean_browser_site_result(details.get("final_site_result_ms"))

    if final_site_result or confirmed_hits >= max_targets:
        response = f"Готово, сер. Підтверджено {confirmed_hits}/{max_targets} цілей за {elapsed_text} секунд."
        if average_cycle is not None:
            response = f"{response} Середній цикл: {average_cycle} ms."
        if final_site_result:
            response = f"{response} Результат сайту: {final_site_result}."
        return response

    return (
        "Завершив, сер, але не можу підтвердити "
        f"{max_targets} попадань. Спроб: {attempted_clicks}, підтверджено: {confirmed_hits}, час: {elapsed_text} секунд."
        f"{_browser_stop_reason_suffix(details)}"
    )


def _render_browser_watch_executed(action: str, result: CommandResult) -> str:
    details = _parse_details(result.details)
    if action == "browser_watch_start":
        watch_id = details.get("watch_id") or result.normalized_target or ""
        return f"Запустив фонове спостереження, сер: {watch_id}. {WATCH_SAFETY_SHORT}"
    if action == "browser_watch_stop":
        watch_id = details.get("watch_id") or result.normalized_target or ""
        return f"Зупинив фонове спостереження, сер: {watch_id}."
    if action == "browser_watch_status":
        profiles = details.get("profiles") or "(none)"
        events_count = details.get("events_count") or "0"
        active_count = details.get("active_count") or "0"
        if active_count.strip() == "0":
            return f"Browser Observer доступний, сер. Активних спостережень немає. Профілі: {profiles}. Events: {events_count}."
        return f"Browser Observer активний, сер. Профілі: {profiles}. Active watches: {active_count}. Events: {events_count}."
    if action == "browser_watch_events":
        events_count = details.get("events_count") or "0"
        last_events = details.get("last_events") or ""
        if last_events:
            return f"Показую Browser Observer events, сер. Всього: {events_count}. Останні: {last_events}."
        return "Browser Observer events поки порожні, сер."
    if action == "browser_watch_poll_once":
        event_type = details.get("event_type") or "event"
        message = details.get("message") or result.message
        return f"Poll-once завершився, сер. Знайшов подію спостереження: {event_type}. {message}."
    return result.message


def _clean_browser_site_result(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith(";"):
        return ""
    cleaned = cleaned.split(";", 1)[0].strip()
    if not cleaned or "user_text=" in cleaned:
        return ""
    return cleaned


def _browser_stop_reason_suffix(details: dict[str, str]) -> str:
    reason = _clean_browser_detail(details.get("stop_reason"))
    if not reason:
        return ""
    return f" Зупинився, сер: {reason}."


def _clean_browser_detail(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned or cleaned.startswith(";"):
        return ""
    cleaned = cleaned.split(";", 1)[0].strip()
    if "user_text=" in cleaned:
        return ""
    return cleaned


def _average_cycle_ms(elapsed_seconds: float, confirmed_hits: int) -> int | None:
    if confirmed_hits <= 0 or elapsed_seconds <= 0:
        return None
    return round(elapsed_seconds * 1000 / confirmed_hits)


def _details_int(details: dict[str, str], key: str, default: int) -> int:
    try:
        return int(float((details.get(key) or "").strip()))
    except ValueError:
        return default


def _details_float(details: dict[str, str], key: str, default: float) -> float:
    try:
        return float((details.get(key) or "").strip())
    except ValueError:
        return default


def _display_target(target: str) -> str:
    names = {
        "spotify": "Spotify",
        "steam": "Steam",
        "brave": "Brave",
        "discord": "Discord",
        "telegram": "Telegram",
        "youtube": "YouTube",
        "google": "Google",
        "github": "GitHub",
        "chatgpt": "ChatGPT",
    }
    return names.get(target, target)


def _display_browser_task(target: str) -> str:
    task = BROWSER_TASKS.get(target)
    if task is not None:
        return task.display_name
    return target


def _parse_details(details: str | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in (details or "").splitlines():
        if "=" in line and (":" not in line or line.index("=") < line.index(":")):
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        parsed[key.strip()] = value.strip()
    return parsed


def _block_type_from_error(error: str | None) -> str:
    value = (error or "").strip()
    if value.startswith("blocked_page_signal:"):
        return "page_signal"
    if value.startswith("url_outside_allowlist:") or "url_outside_allowlist" in value:
        return "url_allowlist"
    return ""


def _signal_from_error(error: str | None) -> str:
    value = (error or "").strip()
    if value.startswith("blocked_page_signal:"):
        return value.split(":", 1)[1].strip()
    return ""
