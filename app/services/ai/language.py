"""Lightweight language detection for the customer reply channel.

The detector is intentionally simple:

* If the caller declared a language on the ticket (``en`` / ``bn`` /
  ``mixed``) we trust it as a strong signal.
* Otherwise we count Bangla-script characters vs. Latin characters
  and pick the dominant script.
* For mixed complaints we answer in the dominant script so the reply
  feels natural to the customer.

We deliberately avoid external NLP dependencies.
"""
from __future__ import annotations

from typing import Literal, Optional

LangCode = Literal["en", "bn", "mixed"]

# Unicode block for Bangla script.
_BANGLA_RANGE = range(0x0980, 0x09FF + 1)


def _is_bangla(ch: str) -> bool:
    return ord(ch) in _BANGLA_RANGE


def _is_latin(ch: str) -> bool:
    # ASCII letters, including common Latin diacritics used in transliteration.
    return ch.isascii() and ch.isalpha()


def _script_ratio(text: str) -> tuple[int, int]:
    """Return ``(bangla_count, latin_count)`` for the input text."""
    bn = 0
    la = 0
    for ch in text or "":
        if _is_bangla(ch):
            bn += 1
        elif _is_latin(ch):
            la += 1
    return bn, la


def detect_language(
    complaint: str,
    declared: Optional[str] = None,
) -> LangCode:
    """Return the language code the customer reply should be written in.

    ``declared`` is the optional ``language`` field on the ticket
    (``"en"`` / ``"bn"`` / ``"mixed"``). When present and consistent
    with the text we honour it. When present but contradicted by the
    actual script (e.g. ``language="bn"`` but the complaint is pure
    English) the script wins.
    """
    bn, la = _script_ratio(complaint or "")
    total = bn + la

    # Edge case: no usable script in the complaint.
    if total == 0:
        if declared in ("bn", "mixed"):
            return "bn"
        return "en"

    # Script ratio: > 60% Bangla → Bangla, > 60% Latin → English.
    bangla_dominant = bn / total > 0.6
    latin_dominant = la / total > 0.6

    if bangla_dominant:
        # Even when declared=``en`` the customer's writing is Bangla,
        # so we should respond in Bangla.
        return "bn"
    if latin_dominant and declared == "bn":
        # Caller declared Bangla but wrote in Latin script (transliteration
        # such as ``"amar taka pathai nai"``). Mixed: respond in Bangla
        # script to match intent, but the user already reads Latin too.
        return "bn"
    if bangla_dominant and declared == "en":
        return "bn"
    if latin_dominant:
        return "en"
    # Mixed scripts and roughly balanced → reply in dominant script.
    return "bn" if bn >= la else "en"


__all__ = ["detect_language", "LangCode"]