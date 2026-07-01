from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher


COMMAND_VERBS = {"зроби", "зробити", "зробіть", "зроб", "make", "сделай", "додай"}
QUIETER_TOKENS = {"тихіше", "тише", "хіше", "хише", "хейше"}
LOUDER_TOKENS = {"гучніше", "голосніше", "вучніше", "учніше", "голесніше"}
DANGEROUS_FILE_PATTERNS = (
    r"\b(?:видали|видели|видалі|удали)\s+файли?\b",
    r"\bdelete\s+files?\b",
)


@dataclass(frozen=True)
class VoiceTextCorrection:
    original_text: str
    corrected_text: str
    changed: bool
    reason: str = ""
    applied_corrections: list[str] = field(default_factory=list)


def correct_voice_text(text: str) -> VoiceTextCorrection:
    original = text or ""
    corrected = original
    applied: list[str] = []

    corrected = _replace_phrase(corrected, r"\bпіднимемо\s+звук\b", "підніми звук", "піднимемо звук -> підніми звук", applied)
    corrected = _replace_phrase(corrected, r"\bскип\s+на\s+музику\b", "скипни музику", "скип на музику -> скипни музику", applied)
    corrected = _replace_phrase(corrected, r"\bстіпні\s+музику\b", "скипни музику", "стіпні музику -> скипни музику", applied)
    corrected = _replace_phrase(corrected, r"\bнаступно\s+писемо\b", "наступну пісню", "наступно писемо -> наступну пісню", applied)
    corrected = _replace_phrase(corrected, r"\bповерне\s+звук\b", "поверни звук", "поверне звук -> поверни звук", applied)
    corrected = _replace_phrase(corrected, r"\bповерний\s+звук\b", "поверни звук", "поверний звук -> поверни звук", applied)
    corrected = _replace_phrase(corrected, r"\bвимкне\s+звук\b", "вимкни звук", "вимкне звук -> вимкни звук", applied)
    corrected = _replace_phrase(corrected, r"\bвідкри\b", "відкрий", "відкри -> відкрий", applied)

    tokens = _tokens(corrected)
    has_command_context = any(token in COMMAND_VERBS for token in tokens)
    if has_command_context:
        matched_quieter = _find_quieter_token(tokens)
        if matched_quieter is not None:
            corrected = _replace_command_tokens(corrected)
            corrected = _replace_token(corrected, matched_quieter, "тихіше")
            applied.append(f"{matched_quieter} -> тихіше")

        matched_louder = _find_louder_token(tokens)
        if matched_louder is not None:
            corrected = _replace_token(corrected, matched_louder, _louder_replacement(matched_louder))
            applied.append(f"{matched_louder} -> {_louder_replacement(matched_louder)}")

        if "дулучності" in tokens:
            corrected = _replace_token(corrected, "дулучності", "гучності")
            applied.append("дулучності -> гучності")

    if not applied:
        return VoiceTextCorrection(original, original, False)

    reason = "voice correction: " + "; ".join(applied)
    return VoiceTextCorrection(
        original_text=original,
        corrected_text=corrected,
        changed=corrected != original,
        reason=reason,
        applied_corrections=applied,
    )


def has_dangerous_voice_text(text: str) -> bool:
    normalized = (text or "").strip().lower()
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in DANGEROUS_FILE_PATTERNS)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-яІіЇїЄєҐґ']+", text.lower())


def _find_quieter_token(tokens: list[str]) -> str | None:
    for token in tokens:
        if token in QUIETER_TOKENS:
            return token
        if SequenceMatcher(None, token, "тихіше").ratio() >= 0.72:
            return token
    return None


def _find_louder_token(tokens: list[str]) -> str | None:
    for token in tokens:
        if token in LOUDER_TOKENS:
            return token
        if SequenceMatcher(None, token, "гучніше").ratio() >= 0.72:
            return token
        if SequenceMatcher(None, token, "голосніше").ratio() >= 0.76:
            return token
    return None


def _louder_replacement(token: str) -> str:
    if token == "голесніше":
        return "голосніше"
    return "гучніше"


def _replace_phrase(text: str, pattern: str, replacement: str, reason: str, applied: list[str]) -> str:
    corrected, count = re.subn(pattern, replacement, text, flags=re.IGNORECASE)
    if count:
        applied.append(reason)
    return corrected


def _replace_command_tokens(text: str) -> str:
    return re.sub(r"\b(зробити|зробіть|зроб|зробі)\b", "зроби", text, flags=re.IGNORECASE)


def _replace_token(text: str, token: str, replacement: str) -> str:
    return re.sub(rf"\b{re.escape(token)}\b", replacement, text, flags=re.IGNORECASE)
