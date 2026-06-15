from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from actions.apps import normalize_target
from parameter_extraction import extract_first_number
from parameter_extraction import get_int_param
from schemas import ActionIntent


ALLOWED_ACTIONS = {
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
    "volume_up",
    "volume_down",
    "volume_mute",
    "volume_unmute",
    "minecraft_server_status",
    "minecraft_server_start",
    "minecraft_server_stop",
    "minecraft_server_restart",
    "minecraft_server_logs",
    "minecraft_server_diagnostics",
    "minecraft_server_metrics",
    "open_app",
    "launch_app",
    "start_minecraft_server",
}

ALLOWED_TARGETS = {
    "system",
    "music",
    "audio",
    "sound",
    "media",
    "current_media",
    "current_track",
    "current_song",
    "active_player",
    "player",
    "browser",
    "brave",
    "youtube",
    "video",
    "spotify",
    "steam",
    "discord",
    "telegram",
    "minecraft_server",
    "default",
}

COMMAND_HINTS = {
    "арвіс",
    "зроби",
    "додай",
    "прибери",
    "прибав",
    "відкрий",
    "запусти",
    "зупини",
    "стопни",
    "перезапусти",
    "рестартни",
    "вруби",
    "постав",
    "поверни",
    "увімкни",
    "включи",
    "вимкни",
    "вируби",
    "пауза",
    "продовж",
    "віднови",
    "скип",
    "скипни",
    "пропусти",
    "перемотай",
    "мотай",
    "промотай",
    "відмотай",
    "повтор",
    "зацикли",
    "shuffle",
    "переміш",
    "лайк",
    "вподоб",
    "збережи",
    "наступ",
    "поперед",
    "тихіше",
    "гучніше",
    "голосніше",
    "слабовато",
    "слабувато",
    "забагато",
    "замало",
    "ще",
    "назад",
    "як було",
    "не те",
    "open",
    "start",
    "run",
    "play",
    "pause",
    "mute",
    "unmute",
    "volume",
    "статус",
    "логи",
    "logs",
    "ресурси",
    "навантаження",
    "cpu",
    "ram",
    "metrics",
    "performance",
}

DANGEROUS_PHRASES = {
    "видали",
    "видалити",
    "знеси",
    "стерти",
    "формат",
    "форматни",
    "format",
    "delete",
    "remove all",
    "sudo",
    "rm -rf",
    "kill",
    "pkill",
    "killall",
    "kill process",
    "install",
    "встанови",
    "скачай",
    "download",
    "run shell",
    "запусти shell",
    "bash command",
    "execute command",
    "execute shell",
    "запусти команду",
    "виконай bash",
}

SERVER_KEYWORDS = {
    "сервер",
    "майн сервер",
    "майнкрафт сервер",
    "minecraft server",
    "mc server",
}

VOLUME_UP_PHRASES = {
    "додай гучності",
    "ще гучності",
    "гучніше",
    "голосніше",
    "слабовато",
    "слабувато",
    "замало",
    "прибав",
    "підніми звук",
    "підніми гучність",
    "додай ще гучності",
    "додай звук",
    "ще голосніше",
    "louder",
    "volume up",
    "increase volume",
}

VOLUME_DOWN_PHRASES = {
    "тихіше",
    "потихіше",
    "зменш звук",
    "зменши звук",
    "зменш гучність",
    "зменши гучність",
    "приглуши",
    "занадто голосно",
    "забагато",
    "lower",
    "quieter",
    "volume down",
    "decrease volume",
}

SEEK_FORWARD_PHRASES = {
    "перемотай вперед",
    "мотай вперед",
    "промотай вперед",
    "проскочи вперед",
    "перемотай на",
    "вперед на",
    "skip forward",
    "seek forward",
}

SEEK_BACKWARD_PHRASES = {
    "перемотай назад",
    "мотай назад",
    "відмотай назад",
    "назад на",
    "skip back",
    "seek backward",
}

REPEAT_PHRASES: list[tuple[str, set[str]]] = [
    (
        "music_repeat_track",
        {"постав пісню на повтор", "повторюй цю пісню", "зацикли пісню", "repeat song", "repeat track", "loop track"},
    ),
    (
        "music_repeat_playlist",
        {"постав плейлист на повтор", "повторюй плейлист", "зацикли плейлист", "repeat playlist", "loop playlist"},
    ),
    ("music_repeat_off", {"вимкни повтор", "прибери повтор", "repeat off", "loop off"}),
]

SHUFFLE_PHRASES: list[tuple[str, set[str]]] = [
    (
        "music_shuffle_on",
        {"увімкни shuffle", "увімкни перемішування", "перемішай пісні", "shuffle on", "random mode on"},
    ),
    ("music_shuffle_off", {"вимкни shuffle", "вимкни перемішування", "shuffle off"}),
    ("music_shuffle_toggle", {"перемкни shuffle", "shuffle", "перемішування"}),
]

LIKE_PHRASES = {
    "мені подобається ця пісня",
    "додай до вподобаного",
    "додай цю пісню до вподобаного",
    "лайкни цю пісню",
    "додай у вподобане",
    "збережи цю пісню",
    "додай у лайкнуті",
    "додай в liked songs",
    "like this song",
    "save current song",
    "add to liked songs",
}

VOLUME_MUTE_PHRASES = {
    "вимкни звук",
    "вируби звук",
    "без звуку",
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
    "unmute",
    "sound on",
    "restore sound",
    "restore audio",
}

MEDIA_PHRASES: list[tuple[str, set[str]]] = [
    (
        "music_pause",
        {
            "пауза",
            "постав на паузу",
            "постав це на паузу",
            "паузу",
            "зупини",
            "зупини музику",
            "зупини відео",
            "стопни",
            "постав паузу в браузері",
            "pause",
        },
    ),
    (
        "music_play",
        {
            "продовж",
            "продовж музику",
            "продовж відео",
            "віднови",
            "зніми з паузи",
            "поверни відтворення",
            "увімкни назад",
            "play",
            "resume",
            "continue",
            "віднови відтворення",
        },
    ),
    (
        "music_next",
        {
            "наступну",
            "наступний трек",
            "наступна пісня",
            "давай наступну",
            "наступна",
            "перемкни",
            "далі",
            "давай далі",
            "скипни",
            "пропусти",
            "next",
            "skip",
            "next track",
        },
    ),
    (
        "music_previous",
        {
            "попередню",
            "попередній трек",
            "попередня пісня",
            "поверни минулу пісню",
            "включи минулу пісню",
            "верни попередню",
            "минулу",
            "назад пісню",
            "назад трек",
            "верни трек",
            "попередня",
            "previous song",
            "previous",
            "prev",
            "previous track",
        },
    ),
]

APP_PHRASES: list[tuple[str, set[str]]] = [
    ("steam", {"відкрий steam", "запусти steam", "вруби steam", "відкрий стім", "запусти стім"}),
    ("spotify", {"відкрий spotify", "запусти spotify", "вруби споті", "запусти споті"}),
    ("discord", {"відкрий discord", "запусти discord", "відкрий дискорд"}),
    ("telegram", {"відкрий telegram", "запусти telegram", "відкрий телеграм", "відкрий телегу"}),
    ("brave", {"відкрий браузер", "відкрий brave", "запусти браузер"}),
]

MINECRAFT_PHRASES: list[tuple[str, set[str]]] = [
    (
        "minecraft_server_status",
        {
            "статус майн сервера",
            "статус сервера",
            "перевір сервер майна",
            "перевір майн сервер",
            "майн сервер працює",
            "чи працює майн сервер",
            "чи запущений майнкрафт сервер",
        },
    ),
    (
        "minecraft_server_restart",
        {
            "перезапусти майн сервер",
            "рестартни майн сервер",
        },
    ),
    (
        "minecraft_server_start",
        {
            "запусти майн сервер",
            "підніми майн сервер",
            "запусти майнкрафт сервер",
            "вруби сервер майнкрафт",
        },
    ),
    (
        "minecraft_server_stop",
        {
            "зупини сервер",
            "зупини майн сервер",
            "стопни сервер",
            "стопни майн сервер",
            "вимкни сервер",
            "вимкни майнкрафт сервер",
            "shutdown server",
        },
    ),
    (
        "minecraft_server_logs",
        {
            "покажи логи майн сервера",
            "останні логи сервера",
            "server logs",
        },
    ),
    (
        "minecraft_server_diagnostics",
        {
            "діагностика майн сервера",
            "що арвіс бачить у процесах сервера",
            "покажи процеси майн сервера",
            "server diagnostics",
        },
    ),
    (
        "minecraft_server_metrics",
        {
            "скільки пам'яті хаває сервер",
            "скільки ram хаває сервер",
            "скільки ресурсів їсть сервер",
            "навантаження майн сервера",
            "cpu майн сервера",
            "ram майн сервера",
            "ресурси майн сервера",
            "minecraft server metrics",
            "server performance",
            "server resource usage",
        },
    ),
]

CONTEXT_REPEAT_PHRASES = {"ще", "ще раз", "давай ще"}
CONTEXT_REVERSE_PHRASES = {"назад", "поверни", "поверни назад", "як було"}
CONTEXT_REVERSE_KEYWORDS = {"назад", "як було", "поверни назад", "поверни як було", "не те", "забудь"}
MEDIA_PLAY_AFTER_PAUSE_PHRASES = {
    "віднови",
    "продовж",
    "зніми з паузи",
    "зроби нормально",
    "поверни як було",
    "поверни назад",
}
MEDIA_PAUSE_AFTER_PLAY_PHRASES = {
    "постав назад на паузу",
    "знову пауза",
    "назад",
}

REPEAT_ACTIONS = {"volume_up", "volume_down", "music_next", "music_previous"}
REVERSE_ACTIONS = {
    "volume_mute": "volume_unmute",
    "volume_down": "volume_up",
    "volume_up": "volume_down",
    "music_pause": "music_play",
    "music_play": "music_pause",
    "music_next": "music_previous",
}


@dataclass
class ResolvedIntent:
    action: str | None
    target: str | None
    risk: str
    need_confirmation: bool
    confidence: float
    source: str
    reason: str
    matched: str | None = None
    params: dict[str, object] = field(default_factory=dict)

    def to_action_intent(self) -> ActionIntent | None:
        if self.action is None:
            return None
        return ActionIntent(
            action=self.action,
            target=self.target or "",
            risk=self.risk,
            need_confirmation=self.need_confirmation,
            params=self.params,
        )


class IntentResolver:
    def __init__(self, llm_client: object | None = None) -> None:
        self.llm_client = llm_client

    def resolve(
        self,
        user_text: str,
        command_history: list[dict[str, object]] | None = None,
        use_llm: bool = True,
    ) -> ResolvedIntent:
        command_history = command_history or []
        heuristic = resolve_with_heuristics(user_text, command_history)
        if heuristic.confidence >= 0.65 or heuristic.risk != "safe":
            return heuristic

        if not use_llm or self.llm_client is None or not looks_like_command(user_text):
            return heuristic

        llm_result = self._resolve_with_llm(user_text, command_history)
        return llm_result or heuristic

    def _resolve_with_llm(
        self,
        user_text: str,
        command_history: list[dict[str, object]],
    ) -> ResolvedIntent | None:
        messages = [
            {
                "role": "system",
                "content": _build_llm_prompt(user_text, command_history),
            }
        ]
        chat = getattr(self.llm_client, "chat", None)
        if chat is None:
            return None

        raw_response, error = chat(messages)
        if error or not raw_response:
            return None

        payload = _extract_json_object(raw_response)
        if payload is None:
            return None

        return _resolved_from_payload(payload, source="llm_resolver")


def resolve_with_heuristics(
    user_text: str,
    command_history: list[dict[str, object]] | None = None,
) -> ResolvedIntent:
    command_history = command_history or []
    text = _normalize_text(user_text)

    if has_dangerous_text(user_text):
        return ResolvedIntent(
            action=None,
            target=None,
            risk="dangerous",
            need_confirmation=True,
            confidence=0.95,
            source="heuristic_user_text",
            reason="User text matches a dangerous action phrase.",
            matched="dangerous",
        )

    minecraft = _resolve_minecraft(text)
    if minecraft is not None:
        return minecraft

    like = _resolve_like(text)
    if like is not None:
        return like

    seek = _resolve_seek(text)
    if seek is not None:
        return seek

    repeat = _resolve_repeat(text)
    if repeat is not None:
        return repeat

    shuffle = _resolve_shuffle(text)
    if shuffle is not None:
        return shuffle

    media = _resolve_media(text)
    if media is not None:
        return media

    volume = _resolve_volume(text)
    if volume is not None:
        return volume

    app = _resolve_app(text)
    if app is not None:
        return app

    context = _resolve_context(text, command_history)
    if context is not None:
        return context

    confidence = 0.45 if looks_like_command(user_text) else 0.2
    return ResolvedIntent(
        action=None,
        target=None,
        risk="safe",
        need_confirmation=False,
        confidence=confidence,
        source="heuristic_user_text",
        reason="Intent is unclear.",
        matched="unclear",
    )


def looks_like_command(user_text: str) -> bool:
    text = _normalize_text(user_text)
    return _contains_any(text, COMMAND_HINTS)


def has_dangerous_text(user_text: str) -> bool:
    text = _normalize_text(user_text)
    return _contains_any(text, DANGEROUS_PHRASES)


def should_pass_to_router(resolved: ResolvedIntent) -> bool:
    return (
        resolved.action in ALLOWED_ACTIONS
        and resolved.confidence >= 0.65
        and resolved.risk == "safe"
        and not resolved.need_confirmation
    )


def resolver_debug_warning(resolved: ResolvedIntent) -> str | None:
    if resolved.action is not None:
        return None
    reason = resolved.reason.lower()
    for action in ALLOWED_ACTIONS:
        if action in reason:
            return f"Resolver reason mentions `{action}`, but action field is empty."
    return None


def _resolve_context(
    text: str,
    command_history: list[dict[str, object]],
) -> ResolvedIntent | None:
    last_action = _last_command_action(command_history)
    if last_action is None:
        return None

    if text in CONTEXT_REPEAT_PHRASES and last_action in REPEAT_ACTIONS:
        return ResolvedIntent(
            action=last_action,
            target=_last_command_target(command_history) or _default_target_for_action(last_action),
            risk="safe",
            need_confirmation=False,
            confidence=0.86,
            source="context_repair",
            reason=f"Repeating previous command action `{last_action}`.",
            matched="context_repeat",
            params=_last_command_params(command_history),
        )

    if last_action == "music_pause" and text in MEDIA_PLAY_AFTER_PAUSE_PHRASES:
        return ResolvedIntent(
            action="music_play",
            target=_last_command_target(command_history) or "media",
            risk="safe",
            need_confirmation=False,
            confidence=0.88,
            source="context_repair",
            reason="Restoring playback after previous pause action.",
            matched="context_media_play_after_pause",
        )

    if last_action == "music_play" and text in MEDIA_PAUSE_AFTER_PLAY_PHRASES:
        return ResolvedIntent(
            action="music_pause",
            target=_last_command_target(command_history) or "media",
            risk="safe",
            need_confirmation=False,
            confidence=0.82,
            source="context_repair",
            reason="Pausing again after previous play action.",
            matched="context_media_pause_after_play",
        )

    if text in CONTEXT_REVERSE_PHRASES or _contains_any(text, CONTEXT_REVERSE_KEYWORDS):
        reverse_action = REVERSE_ACTIONS.get(last_action)
        if reverse_action is None:
            return ResolvedIntent(
                action=None,
                target=None,
                risk="safe",
                need_confirmation=False,
                confidence=0.4,
                source="context_repair",
                reason=f"Previous action `{last_action}` has no clear reverse in v0.1.",
                matched="context_reverse_unclear",
            )
        return ResolvedIntent(
            action=reverse_action,
            target=_default_target_for_action(reverse_action),
            risk="safe",
            need_confirmation=False,
            confidence=0.86,
            source="context_repair",
            reason=f"Reversing previous command action `{last_action}`.",
            matched="context_reverse",
        )

    return None


def _resolve_volume(text: str) -> ResolvedIntent | None:
    checks = [
        ("volume_unmute", VOLUME_UNMUTE_PHRASES),
        ("volume_mute", VOLUME_MUTE_PHRASES),
        ("volume_down", VOLUME_DOWN_PHRASES),
        ("volume_up", VOLUME_UP_PHRASES),
    ]
    for action, phrases in checks:
        if _contains_any(text, phrases):
            return ResolvedIntent(
                action=action,
                target="system",
                risk="safe",
                need_confirmation=False,
                confidence=0.9,
                source="heuristic_user_text",
                reason=f"User text clearly maps to `{action}`.",
                matched=action,
                params=_params_for_action(action, text),
            )
    contextual_action = _detect_contextual_volume_action(text)
    if contextual_action is not None:
        return ResolvedIntent(
            action=contextual_action,
            target="system",
            risk="safe",
            need_confirmation=False,
            confidence=0.9,
            source="heuristic_user_text",
            reason=f"User text clearly maps to `{contextual_action}` using volume context.",
            matched=contextual_action,
            params=_params_for_action(contextual_action, text),
        )
    return None


def _params_for_action(action: str, text: str) -> dict[str, object]:
    if action in {"volume_up", "volume_down"}:
        return {"step_percent": extract_first_number(text, 5, 1, 50)}
    if action in {"media_seek_forward", "media_seek_backward"}:
        return {"seconds": extract_first_number(text, 5, 1, 300)}
    return {}


def _resolve_seek(text: str) -> ResolvedIntent | None:
    if _contains_any(text, SEEK_FORWARD_PHRASES):
        return ResolvedIntent(
            action="media_seek_forward",
            target="media",
            risk="safe",
            need_confirmation=False,
            confidence=0.9,
            source="heuristic_user_text",
            reason="User text clearly maps to `media_seek_forward`.",
            matched="seek_forward",
            params={"seconds": extract_first_number(text, 5, 1, 300)},
        )
    if _contains_any(text, SEEK_BACKWARD_PHRASES) or re.search(r"\bповерни на \d+", text):
        return ResolvedIntent(
            action="media_seek_backward",
            target="media",
            risk="safe",
            need_confirmation=False,
            confidence=0.9,
            source="heuristic_user_text",
            reason="User text clearly maps to `media_seek_backward`.",
            matched="seek_backward",
            params={"seconds": extract_first_number(text, 5, 1, 300)},
        )
    return None


def _detect_contextual_volume_action(text: str) -> str | None:
    has_volume_context = any(token in text for token in ("гучн", "звук", "volume", "audio", "sound"))
    if not has_volume_context:
        return None
    if any(token in text for token in ("додай", "прибав", "збільш", "підніми", "increase", "raise")):
        return "volume_up"
    if any(token in text for token in ("прибери", "зменш", "знизь", "decrease", "lower")):
        return "volume_down"
    return None


def _resolve_repeat(text: str) -> ResolvedIntent | None:
    for action, phrases in REPEAT_PHRASES:
        if _contains_any(text, phrases):
            return ResolvedIntent(
                action=action,
                target="media",
                risk="safe",
                need_confirmation=False,
                confidence=0.88,
                source="heuristic_user_text",
                reason=f"User text clearly maps to `{action}`.",
                matched=action,
            )
    return None


def _resolve_shuffle(text: str) -> ResolvedIntent | None:
    for action, phrases in SHUFFLE_PHRASES:
        if _contains_any(text, phrases):
            return ResolvedIntent(
                action=action,
                target="media",
                risk="safe",
                need_confirmation=False,
                confidence=0.88,
                source="heuristic_user_text",
                reason=f"User text clearly maps to `{action}`.",
                matched=action,
            )
    return None


def _resolve_like(text: str) -> ResolvedIntent | None:
    if not _contains_any(text, LIKE_PHRASES):
        return None
    return ResolvedIntent(
        action="music_like_current",
        target="media",
        risk="safe",
        need_confirmation=False,
        confidence=0.88,
        source="heuristic_user_text",
        reason="User text asks to like or save the current song.",
        matched="like_current_song",
    )


def _resolve_media(text: str) -> ResolvedIntent | None:
    if _has_server_keyword(text):
        return None
    for action, phrases in MEDIA_PHRASES:
        if _contains_any(text, phrases):
            return ResolvedIntent(
                action=action,
                target="brave" if "браузер" in text else "media",
                risk="safe",
                need_confirmation=False,
                confidence=0.85,
                source="heuristic_user_text",
                reason=f"User text clearly maps to `{action}`.",
                matched=action,
            )
    return None


def _has_server_keyword(text: str) -> bool:
    return _contains_any(text, SERVER_KEYWORDS)


def _resolve_app(text: str) -> ResolvedIntent | None:
    for target, phrases in APP_PHRASES:
        if _contains_any(text, phrases):
            return ResolvedIntent(
                action="open_app",
                target=target,
                risk="safe",
                need_confirmation=False,
                confidence=0.9,
                source="heuristic_user_text",
                reason=f"User text asks to launch `{target}`.",
                matched=f"open_app:{target}",
            )
    return None


def _resolve_minecraft(text: str) -> ResolvedIntent | None:
    for action, phrases in MINECRAFT_PHRASES:
        if _contains_any(text, phrases):
            return ResolvedIntent(
                action=action,
                target="default",
                risk="safe",
                need_confirmation=False,
                confidence=0.9,
                source="heuristic_user_text",
                reason=f"User text clearly maps to `{action}` for local Minecraft server manager.",
                matched=action,
            )
    return None


def _last_command_action(command_history: list[dict[str, object]]) -> str | None:
    for entry in reversed(command_history):
        action = entry.get("normalized_action")
        if isinstance(action, str) and action:
            return action
    return None


def _last_command_target(command_history: list[dict[str, object]]) -> str | None:
    for entry in reversed(command_history):
        target = entry.get("normalized_target")
        if isinstance(target, str) and target:
            return target
    return None


def _last_command_params(command_history: list[dict[str, object]]) -> dict[str, object]:
    for entry in reversed(command_history):
        params = entry.get("params")
        if isinstance(params, dict):
            return dict(params)
    return {}


def _default_target_for_action(action: str) -> str:
    if action.startswith("volume_"):
        return "system"
    if action.startswith("music_"):
        return "media"
    if action.startswith("minecraft_server_"):
        return "default"
    return ""


def _resolved_from_payload(payload: dict[str, Any], source: str) -> ResolvedIntent | None:
    raw_action = payload.get("action")
    action = raw_action if isinstance(raw_action, str) else None
    if action is not None and action not in ALLOWED_ACTIONS:
        action = None

    raw_target = payload.get("target")
    target = normalize_target(raw_target) if isinstance(raw_target, str) else None
    if target and target not in ALLOWED_TARGETS:
        target = None

    risk = str(payload.get("risk") or "safe").lower()
    if risk != "safe":
        action = None

    need_confirmation = bool(payload.get("need_confirmation", False))
    if need_confirmation:
        action = None

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    reason = str(payload.get("reason") or "LLM resolver result.")
    params = _sanitize_params(action, payload.get("params"))
    return ResolvedIntent(
        action=action,
        target=target,
        risk=risk,
        need_confirmation=need_confirmation,
        confidence=max(0.0, min(confidence, 1.0)),
        source=source,
        reason=reason,
        matched=source if action is None else action,
        params=params,
    )


def _sanitize_params(action: str | None, raw_params: object) -> dict[str, object]:
    params = raw_params if isinstance(raw_params, dict) else {}
    if action in {"volume_up", "volume_down"}:
        return {"step_percent": get_int_param(params, "step_percent", 5, 1, 50)}
    if action in {"media_seek_forward", "media_seek_backward"}:
        return {"seconds": get_int_param(params, "seconds", 5, 1, 300)}
    return {}


def _build_llm_prompt(user_text: str, command_history: list[dict[str, object]]) -> str:
    recent_history = command_history[-5:]
    return (
        "You are an intent resolver for a local assistant. Return ONLY JSON.\n"
        "Never return raw shell commands. Only use allowed actions.\n"
        "For Minecraft server phrases, use the local Minecraft Server Manager actions and do not ask for IP/domain.\n"
        f"Allowed actions: {sorted(ALLOWED_ACTIONS)}\n"
        "Optional params: step_percent for volume_up/volume_down, seconds for media_seek_forward/media_seek_backward.\n"
        "If unclear, set action null and confidence below 0.65.\n"
        f"Recent command history: {json.dumps(recent_history, ensure_ascii=False)}\n"
        f"User text: {user_text}\n"
        "JSON fields: action, target, risk, need_confirmation, confidence, reason, params."
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").strip().lower().replace("-", " ").replace("_", " ").split())


def _contains_any(text: str, phrases: set[str]) -> bool:
    return any(phrase in text for phrase in phrases)
