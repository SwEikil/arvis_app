from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from typing import Any

from actions.apps import normalize_target
from actions.browser_observer_log import sanitize_event_data
from actions.browser_observer_log import sanitize_text
from actions.browser_observer_log import sanitize_url
from parameter_extraction import extract_first_number
from parameter_extraction import get_int_param
from schemas import ActionIntent
from voice_text_normalizer import VoiceTextCorrection
from voice_text_normalizer import correct_voice_text
from voice_text_normalizer import has_dangerous_voice_text


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
    "media_status",
    "volume_up",
    "volume_down",
    "volume_mute",
    "volume_unmute",
    "volume_status",
    "volume_set",
    "minecraft_server_status",
    "minecraft_server_start",
    "minecraft_server_stop",
    "minecraft_server_restart",
    "minecraft_server_logs",
    "minecraft_server_diagnostics",
    "minecraft_server_metrics",
    "open_app",
    "launch_app",
    "browser_task_run",
    "browser_watch_start",
    "browser_watch_stop",
    "browser_watch_status",
    "browser_watch_events",
    "browser_watch_poll_once",
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
    "google",
    "github",
    "chatgpt",
    "video",
    "spotify",
    "steam",
    "discord",
    "telegram",
    "humanbenchmark_aim",
    "observer",
    "viewport_change_full",
    "text_appeared",
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
    "перевір",
    "покажи",
    "статус",
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
    "observe",
    "watch",
    "show",
    "stop",
    "play",
    "pause",
    "mute",
    "unmute",
    "volume",
    "гучність",
    "статус",
    "грає",
    "трек",
    "пісня",
    "playing",
    "тренування",
    "аім",
    "аіма",
    "аіму",
    "аима",
    "аиму",
    "эйм",
    "эйма",
    "эйму",
    "aim",
    "trainer",
    "benchmark",
    "логи",
    "logs",
    "ресурси",
    "навантаження",
    "cpu",
    "ram",
    "metrics",
    "performance",
    "начни",
    "останови",
    "проверь",
    "наблюдение",
    "наблюдатель",
    "события",
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
    "занадто тихо",
    "погано чути",
    "слабий звук",
    "зроби щоб було чутно",
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
    "вуха ріже",
    "тихіше трохи",
    "занадто гучно",
}

VOLUME_STATUS_PHRASES = {
    "яка гучність",
    "скільки звуку",
    "який звук",
    "на скільки звук",
    "volume",
    "what volume",
    "перевір звук",
    "перевір гучність",
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
    "мені сподобалася ця пісня додай її",
    "мені сподобався цей трек",
    "мені подобається цей трек",
    "ця пісня імба додай",
    "о це хороша пісня збережи",
    "додай її в лайкнуті",
    "like this",
    "save this song",
}

NEGATIVE_NEXT_PHRASES = {
    "мені не подобається ця пісня давай next",
    "фігня трек скипни",
    "це не те наступну",
    "не подобається пропусти",
    "skip this",
    "next one",
}

MEDIA_STATUS_PHRASES = {
    "що зараз грає",
    "яка пісня",
    "що там включено",
    "що грає",
    "що за трек",
    "який трек",
    "хто грає",
    "що в spotify",
    "що в браузері грає",
    "now playing",
    "what is playing",
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
            "pause this",
            "стоп",
            "хватить поки",
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
            "play again",
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
            "next one",
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
    (
        "youtube",
        {
            "відкрий ютуб",
            "відкрий ютюб",
            "відкрий ютьюб",
            "запусти ютуб",
            "запусти ютюб",
            "открой ютуб",
            "открой ютюб",
            "open youtube",
            "start youtube",
            "open yt",
        },
    ),
    (
        "google",
        {
            "відкрий google",
            "відкрий гугл",
            "відкрий ґугл",
            "запусти google",
            "запусти гугл",
            "открой google",
            "открой гугл",
            "open google",
            "start google",
        },
    ),
    (
        "github",
        {
            "відкрий github",
            "відкрий git hub",
            "відкрий гітхаб",
            "відкрий гитхаб",
            "запусти github",
            "открой github",
            "открой гитхаб",
            "open github",
            "open git hub",
            "start github",
        },
    ),
    (
        "chatgpt",
        {
            "відкрий chatgpt",
            "відкрий chat gpt",
            "відкрий чатгпт",
            "відкрий чат гпт",
            "відкрий чатджпт",
            "запусти chatgpt",
            "запусти чатгпт",
            "открой chatgpt",
            "открой чатгпт",
            "open chatgpt",
            "open chat gpt",
            "start chatgpt",
        },
    ),
]

BROWSER_TASK_PHRASES: list[tuple[str, set[str]]] = [
    (
        "humanbenchmark_aim",
        {
            "відкрий тренування аіма",
            "відкрий тренування аіму",
            "відкрий тренування aimу",
            "відкрий тренеровку аима",
            "запусти aim trainer",
            "відкрий humanbenchmark aim",
            "пройди aim test",
            "тренування аіма",
            "тренування аіму",
            "тренировка аима",
            "тренеровка аима",
            "тренування aim",
            "тренування aimу",
            "тест аіма",
            "тест аіму",
            "тест aim",
            "аім тренер",
            "аима тренер",
            "аиму тренер",
            "эйм тренер",
            "aim trainer",
            "aim test",
            "open aim trainer",
            "human benchmark aim",
            "humanbenchmark aim",
        },
    ),
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
    original_text: str = ""
    corrected_text: str = ""
    correction_reason: str = ""
    applied_corrections: list[str] = field(default_factory=list)

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

        return _resolved_from_payload(payload, source="llm_resolver", user_text=user_text)


def resolve_with_heuristics(
    user_text: str,
    command_history: list[dict[str, object]] | None = None,
) -> ResolvedIntent:
    command_history = command_history or []

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

    correction = correct_voice_text(user_text)
    text = _normalize_text(correction.corrected_text)

    voice_direct = _resolve_voice_corrected_direct(text, correction)
    if voice_direct is not None:
        return voice_direct

    minecraft = _resolve_minecraft(text)
    if minecraft is not None:
        return _with_voice_correction(minecraft, correction)

    browser_task = _resolve_browser_task(text)
    if browser_task is not None:
        return _with_voice_correction(browser_task, correction)

    browser_watch = _resolve_browser_watch(text, correction.corrected_text)
    if browser_watch is not None:
        return _with_voice_correction(browser_watch, correction)

    negative_next = _resolve_negative_next(text)
    if negative_next is not None:
        return _with_voice_correction(negative_next, correction)

    like = _resolve_like(text)
    if like is not None:
        return _with_voice_correction(like, correction)

    media_status = _resolve_media_status(text)
    if media_status is not None:
        return _with_voice_correction(media_status, correction)

    seek = _resolve_seek(text)
    if seek is not None:
        return _with_voice_correction(seek, correction)

    repeat = _resolve_repeat(text)
    if repeat is not None:
        return _with_voice_correction(repeat, correction)

    shuffle = _resolve_shuffle(text)
    if shuffle is not None:
        return _with_voice_correction(shuffle, correction)

    media = _resolve_media(text)
    if media is not None:
        return _with_voice_correction(media, correction)

    volume = _resolve_volume(text)
    if volume is not None:
        return _with_voice_correction(volume, correction)

    app = _resolve_app(text)
    if app is not None:
        return _with_voice_correction(app, correction)

    context = _resolve_context(text, command_history)
    if context is not None:
        return _with_voice_correction(context, correction)

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


def _with_voice_correction(resolved: ResolvedIntent, correction: VoiceTextCorrection) -> ResolvedIntent:
    if not correction.changed or resolved.action is None:
        return resolved
    resolved.source = "voice_correction"
    resolved.confidence = max(resolved.confidence, 0.85)
    resolved.original_text = correction.original_text
    resolved.corrected_text = correction.corrected_text
    resolved.correction_reason = correction.reason
    resolved.applied_corrections = list(correction.applied_corrections)
    resolved.reason = f"{correction.reason}; {resolved.reason}"
    return resolved


def _resolve_voice_corrected_direct(text: str, correction: VoiceTextCorrection) -> ResolvedIntent | None:
    if not correction.changed:
        return None

    checks = [
        ("volume_up", "system", ("гучніше", "голосніше", "гучності", "підніми звук")),
        ("volume_down", "system", ("тихіше", "потихіше", "приглуши")),
        ("music_next", "media", ("скипни", "наступну пісню")),
        ("volume_unmute", "system", ("поверни звук",)),
        ("volume_mute", "system", ("вимкни звук",)),
    ]
    for action, target, phrases in checks:
        if any(phrase in text for phrase in phrases):
            return _with_voice_correction(
                ResolvedIntent(
                    action=action,
                    target=target,
                    risk="safe",
                    need_confirmation=False,
                    confidence=0.9,
                    source="heuristic_user_text",
                    reason=f"Corrected voice text maps directly to `{action}`.",
                    matched=f"voice_correction:{action}",
                    params=_params_for_action(action, text),
                ),
                correction,
            )
    return None


def looks_like_command(user_text: str) -> bool:
    text = _normalize_text(user_text)
    return _contains_any(text, COMMAND_HINTS)


def has_dangerous_text(user_text: str) -> bool:
    text = _normalize_text(user_text)
    return _contains_any(text, DANGEROUS_PHRASES) or has_dangerous_voice_text(user_text)


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
    if _detect_volume_set_text(text):
        return ResolvedIntent(
            action="volume_set",
            target="system",
            risk="safe",
            need_confirmation=False,
            confidence=0.9,
            source="heuristic_user_text",
            reason="User text clearly asks to set a specific volume level.",
            matched="volume_set",
            params=_params_for_action("volume_set", text),
        )

    checks = [
        ("volume_status", VOLUME_STATUS_PHRASES),
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
    if action == "volume_set":
        return {"level_percent": extract_first_number(text, 50, 0, 100)}
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
    if _detect_volume_set_text(text) and has_volume_context:
        return "volume_set"
    if not has_volume_context:
        return None
    if any(token in text for token in ("яка", "скільки", "перевір", "what volume")):
        return "volume_status"
    if any(token in text for token in ("додай", "прибав", "збільш", "підніми", "increase", "raise")):
        return "volume_up"
    if any(token in text for token in ("прибери", "зменш", "знизь", "decrease", "lower")):
        return "volume_down"
    return None


def _detect_volume_set_text(text: str) -> bool:
    patterns = [
        r"\b(?:постав|встанови)\s+(?:звук|гучність|volume)\D{0,20}\d+",
        r"\bзроби\s+(?:звук|гучність|volume)\D{0,20}\d+",
        r"\b(?:звук|гучність|volume)\s+(?:на\s+|to\s+)?\d+",
        r"\bset volume to\s+\d+",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


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
    if not (_contains_any(text, LIKE_PHRASES) or _meaning_match(text, LIKE_PHRASES)):
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


def _resolve_negative_next(text: str) -> ResolvedIntent | None:
    if not (_contains_any(text, NEGATIVE_NEXT_PHRASES) or _meaning_match(text, NEGATIVE_NEXT_PHRASES)):
        return None
    return ResolvedIntent(
        action="music_next",
        target="media",
        risk="safe",
        need_confirmation=False,
        confidence=0.88,
        source="heuristic_user_text",
        reason="User text asks to skip the current song.",
        matched="negative_next",
    )


def _resolve_media_status(text: str) -> ResolvedIntent | None:
    if not (_contains_any(text, MEDIA_STATUS_PHRASES) or _meaning_match(text, MEDIA_STATUS_PHRASES)):
        return None
    return ResolvedIntent(
        action="media_status",
        target="media",
        risk="safe",
        need_confirmation=False,
        confidence=0.9,
        source="heuristic_user_text",
        reason="User text asks what is currently playing.",
        matched="media_status",
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
        if _contains_any(text, phrases) or _meaning_match(text, phrases):
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


def _resolve_browser_task(text: str) -> ResolvedIntent | None:
    for target, phrases in BROWSER_TASK_PHRASES:
        if _contains_any(text, phrases) or _meaning_match(text, phrases):
            return ResolvedIntent(
                action="browser_task_run",
                target=target,
                risk="safe",
                need_confirmation=False,
                confidence=0.9,
                source="heuristic_user_text",
                reason=f"User text asks to run browser task `{target}`.",
                matched=f"browser_task_run:{target}",
            )
    return None


def _resolve_browser_watch(text: str, raw_text: str = "") -> ResolvedIntent | None:
    if _looks_like_browser_event_request(text, raw_text):
        return ResolvedIntent(
            action="browser_watch_events",
            target="observer",
            risk="safe",
            need_confirmation=False,
            confidence=0.9,
            source="heuristic_user_text",
            reason="User text asks for Browser Observer events.",
            matched="browser_watch_events",
            params=_browser_event_params(raw_text),
        )

    if _contains_any(
        text,
        {
            "статус спостереження",
            "статус спостерігача",
            "статус наблюдения",
            "статус наблюдателя",
            "какие наблюдения сейчас активны",
            "які спостереження зараз активні",
            "покажи завершенные наблюдения",
            "покажи завершённые наблюдения",
            "покажи завершені спостереження",
            "browser watch status",
            "watch status",
            "active browser watches",
            "show completed watches",
        },
    ):
        return ResolvedIntent(
            action="browser_watch_status",
            target="observer",
            risk="safe",
            need_confirmation=False,
            confidence=0.9,
            source="heuristic_user_text",
            reason="User text asks for Browser Observer status.",
            matched="browser_watch_status",
        )

    if _contains_any(
        text,
        {
            "зупини спостереження",
            "зупини watcher",
            "останови наблюдение",
            "останови наблюдатель",
            "stop browser watch",
            "stop observation",
        },
    ):
        return ResolvedIntent(
            action="browser_watch_stop",
            target=_extract_watch_target(raw_text, "stop"),
            risk="safe",
            need_confirmation=False,
            confidence=0.9,
            source="heuristic_user_text",
            reason="User text asks to stop a Browser Observer watch.",
            matched="browser_watch_stop",
        )

    if _contains_any(
        text,
        {
            "стеж за профілем",
            "запусти спостереження",
            "начни наблюдать за браузером",
            "начни наблюдение",
            "запусти наблюдение за страницей",
            "запусти наблюдение",
            "start browser observation",
            "start browser watch",
            "watch browser page",
        },
    ):
        return ResolvedIntent(
            action="browser_watch_start",
            target=_extract_watch_target(raw_text, "start"),
            risk="safe",
            need_confirmation=False,
            confidence=0.9,
            source="heuristic_user_text",
            reason="User text asks to start a configured Browser Observer profile.",
            matched="browser_watch_start",
        )

    if _contains_any(
        text,
        {
            "перевір профіль спостереження",
            "перевір сторінку один раз",
            "проверь страницу один раз",
            "проверь профиль наблюдения",
            "poll watch profile",
            "check page once",
            "poll browser page once",
        },
    ):
        return ResolvedIntent(
            action="browser_watch_poll_once",
            target=_extract_watch_target(raw_text, "poll"),
            risk="safe",
            need_confirmation=False,
            confidence=0.9,
            source="heuristic_user_text",
            reason="User text asks to poll a configured browser observer profile once.",
            matched="browser_watch_poll_once",
        )
    return None


def _looks_like_browser_event_request(text: str, raw_text: str) -> bool:
    has_event_noun = _contains_any(
        text,
        {
            "події",
            "подій",
            "события",
            "событий",
            "events",
            "browser watch events",
        },
    )
    has_event_request = _contains_any(
        text,
        {
            "покажи",
            "последние",
            "останні",
            "show",
            "last",
        },
    )
    if has_event_noun and has_event_request:
        return True
    return bool(
        re.search(
            r"(?i)\b(?:покажи|show)\s+[A-Za-z][A-Za-z0-9_.:-]*\s+"
            r"(?:с|з|із|from)\s+(?:сайта\s+|сайту\s+|site\s+)?"
            r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}\b",
            raw_text or "",
        )
    )


def _extract_watch_target(raw_text: str, action: str) -> str:
    raw = raw_text or ""
    patterns = [
        r"(?i)\b(?:профиль|профиля|профилю|профилем|профіль|профілю|профілем|profile)\s+"
        r"([A-Za-z0-9_.:-]+)",
    ]
    if action == "stop":
        patterns.append(
            r"(?i)\b(?:останови|зупини|stop)\s+"
            r"(?:наблюдение|наблюдателя|спостереження|watcher|(?:browser\s+)?watch)\s+"
            r"([A-Za-z0-9_.:-]+)"
        )
    elif action == "start":
        patterns.append(
            r"(?i)\b(?:запусти|начни|start)\s+"
            r"(?:наблюдение|спостереження|watch)\s+([A-Za-z0-9_.:-]+)"
        )
    elif action == "poll":
        patterns.append(
            r"(?i)\b(?:перевір профіль спостереження|проверь профиль наблюдения|poll watch profile)\s+"
            r"([A-Za-z0-9_.:-]+)"
        )
    ignored = {
        "за",
        "браузером",
        "браузер",
        "browser",
        "страницей",
        "страницу",
        "сторінкою",
        "сторінку",
        "page",
        "один",
        "once",
    }
    for pattern in patterns:
        match = re.search(pattern, raw)
        if match:
            candidate = match.group(1).strip().lower()
            if candidate not in ignored:
                cleaned = _sanitize_browser_filter("profile", candidate)
                return str(cleaned or "")
    return ""


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


def _resolved_from_payload(payload: dict[str, Any], source: str, user_text: str = "") -> ResolvedIntent | None:
    raw_action = payload.get("action")
    action = raw_action if isinstance(raw_action, str) else None
    if action is not None and action not in ALLOWED_ACTIONS:
        action = None

    raw_target = payload.get("target")
    target = normalize_target(raw_target) if isinstance(raw_target, str) else None
    if target and target not in ALLOWED_TARGETS:
        target = None

    need_confirmation = bool(payload.get("need_confirmation", False))
    risk = str(payload.get("risk") or "safe").lower()
    if (
        risk in {"low", "minimal", "none"}
        and action in ALLOWED_ACTIONS
        and not need_confirmation
        and not has_dangerous_text(user_text)
    ):
        risk = "safe"
    if risk != "safe":
        action = None

    if need_confirmation:
        action = None

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    reason = sanitize_text(payload.get("reason") or "LLM resolver result.")
    raw_params = payload.get("params")
    if _has_unknown_llm_params(action, raw_params):
        action = None
        reason = "LLM resolver returned unsupported Browser Observer parameters."
        confidence = min(confidence, 0.4)
    params = _sanitize_params(action, raw_params)
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
    if action == "volume_set":
        return {"level_percent": get_int_param(params, "level_percent", 50, 0, 100)}
    if action in {"media_seek_forward", "media_seek_backward"}:
        return {"seconds": get_int_param(params, "seconds", 5, 1, 300)}
    if action == "browser_watch_events":
        return {key: _sanitize_browser_filter(key, value) for key, value in params.items()}
    return {}


def _has_unknown_llm_params(action: str | None, raw_params: object) -> bool:
    if not action or not action.startswith("browser_watch_"):
        return False
    if raw_params is None:
        return False
    if not isinstance(raw_params, dict):
        return True
    if action != "browser_watch_events":
        return bool(raw_params)
    return bool(set(raw_params) - _BROWSER_EVENT_FILTERS)


def _build_llm_prompt(user_text: str, command_history: list[dict[str, object]]) -> str:
    recent_history = command_history[-5:]
    return (
        "You are an intent resolver for a local assistant. Return ONLY JSON.\n"
        "Never return raw shell commands. Only use allowed actions.\n"
        "Use open_app only for whitelist apps/sites, never for arbitrary URLs.\n"
        "Use browser_task_run only for whitelist browser tasks such as humanbenchmark_aim; never for arbitrary URLs.\n"
        "Browser Observer is observation-only: never turn browser_watch_* into clicks, navigation, typing, or browser_task_run.\n"
        "Use browser_watch_start/stop/poll_once only with a configured profile/watch target; if it is missing, leave target empty.\n"
        "For Minecraft server phrases, use the local Minecraft Server Manager actions and do not ask for IP/domain.\n"
        f"Allowed actions: {sorted(ALLOWED_ACTIONS)}\n"
        "Optional params: step_percent for volume_up/volume_down, level_percent for volume_set, seconds for media_seek_forward/media_seek_backward. "
        "For browser_watch_events only, params may contain exactly: profile, event_types, since, until, site, url_prefix, limit, after_event_id, after_position. "
        "event_types must stay a JSON string or array, and integer/boolean types must not be coerced. "
        "ISO times without timezone must be preserved for downstream rejection, never given an invented timezone.\n"
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


_BROWSER_EVENT_FILTERS = {
    "profile",
    "event_types",
    "since",
    "until",
    "site",
    "url_prefix",
    "limit",
    "after_event_id",
    "after_position",
}
_ISO_TIME_RE = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?"


def _browser_event_params(raw_text: str) -> dict[str, object]:
    params: dict[str, object] = {}
    for match in re.finditer(r"(?<!\S)([A-Za-z_]+)=([^\s]+)", raw_text or ""):
        key = match.group(1).lower()
        value: object = match.group(2).strip()
        if key in {"limit", "after_position"} and str(value).isdigit():
            value = int(str(value))
        elif key == "event_types":
            value = [item for item in re.split(r"[,|]", str(value)) if item]
        params[key] = _sanitize_browser_filter(key, value)

    natural_patterns = {
        "profile": [
            r"(?i)\b(?:профиля|профілю|profile)\s+([A-Za-z0-9_.:-]+)",
        ],
        "site": [
            r"(?i)\b(?:с сайта|з сайту|із сайту|from site|site)\s+((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63})\b",
            r"(?i)\b[A-Za-z][A-Za-z0-9_.:-]*\s+(?:с|з|із|from)\s+((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63})\b",
        ],
        "url_prefix": [
            r"(?i)\b(?:по url|для url|from url|url prefix|url_prefix)\s+(https?://[^\s]+)",
        ],
        "after_position": [
            r"(?i)\b(?:после позиции|після позиції|after position)\s+([^\s]+)",
        ],
        "after_event_id": [
            r"(?i)\b(?:после события|після події|after event)\s+([A-Za-z0-9_.:-]+)",
            r"(?i)\b(?:события|події|events)\s+(?:после|після|after)\s+([A-Za-z0-9_.:-]+)",
        ],
        "since": [
            rf"(?i)\b(?:since|начиная с|починаючи з|с)\s+({_ISO_TIME_RE})",
        ],
        "until": [
            rf"(?i)\b(?:until|до)\s+({_ISO_TIME_RE})",
        ],
    }
    for key, patterns in natural_patterns.items():
        if key in params:
            continue
        for pattern in patterns:
            match = re.search(pattern, raw_text or "")
            if not match:
                continue
            value = match.group(1).rstrip(".,);]")
            if key == "after_position" and value.isdigit():
                params[key] = int(value)
            else:
                params[key] = _sanitize_browser_filter(key, value)
            break

    if "limit" not in params:
        limit_match = re.search(
            r"(?i)\b(?:последние|последних|останні|last)\s+([^\s]+)\s+"
            r"(?:событи[йя]|поді[йї]|events?)\b",
            raw_text or "",
        )
        if limit_match:
            value = limit_match.group(1)
            params["limit"] = int(value) if value.isdigit() else sanitize_text(value)

    if "event_types" not in params:
        type_match = re.search(
            r"(?i)\b(?:типа|типов|типу|типів|type|types)\s+"
            r"([A-Za-z][A-Za-z0-9_.:-]*(?:\s*(?:,|и|та|and)\s*[A-Za-z][A-Za-z0-9_.:-]*)*)",
            raw_text or "",
        )
        if type_match:
            values = [
                item
                for item in re.split(r"\s*(?:,|и|та|and)\s*", type_match.group(1), flags=re.IGNORECASE)
                if item
            ]
            params["event_types"] = [_sanitize_browser_filter("event_types", item) for item in values]
        else:
            direct_type = re.search(
                r"(?i)\b(?:покажи|show)\s+([A-Za-z][A-Za-z0-9_.:-]*)\s+"
                r"(?:с|з|із|from)\s+(?:сайта\s+|сайту\s+|site\s+)?"
                r"(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}\b",
                raw_text or "",
            )
            if direct_type and direct_type.group(1).casefold() not in {"events", "события", "події"}:
                params["event_types"] = [sanitize_text(direct_type.group(1))]
    return params


def _sanitize_browser_filter(key: str, value: object) -> object:
    if key == "url_prefix" and isinstance(value, str):
        return sanitize_url(value.rstrip(".,);]")) or "[redacted-url]"
    return sanitize_event_data(value, key=key)


def _normalize_text(value: str | None) -> str:
    text = (value or "").strip().lower()
    replacements = {
        "’": "'",
        "`": "'",
        "шо": "що",
        "некст": "next",
        "спотік": "spotify",
        "споті": "spotify",
        "споти": "spotify",
        "спотіфай": "spotify",
        "лайкни": "лайк",
        "liked": "лайкнуті",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"[?!.,:;()\[\]{}\"“”]+", " ", text)
    text = text.replace("-", " ").replace("_", " ")
    return " ".join(text.split())


def _contains_any(text: str, phrases: set[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _meaning_match(text: str, phrases: set[str], threshold: float = 0.84) -> bool:
    if not text:
        return False
    for phrase in phrases:
        normalized_phrase = _normalize_text(phrase)
        if normalized_phrase and SequenceMatcher(None, text, normalized_phrase).ratio() >= threshold:
            return True
    return False
