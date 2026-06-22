from __future__ import annotations

import re


NUMBER_WORDS = {
    "п'ять": 5,
    "пять": 5,
    "десять": 10,
    "двадцять": 20,
    "тридцять": 30,
    "сорок": 40,
    "п'ятдесят": 50,
    "пятдесят": 50,
}


def extract_first_number(
    text: str | None,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    value = text or ""
    match = re.search(r"\d+", value)
    if match is None:
        lowered = value.lower()
        for word, number in NUMBER_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", lowered):
                return clamp_int(number, min_value, max_value)
        return default
    return clamp_int(int(match.group(0)), min_value, max_value)


def get_int_param(
    params: dict[str, object] | None,
    key: str,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    if not params:
        return default
    value = params.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return clamp_int(value, min_value, max_value)
    if isinstance(value, str) and value.strip().isdigit():
        return clamp_int(int(value.strip()), min_value, max_value)
    return default


def clamp_int(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(value, max_value))
