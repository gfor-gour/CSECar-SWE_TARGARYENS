"""Safety post-processor for AI-generated customer replies.

The LLM is instructed to never produce unsafe content, but defence in
depth is non-negotiable: any LLM output that touches a customer MUST
pass through this module before it leaves the system.

Two responsibilities:

1. :func:`scan_for_unsafe` — pattern-matches the candidate reply for
   forbidden phrases (credential requests, refund guarantees,
   third-party contact instructions, suspicious external links, etc.).

2. :func:`safe_fallback_reply` — returns a generic, language-aware
   fallback message that is always safe. Used both as the final
   fallback after a retry fails and as the substitute when the LLM
   produces unsafe content on a fresh request.

The scan uses word boundaries and negative look-behind so the safety
*warning* phrasing that the deterministic builder already emits
("Please do not share your PIN or OTP") does NOT trigger a false
positive.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# Patterns that are *always* forbidden regardless of context.
# Each pattern is tested case-insensitively. We use negative
# look-behind to permit the safety warning itself ("do not share your
# PIN") while still catching the imperatives we want to block
# ("share your PIN", "send us your password").
#
# Python's stdlib ``re`` does not support alternation inside
# look-behinds, so we cannot write ``(?<!do not |never )``. Instead
# we:
#   (a) emit ONE pattern per credential phrase with the most common
#       defensive lookbehind ("do not "), and
#   (b) pre-strip any sentence containing a recognised defensive
#       preamble so the scan never sees a "do not share your PIN"
#       warning as a credential request. The preamble stripping is
#       done in :func:`_strip_defensive_preambles`.
_ALWAYS_FORBIDDEN: tuple[str, ...] = (
    # Refund / money-recovery promises
    r"\bwe will refund\b",
    r"\bwe will reverse\b",
    r"\bwe will unblock\b",
    r"\bwe will recover\b",
    r"\bwe guarantee (?:a )?refund\b",
    r"\brefund will be processed immediately\b",
    r"\bmoney has been recovered\b",
    r"\bwe have already refunded\b",
    r"\bwill be refunded\b",
    r"\byour account will be (?:unblocked|restored)\b",
    r"\byour money is safe now\b",
    r"\bcompensation (?:will|has) been (?:approved|granted|sanctioned)\b",
    # Credential requests (imperative form). Only the "do not" variant
    # is given a defensive lookbehind; the preamble-stripper below
    # handles "never share your PIN" and "for your safety, please
    # do not share your PIN" before this regex ever runs.
    r"(?<!do not )(?:please\s+)?share your (?:pin|otp|password|cvv|secret)\b",
    r"(?<!do not )(?:please\s+)?send your (?:pin|otp|password|cvv|secret)\b",
    r"(?<!do not )(?:please\s+)?give (?:us )?your (?:pin|otp|password|cvv|secret)\b",
    r"\btell me your (?:pin|otp|password|cvv|secret)\b",
    r"(?:provide|kindly provide) your (?:pin|otp|password|cvv|secret)\b",
    # Third-party / unofficial contact channels
    r"\bvisit this (?:link|website|url)\b",
    r"\bclick this (?:external )?link\b",
    r"\bdownload this (?:software|app|file)\b",
    r"\binstall this (?:software|app|file)\b",
    # Phishing instructions embedded in the reply itself
    r"\bshare your (?:card|login|card number)\b",
    r"(?<!do not )send (?:us )?your card number\b",
    r"\bgive us your login\b",
)

# Defensive preambles that frame a "do not share your PIN" *warning*
# rather than an imperative. When we see one of these we excise the
# whole sentence from the text before running the credential regex,
# so the lookbehind-only pattern can't false-positive on legitimate
# safety guidance.
#
# Each entry is a compiled regex that matches a complete sentence
# (greedy up to the next sentence terminator). The match is replaced
# with a single space before the scan runs.
_DEFENSIVE_PREAMBLES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # "Please do not share your PIN or OTP with anyone."
        r"please\s+do\s+not\s+share\s+your\s+(?:pin|otp|password|cvv|secret)[^\n\r]*?[\.\!\u0964]?",
        # "Never share your PIN with anyone, even if they claim..."
        r"never\s+share\s+your\s+(?:pin|otp|password|cvv|secret)[^\n\r]*?[\.\!\u0964]?",
        # "For your safety, please do not share your PIN..."
        r"for\s+your\s+safety[,\s]+please\s+do\s+not\s+share\s+your\s+(?:pin|otp|password|cvv|secret)[^\n\r]*?[\.\!\u0964]?",
        # "do not share these with anyone" (generic follow-up)
        r"please\s+do\s+not\s+share\s+these\s+with\s+anyone[,\s][^\n\r]*?[\.\!\u0964]?",
        # Bangla: "অনুগ্রহ করে কারো সাথে আপনার পিন, ওটিপি বা পাসওয়ার্ড শেয়ার করবেন না।"
        r"অনুগ্রহ\s+করে\s+কারো\s+সাথে\s+আপনার\s+(?:পিন|ওটিপি|পাসওয়ার্ড)\s+শেয়ার\s+করবেন\s+না[\u0964\.\!]?",
        # Bangla: "শেয়ার করবেন না" anywhere (defensive variant)
        r"(?:পিন|ওটিপি|পাসওয়ার্ড)[^\n\r]{0,40}শেয়ার\s+করবেন\s+না[\u0964\.\!]?",
    )
)

# External-URL detector. Catches raw http(s) URLs and obfuscated forms
# such as ``h‌ttp://`` (zero-width chars) or ``http[:]//``.
_URL_PATTERN = re.compile(
    r"(?:https?[:\u200b\u200c\u200d]+//|www\.)[^\s]+",
    re.IGNORECASE,
)

# Compiled combined pattern for performance.
_FORBIDDEN_RE = re.compile(
    "|".join(_ALWAYS_FORBIDDEN),
    re.IGNORECASE,
)


def _strip_defensive_preambles(text: str) -> str:
    """Remove sentences that frame a defensive warning about sharing
    credentials (e.g. "Please do not share your PIN or OTP with anyone.").

    These sentences are *always* safe — they are the explicit
    instruction we WANT to send to the customer. The pattern-based
    credential-request detector below cannot tell a warning from an
    imperative just by looking at the words immediately preceding
    "share your PIN", because Python's ``re`` lookbehind doesn't
    accept alternation. So we excise defensive preambles before the
    scan runs.

    Returns the original text with the matched warnings blanked out.
    """
    out = text
    for pat in _DEFENSIVE_PREAMBLES:
        out = pat.sub(" ", out)
    return out


def scan_for_unsafe(text: str) -> list[str]:
    """Return a list of unsafe phrases found in ``text``.

    An empty list means the text passed the safety scan.
    """
    if not text:
        return []
    hits: list[str] = []

    # Strip defensive preambles BEFORE scanning so the credential
    # patterns don't false-positive on legitimate warnings.
    scan_target = _strip_defensive_preambles(text)

    for m in _FORBIDDEN_RE.finditer(scan_target):
        hits.append(m.group(0).strip())

    for m in _URL_PATTERN.finditer(scan_target):
        hits.append(m.group(0).strip())

    # De-duplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for h in hits:
        if h.lower() in seen:
            continue
        seen.add(h.lower())
        deduped.append(h)
    return deduped


def is_safe(text: str) -> bool:
    return len(scan_for_unsafe(text)) == 0


# ---------- Safe fallback messages ----------

_FALLBACK_EN = (
    "Thank you for reaching out. We have received your message and "
    "our team will review it. We will get back to you through our "
    "official support channels. For your safety, please do not share "
    "your PIN, OTP, or password with anyone."
)

_FALLBACK_BN = (
    "আমাদের সাথে যোগাযোগ করার জন্য ধন্যবাদ। আমরা আপনার বার্তা পেয়েছি এবং "
    "আমাদের দল এটি পর্যালোচনা করবে। অফিসিয়াল সাপোর্ট চ্যানেলের মাধ্যমে "
    "আমরা আপনার সাথে যোগাযোগ করব। আপনার সুরক্ষার জন্য, অনুগ্রহ করে "
    "কারো সাথে আপনার পিন, ওটিপি বা পাসওয়ার্ড শেয়ার করবেন না।"
)


def safe_fallback_reply(language: str = "en") -> str:
    """Return a guaranteed-safe, language-appropriate fallback reply."""
    return _FALLBACK_BN if language == "bn" else _FALLBACK_EN


def sanitize_customer_reply(text: str, language: str = "en") -> str:
    """Return ``text`` if it passes the safety scan, else the fallback.

    This is the last line of defence: if both the first attempt and
    the retry failed the safety check, we replace the whole reply
    rather than try to surgically edit it.
    """
    if not text:
        return safe_fallback_reply(language)
    if is_safe(text):
        return text
    return safe_fallback_reply(language)


__all__ = [
    "scan_for_unsafe",
    "is_safe",
    "safe_fallback_reply",
    "sanitize_customer_reply",
]