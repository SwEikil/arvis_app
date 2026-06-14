from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from actions.apps import execute_app_action, normalize_target, preview_app_action
from actions.media import execute_media_action, preview_media_action
from actions.volume import execute_volume_action, preview_volume_action
from parameter_extraction import extract_first_number
from parameter_extraction import get_int_param
from schemas import ActionIntent


@dataclass
class CommandResult:
    executed: bool
    action: str
    message: str
    details: str | None = None
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

        if risk != "safe":
            return CommandResult(
                executed=False,
                action=action,
                message="Action was not executed because it requires confirmation or is not supported in v0.1.",
                details=f"risk={intent.risk}",
                original_action=intent.action,
                normalized_action=action,
                original_target=intent.target,
                normalized_target=target,
                original_user_text=user_text,
                params=params,
            )

        if intent.need_confirmation:
            return CommandResult(
                executed=False,
                action=action,
                message="Action was not executed because confirmation is required and confirmations are not supported in v0.1.",
                details="need_confirmation=true",
                original_action=intent.action,
                normalized_action=action,
                original_target=intent.target,
                normalized_target=target,
                original_user_text=user_text,
                params=params,
            )

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

        if _normalize_action_name(intent.action) in GENERIC_VOLUME_ACTIONS:
            return CommandResult(
                executed=False,
                action=action,
                message="Volume direction is not clear. Nothing was executed.",
                details=_format_details(
                    None,
                    "Target looks like an audio scope, but no up/down/mute direction was found in target or user_text.",
                    user_text,
                ),
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
            message="Action is unknown or not whitelisted. Nothing was executed.",
            details=None,
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
        return CommandResult(
            executed=executed,
            action=action,
            message=message,
            details=_format_details(details, details_note, user_text),
            original_action=intent.action,
            normalized_action=action,
            original_target=intent.target,
            normalized_target=target,
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
}

VOLUME_ACTIONS = {
    "volume_up",
    "volume_down",
    "volume_mute",
    "volume_unmute",
}

APP_ACTIONS = {
    "open_app",
    "launch_app",
    "start_minecraft_server",
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


def normalize_action(
    action: str,
    target: str | None = None,
    user_text: str | None = None,
) -> tuple[str, str | None]:
    normalized_action = _normalize_action_name(action)
    normalized_target = normalize_target(target) if target is not None else None

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

    return ACTION_ALIASES.get(normalized_action, normalized_action), normalized_target


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
    if user_text:
        parts.append(f"user_text={user_text}")
    return "; ".join(parts) or None


def should_try_intent_resolver(result: CommandResult) -> bool:
    message = result.message.lower()
    details = (result.details or "").lower()
    return (
        "unknown or not whitelisted" in message
        or "not in the whitelist" in message
        or "volume direction is not clear" in message
        or "not supported" in message and "risk=" not in details
    )
