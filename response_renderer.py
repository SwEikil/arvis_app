from __future__ import annotations

from command_router import CommandResult
from intent_resolver import ResolvedIntent


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
        return f"Dry-run, сер: я б виконав дію {action}, але реальна команда не запускалась."

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
        return f"Цю дію треба спершу налаштувати, сер: {reason_code or command_result.message}."

    if status == "unknown_action":
        return "Не знаю такої дозволеної дії, сер. Нічого не виконував."

    if status == "unknown_target":
        target = command_result.normalized_target or command_result.original_target or ""
        return f"Ціль не в whitelist, сер: {target}. Нічого не виконував."

    if status == "ambiguous":
        return "Не до кінця зрозумів дію, сер. Нічого не виконував."

    if status == "command_failed":
        return command_result.message

    if status == "executed":
        generic_response = _render_generic_executed(action, command_result)
        if generic_response is not None:
            return generic_response

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
        return "Гучність збільшено, сер."
    if action == "volume_down":
        return "Гучність зменшено, сер."
    if action == "volume_mute":
        return "Звук вимкнено, сер."
    if action == "volume_unmute":
        return "Звук повернув, сер."
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
        return f"Запустив {target}, сер."
    return None


def _parse_details(details: str | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in (details or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed
