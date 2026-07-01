from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from actions.apps import execute_app_action, normalize_target, preview_app_action
from actions.browser_agent import execute_browser_task, preview_browser_task
from actions.browser_agent import normalize_browser_task_target
from actions.media import execute_media_action, preview_media_action
from actions.minecraft_server import MINECRAFT_ACTIONS
from actions.minecraft_server import execute_minecraft_server_action
from actions.minecraft_server import normalize_minecraft_target
from actions.volume import execute_volume_action, preview_volume_action
from parameter_extraction import extract_first_number
from parameter_extraction import get_int_param
from schemas import ActionIntent


@dataclass
class CommandResult:
    executed: bool
    action: str
    status: str
    message: str
    details: str | None = None
    reason_code: str | None = None
    is_safety_block: bool = False
    original_action: str | None = None
    normalized_action: str | None = None
    original_target: str | None = None
    normalized_target: str | None = None
    original_user_text: str | None = None
    params: dict[str, object] | None = None


class CommandRouter:
    def __init__(self, dry_run: bool = True) -> None:
        self.dry_run = dry_run

    def route(self, intent: ActionIntent, user_text: str | None = None) -> CommandResult:
        action, target = normalize_action(intent.action, intent.target, user_text=user_text)
        params = normalize_params(action, getattr(intent, "params", {}), user_text)
        volume_direction_source = _volume_direction_source(intent.action, intent.target, user_text, action)
        risk = (intent.risk or "").strip().lower()
        repair_minecraft_risk = _can_repair_minecraft_risk(action, target, user_text)

        dangerous_reason = _dangerous_reason(intent.action, action, user_text)
        if dangerous_reason is not None:
            return CommandResult(
                executed=False,
                action=action,
                status="blocked_dangerous",
                message="Blocked: dangerous action. Nothing was executed.",
                details=_format_details(f"risk={intent.risk}", dangerous_reason, user_text),
                reason_code=dangerous_reason,
                is_safety_block=True,
                original_action=intent.action,
                normalized_action=action,
                original_target=intent.target,
                normalized_target=target,
                original_user_text=user_text,
                params=params,
            )

        if risk != "safe" and not repair_minecraft_risk:
            return CommandResult(
                executed=False,
                action=action,
                status="blocked_dangerous",
                message="Blocked: dangerous action. Nothing was executed.",
                details=_format_details(f"risk={intent.risk}", None, user_text),
                reason_code=f"risk_{risk or 'unknown'}",
                is_safety_block=True,
                original_action=intent.action,
                normalized_action=action,
                original_target=intent.target,
                normalized_target=target,
                original_user_text=user_text,
                params=params,
            )

        if intent.need_confirmation and not repair_minecraft_risk:
            return CommandResult(
                executed=False,
                action=action,
                status="blocked_confirmation_required",
                message="Action requires confirmation and confirmation execution is not supported yet.",
                details=_format_details("need_confirmation=true", None, user_text),
                reason_code="confirmation_required",
                is_safety_block=True,
                original_action=intent.action,
                normalized_action=action,
                original_target=intent.target,
                normalized_target=target,
                original_user_text=user_text,
                params=params,
            )

        if action in MINECRAFT_ACTIONS:
            return self._run_minecraft_action(action, target, intent, user_text, params)

        if action in MEDIA_ACTIONS:
            return self._run_or_preview(
                action,
                target,
                intent,
                user_text,
                params,
                None,
                lambda: execute_media_action(action, params),
                lambda: preview_media_action(action, params),
            )

        if action in VOLUME_ACTIONS:
            volume_note = _volume_details_note(target, volume_direction_source)
            return self._run_or_preview(
                action,
                target,
                intent,
                user_text,
                params,
                volume_note,
                lambda: execute_volume_action(action, params),
                lambda: preview_volume_action(action, params),
            )

        if action in APP_ACTIONS:
            return self._run_or_preview(
                action,
                target,
                intent,
                user_text,
                params,
                None,
                lambda: execute_app_action(action, target),
                lambda: preview_app_action(action, target),
            )

        if action in BROWSER_ACTIONS:
            return self._run_or_preview(
                action,
                target,
                intent,
                user_text,
                params,
                None,
                lambda: execute_browser_task(target),
                lambda: preview_browser_task(target),
            )

        if _normalize_action_name(intent.action) in GENERIC_VOLUME_ACTIONS:
            return CommandResult(
                executed=False,
                action=action,
                status="ambiguous",
                message="Volume direction is not clear. Nothing was executed.",
                details=_format_details(
                    None,
                    "Target looks like an audio scope, but no up/down/mute direction was found in target or user_text.",
                    user_text,
                ),
                reason_code="volume_direction_unknown",
                original_action=intent.action,
                normalized_action=action,
                original_target=intent.target,
                normalized_target=target,
                original_user_text=user_text,
                params=params,
            )

        return CommandResult(
            executed=False,
            action=action,
            status="unknown_action",
            message="Action is unknown or not whitelisted.",
            details=_format_details(None, None, user_text),
            reason_code="action_not_whitelisted",
            original_action=intent.action,
            normalized_action=action,
            original_target=intent.target,
            normalized_target=target,
            original_user_text=user_text,
            params=params,
        )

    def _run_or_preview(
        self,
        action: str,
        target: str | None,
        intent: ActionIntent,
        user_text: str | None,
        params: dict[str, object],
        details_note: str | None,
        execute: Callable[[], tuple[bool, str, str | None]],
        preview: Callable[[], tuple[bool, str, str | None]],
    ) -> CommandResult:
        executed, message, details = preview() if self.dry_run else execute()
        status, reason_code, is_safety_block = _classify_outcome(
            action=action,
            target=target,
            dry_run=self.dry_run,
            executed=executed,
            message=message,
            details=details,
        )
        return CommandResult(
            executed=executed,
            action=action,
            status=status,
            message=message,
            details=_format_details(details, details_note, user_text),
            reason_code=reason_code,
            is_safety_block=is_safety_block,
            original_action=intent.action,
            normalized_action=action,
            original_target=intent.target,
            normalized_target=target,
            original_user_text=user_text,
            params=params,
        )

    def _run_minecraft_action(
        self,
        action: str,
        target: str | None,
        intent: ActionIntent,
        user_text: str | None,
        params: dict[str, object],
    ) -> CommandResult:
        minecraft_target = normalize_minecraft_target(target)
        result = execute_minecraft_server_action(action, minecraft_target, dry_run=self.dry_run)
        return CommandResult(
            executed=result.executed,
            action=action,
            status=result.status,
            message=result.message,
            details=_format_details(result.details, None, user_text),
            reason_code=result.reason_code,
            is_safety_block=result.is_safety_block,
            original_action=intent.action,
            normalized_action=action,
            original_target=intent.target,
            normalized_target=minecraft_target,
            original_user_text=user_text,
            params=params,
        )


MEDIA_ACTIONS = {
    "music_play_pause",
    "music_next",
    "music_previous",
    "music_play",
    "music_pause",
    "play_music_by_mood",
    "media_seek_forward",
    "media_seek_backward",
    "music_repeat_track",
    "music_repeat_playlist",
    "music_repeat_off",
    "music_shuffle_on",
    "music_shuffle_off",
    "music_shuffle_toggle",
    "music_like_current",
    "media_status",
}

VOLUME_ACTIONS = {
    "volume_up",
    "volume_down",
    "volume_mute",
    "volume_unmute",
    "volume_status",
    "volume_set",
}

APP_ACTIONS = {
    "open_app",
    "launch_app",
}

BROWSER_ACTIONS = {
    "browser_task_run",
}

BROWSER_ACTION_ALIASES = {
    "browser_task_run": "browser_task_run",
    "launch_game_module": "browser_task_run",
    "run_game_module": "browser_task_run",
    "start_game_module": "browser_task_run",
    "open_game_module": "browser_task_run",
}

GENERIC_VOLUME_ACTIONS = {
    "adjust_volume",
    "change_volume",
    "set_volume",
    "volume",
}


APP_ACTION_ALIASES = {
    "launch_application": "open_app",
    "launch_app": "open_app",
    "open_application": "open_app",
    "open_app": "open_app",
    "start_application": "open_app",
    "start_app": "open_app",
    "run_application": "open_app",
    "run_app": "open_app",
}

ACTION_ALIASES = {
    **APP_ACTION_ALIASES,
    **BROWSER_ACTION_ALIASES,
    "decrease_volume": "volume_down",
    "lower_volume": "volume_down",
    "volume_decrease": "volume_down",
    "volume_lower": "volume_down",
    "increase_volume": "volume_up",
    "raise_volume": "volume_up",
    "volume_increase": "volume_up",
    "volume_higher": "volume_up",
    "volume_restore": "volume_unmute",
    "restore_audio": "volume_unmute",
    "restore_sound": "volume_unmute",
    "restore_volume": "volume_unmute",
    "unmute_audio": "volume_unmute",
    "unmute_sound": "volume_unmute",
    "unmute_volume": "volume_unmute",
    "enable_audio": "volume_unmute",
    "enable_sound": "volume_unmute",
    "turn_on_sound": "volume_unmute",
    "sound_on": "volume_unmute",
    "volume_status": "volume_status",
    "get_volume": "volume_status",
    "check_volume": "volume_status",
    "set_volume_level": "volume_set",
    "volume_set": "volume_set",
    "mute_audio": "volume_mute",
    "mute_sound": "volume_mute",
    "mute_volume": "volume_mute",
    "disable_audio": "volume_mute",
    "disable_sound": "volume_mute",
    "turn_off_sound": "volume_mute",
    "sound_off": "volume_mute",
    "toggle_mute": "volume_mute",
    "pause_browser_activity": "music_pause",
    "pause_browser": "music_pause",
    "pause_playback": "music_pause",
    "pause_media": "music_pause",
    "pause_music": "music_pause",
    "pause_track": "music_pause",
    "pause_song": "music_pause",
    "stop_playback": "music_pause",
    "stop_media": "music_pause",
    "stop_music": "music_pause",
    "resume_playback": "music_play",
    "resume_media": "music_play",
    "resume_music": "music_play",
    "continue_playback": "music_play",
    "continue_media": "music_play",
    "continue_music": "music_play",
    "unpause": "music_play",
    "unpause_media": "music_play",
    "restore_playback": "music_play",
    "restore_music": "music_play",
    "play_current": "music_play",
    "play_current_track": "music_play",
    "play_media": "music_play",
    "play_music": "music_play",
    "toggle_playback": "music_play_pause",
    "toggle_music": "music_play_pause",
    "toggle_media": "music_play_pause",
    "play_pause": "music_play_pause",
    "media_play_pause": "music_play_pause",
    "play_next_track": "music_next",
    "next_track": "music_next",
    "next_song": "music_next",
    "skip_track": "music_next",
    "skip_song": "music_next",
    "skip_next": "music_next",
    "media_next": "music_next",
    "music_next_track": "music_next",
    "play_next_song": "music_next",
    "go_next": "music_next",
    "switch_track_next": "music_next",
    "play_previous_track": "music_previous",
    "previous_track": "music_previous",
    "previous_song": "music_previous",
    "prev_track": "music_previous",
    "prev_song": "music_previous",
    "media_previous": "music_previous",
    "music_previous_track": "music_previous",
    "play_previous_song": "music_previous",
    "go_previous": "music_previous",
    "switch_track_previous": "music_previous",
    "seek_forward": "media_seek_forward",
    "skip_forward": "media_seek_forward",
    "fast_forward": "media_seek_forward",
    "seek_backward": "media_seek_backward",
    "skip_back": "media_seek_backward",
    "skip_backward": "media_seek_backward",
    "rewind": "media_seek_backward",
    "repeat_song": "music_repeat_track",
    "repeat_track": "music_repeat_track",
    "loop_track": "music_repeat_track",
    "repeat_playlist": "music_repeat_playlist",
    "loop_playlist": "music_repeat_playlist",
    "repeat_off": "music_repeat_off",
    "loop_off": "music_repeat_off",
    "shuffle_on": "music_shuffle_on",
    "shuffle_off": "music_shuffle_off",
    "shuffle": "music_shuffle_toggle",
    "shuffle_toggle": "music_shuffle_toggle",
    "toggle_shuffle": "music_shuffle_toggle",
    "like_current": "music_like_current",
    "like_current_song": "music_like_current",
    "save_current_song": "music_like_current",
    "favorite_current_song": "music_like_current",
    "media_status": "media_status",
    "now_playing": "media_status",
    "current_track_status": "media_status",
    "what_is_playing": "media_status",
    "start_minecraft_server": "minecraft_server_start",
    "stop_server": "minecraft_server_stop",
    "server_stop": "minecraft_server_stop",
    "stop_minecraft_server": "minecraft_server_stop",
    "minecraft_stop": "minecraft_server_stop",
    "shutdown_server": "minecraft_server_stop",
    "shutdown_minecraft_server": "minecraft_server_stop",
    "start_server": "minecraft_server_start",
    "server_start": "minecraft_server_start",
    "minecraft_start": "minecraft_server_start",
    "launch_server": "minecraft_server_start",
    "launch_minecraft_server": "minecraft_server_start",
    "restart_server": "minecraft_server_restart",
    "server_restart": "minecraft_server_restart",
    "restart_minecraft_server": "minecraft_server_restart",
    "minecraft_restart": "minecraft_server_restart",
    "reboot_server": "minecraft_server_restart",
    "server_status": "minecraft_server_status",
    "check_server": "minecraft_server_status",
    "check_minecraft_server": "minecraft_server_status",
    "minecraft_status": "minecraft_server_status",
    "get_server_status": "minecraft_server_status",
}

ADJUST_VOLUME_TARGET_ALIASES = {
    "lower": "volume_down",
    "down": "volume_down",
    "quieter": "volume_down",
    "тихіше": "volume_down",
    "нижче": "volume_down",
    "up": "volume_up",
    "higher": "volume_up",
    "louder": "volume_up",
    "голосніше": "volume_up",
    "вище": "volume_up",
    "mute": "volume_mute",
    "muted": "volume_mute",
    "silence": "volume_mute",
    "вимкни звук": "volume_mute",
}

VOLUME_DOWN_PHRASES = {
    "тихіше",
    "тише",
    "потихіше",
    "зроби тихіше",
    "зменш гучність",
    "зменши звук",
    "знизь гучність",
    "приглуши",
    "приглуши звук",
    "занадто гучно",
    "занадто голосно",
    "нижче",
    "lower",
    "quieter",
    "decrease volume",
    "volume down",
}

VOLUME_UP_PHRASES = {
    "голосніше",
    "гучніше",
    "додай гучності",
    "додай ще гучності",
    "додай звук",
    "додай ще",
    "зроби голосніше",
    "зроби гучніше",
    "збільш гучність",
    "підніми гучність",
    "прибав",
    "прибав звук",
    "слабовато",
    "слабувато",
    "замало",
    "ще гучності",
    "ще голосніше",
    "вище",
    "higher",
    "louder",
    "increase volume",
    "volume up",
}

VOLUME_MUTE_PHRASES = {
    "вимкни звук",
    "вируби звук",
    "відключи звук",
    "без звуку",
    "зам'ють",
    "замуть",
    "mute",
    "sound off",
}

VOLUME_UNMUTE_PHRASES = {
    "поверни звук",
    "увімкни звук",
    "включи звук",
    "верни звук",
    "звук назад",
    "поверни аудіо",
    "увімкни аудіо",
    "unmute",
    "sound on",
    "restore sound",
    "restore audio",
}

VOLUME_SCOPE_TARGETS = {
    "music",
    "audio",
    "sound",
    "system",
    "browser",
    "brave",
    "player",
}

SEEK_BACKWARD_TARGETS = {"backward", "back", "назад"}
SEEK_FORWARD_TARGETS = {"forward", "ahead", "вперед"}

DANGEROUS_ACTIONS = {
    "delete_all_files",
    "delete_file",
    "format_disk",
    "install_package",
    "run_shell",
    "execute_command",
    "open_url",
    "download_file",
    "run_script",
}

DANGEROUS_TEXT_PHRASES = {
    "видали",
    "знеси",
    "стерти",
    "форматни",
    "format",
    "delete",
    "remove all",
    "rm rf",
    "sudo",
    "kill",
    "pkill",
    "killall",
    "kill process",
    "bash command",
    "виконай bash",
    "запусти shell",
    "run shell",
    "execute command",
    "execute shell",
}

SERVER_KEYWORDS = {
    "server",
    "сервер",
    "майн сервер",
    "майнкрафт сервер",
    "minecraft server",
    "mc server",
}

MINECRAFT_SAFE_COMMAND_PHRASES = {
    "запусти сервер",
    "підніми сервер",
    "start server",
    "зупини сервер",
    "стопни сервер",
    "вимкни сервер",
    "stop server",
    "shutdown server",
    "зупини майн сервер",
    "перезапусти сервер",
    "рестартни сервер",
    "restart server",
    "статус сервера",
    "перевір сервер",
    "check server",
    "server status",
    "покажи логи сервера",
    "логи сервера",
    "server logs",
}


def normalize_action(
    action: str,
    target: str | None = None,
    user_text: str | None = None,
) -> tuple[str, str | None]:
    normalized_action = _normalize_action_name(action)
    normalized_target = normalize_target(target) if target is not None else None

    minecraft_action = ACTION_ALIASES.get(normalized_action)
    if minecraft_action in MINECRAFT_ACTIONS and _looks_like_minecraft_request(target, user_text, normalized_target):
        return minecraft_action, "default"

    if normalized_action in MINECRAFT_ACTIONS:
        if _looks_like_minecraft_request(target, user_text, normalized_target):
            return normalized_action, "default"
        return normalized_action, normalize_minecraft_target(target)

    user_text_action = _detect_volume_direction(user_text)
    if user_text_action is not None:
        return user_text_action, normalized_target

    if normalized_action in GENERIC_VOLUME_ACTIONS:
        target_action = _detect_volume_direction(target)
        if target_action is not None:
            return target_action, normalized_target

    if normalized_action == "seek_media":
        seek_action = _detect_seek_direction(target)
        if seek_action is not None:
            return seek_action, normalized_target

    resolved_action = ACTION_ALIASES.get(normalized_action, normalized_action)
    if resolved_action in BROWSER_ACTIONS:
        return resolved_action, normalize_browser_task_target(target)

    return resolved_action, normalized_target


def normalize_params(
    action: str,
    params: dict[str, object] | None = None,
    user_text: str | None = None,
) -> dict[str, object]:
    normalized_params = dict(params or {})
    if action in {"volume_up", "volume_down"}:
        if user_text and "step_percent" not in normalized_params:
            normalized_params["step_percent"] = extract_first_number(user_text, 5, 1, 50)
        else:
            normalized_params["step_percent"] = get_int_param(normalized_params, "step_percent", 5, 1, 50)
    elif action == "volume_set":
        if user_text and "level_percent" not in normalized_params:
            normalized_params["level_percent"] = extract_first_number(user_text, 50, 0, 100)
        else:
            normalized_params["level_percent"] = get_int_param(normalized_params, "level_percent", 50, 0, 100)
    elif action in {"media_seek_forward", "media_seek_backward"}:
        if user_text and "seconds" not in normalized_params:
            normalized_params["seconds"] = extract_first_number(user_text, 5, 1, 300)
        else:
            normalized_params["seconds"] = get_int_param(normalized_params, "seconds", 5, 1, 300)
    return normalized_params


def _normalize_action_name(action: str | None) -> str:
    return _normalize_text(action).replace(" ", "_")


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").strip().lower().replace("-", " ").replace("_", " ").split())


def _detect_volume_direction(value: str | None) -> str | None:
    normalized = _normalize_text(value)
    if not normalized:
        return None
    if normalized in ADJUST_VOLUME_TARGET_ALIASES:
        return ADJUST_VOLUME_TARGET_ALIASES[normalized]
    if _contains_phrase(normalized, VOLUME_DOWN_PHRASES):
        return "volume_down"
    if _contains_phrase(normalized, VOLUME_UP_PHRASES):
        return "volume_up"
    if _contains_phrase(normalized, VOLUME_UNMUTE_PHRASES):
        return "volume_unmute"
    if _contains_phrase(normalized, VOLUME_MUTE_PHRASES):
        return "volume_mute"
    return None


def _detect_seek_direction(value: str | None) -> str | None:
    normalized = _normalize_text(value)
    if not normalized:
        return None
    if normalized in SEEK_BACKWARD_TARGETS:
        return "media_seek_backward"
    if normalized in SEEK_FORWARD_TARGETS:
        return "media_seek_forward"
    return None


def _contains_phrase(text: str, phrases: set[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _looks_like_minecraft_request(
    target: str | None,
    user_text: str | None,
    normalized_target: str | None = None,
) -> bool:
    target_text = _normalize_text(target)
    user_text_normalized = _normalize_text(user_text)
    return (
        normalized_target == "default"
        or target_text == "default_server"
        or _contains_phrase(target_text, SERVER_KEYWORDS)
        or _contains_phrase(user_text_normalized, SERVER_KEYWORDS)
    )


def _can_repair_minecraft_risk(action: str, target: str | None, user_text: str | None) -> bool:
    if action not in MINECRAFT_ACTIONS:
        return False
    if normalize_minecraft_target(target) != "default":
        return False
    text = _normalize_text(user_text)
    if not text or _contains_phrase(text, DANGEROUS_TEXT_PHRASES):
        return False
    return _contains_phrase(text, MINECRAFT_SAFE_COMMAND_PHRASES)


def _dangerous_reason(original_action: str | None, normalized_action: str, user_text: str | None) -> str | None:
    if _normalize_action_name(original_action) in DANGEROUS_ACTIONS or normalized_action in DANGEROUS_ACTIONS:
        return "dangerous_action"
    if _contains_phrase(_normalize_text(user_text), DANGEROUS_TEXT_PHRASES):
        return "dangerous_user_text"
    return None


def _classify_outcome(
    action: str,
    target: str | None,
    dry_run: bool,
    executed: bool,
    message: str,
    details: str | None,
) -> tuple[str, str | None, bool]:
    message_text = message.lower()
    details_text = (details or "").lower()

    if action == "music_like_current":
        return "unsupported", "spotify_api_required", False

    if action == "start_minecraft_server" and "not configured" in message_text:
        return "not_configured", "minecraft_server_not_configured", False

    if action == "start_minecraft_server" and "not in the whitelist" in message_text:
        return "unknown_target", "minecraft_server_target_not_whitelisted", False

    if action in APP_ACTIONS and "not in the whitelist" in message_text:
        return "unknown_target", "app_target_not_whitelisted", False

    if action in BROWSER_ACTIONS and "not in the whitelist" in message_text:
        return "unknown_target", "browser_task_target_not_whitelisted", False

    if action in BROWSER_ACTIONS and "not configured" in message_text:
        return "not_configured", "browser_agent_not_configured", False

    if action in BROWSER_ACTIONS and "blocked" in message_text:
        return "blocked", "browser_task_blocked", False

    if "not supported" in message_text:
        return "unsupported", "action_not_implemented", False

    if executed:
        return "executed", None, False

    if dry_run:
        return "dry_run", None, False

    if "not found" in message_text or "not found" in details_text:
        return "command_failed", "command_not_found", False

    return "command_failed", "execution_failed", False


def _volume_direction_source(
    action: str,
    target: str | None,
    user_text: str | None,
    normalized_action: str,
) -> str | None:
    if normalized_action not in VOLUME_ACTIONS:
        return None
    if _detect_volume_direction(user_text) == normalized_action:
        return "user_text"
    if _normalize_action_name(action) in GENERIC_VOLUME_ACTIONS and _detect_volume_direction(target) == normalized_action:
        return "target"
    return None


def _volume_details_note(target: str | None, direction_source: str | None) -> str | None:
    notes: list[str] = []
    if direction_source == "user_text":
        notes.append("volume direction/action detected from user_text")
    elif direction_source == "target":
        notes.append("volume direction detected from target")

    if target in VOLUME_SCOPE_TARGETS:
        notes.append("per-app volume is not supported in v0.1; changing default audio sink")

    return "; ".join(notes) or None


def _format_details(
    details: str | None,
    note: str | None,
    user_text: str | None,
) -> str | None:
    parts = [part for part in (details, note) if part]
    return "; ".join(parts) or None


def should_try_intent_resolver(result: CommandResult) -> bool:
    return result.status in {"unknown_action", "unknown_target", "ambiguous"}
