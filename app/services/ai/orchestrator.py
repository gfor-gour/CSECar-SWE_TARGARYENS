"""Orchestrator for the AI layer.

The analyzer calls :func:`generate_ai_fields` exactly once per
ticket. The orchestrator:

  1. Detects the customer's language.
  2. Calls the LLM with the backend context (sans sensitive IDs).
  3. Runs the safety post-processor over the customer reply.
  4. On an unsafe reply, retries ONCE with a stronger safety
     reminder appended to the prompt.
  5. If the retry still produces unsafe content, or any LLM call
     fails, returns the deterministic fallback values supplied by
     the caller (the analyzer computes these from its own builders
     so the API never returns empty strings).
  6. Validates the assistant JSON shape: ``customer_reply``,
     ``agent_summary``, ``recommended_next_action`` must all be
     non-empty strings.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.ai import safety
from app.services.ai.language import detect_language
from app.services.ai.llm import invoke_llm_json

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Fields the backend owns and that we are FORBIDDEN from re-deriving
# in the AI reply. The model is told these values in the backend_context
# block; the model MUST NOT contradict them.
_BACKEND_FIELDS = (
    "case_type",
    "evidence_verdict",
    "severity",
    "department",
    "human_review_required",
    "reason_codes",
    "confidence",
)


def _build_backend_context(
    case_type: str,
    evidence_verdict: str,
    severity: str,
    department: str,
    human_review_required: bool,
    language: str,
    reason_codes: Optional[list[str]] = None,
    confidence: Optional[float] = None,
) -> dict[str, Any]:
    """Build the backend_context block sent to the LLM.

    Deliberately EXCLUDES the matched transaction_id, ticket_id, and
    any counterparty information that could leak the customer's
    internal identifiers into the customer-facing reply.
    """
    ctx: dict[str, Any] = {
        "case_type": case_type,
        "evidence_verdict": evidence_verdict,
        "severity": severity,
        "department": department,
        "human_review_required": bool(human_review_required),
        "language": language,
    }
    if reason_codes:
        # Cap to a small number so the prompt stays bounded.
        ctx["reason_codes"] = list(reason_codes)[:8]
    if confidence is not None:
        ctx["confidence"] = round(float(confidence), 2)
    return ctx


def _normalise_llm_output(parsed: dict[str, Any]) -> Optional[dict[str, str]]:
    """Coerce a parsed JSON dict into the three expected string fields.

    Returns ``None`` if any of the three required keys is missing or
    not a non-empty string after stripping.
    """
    if not isinstance(parsed, dict):
        return None
    out: dict[str, str] = {}
    for key in ("customer_reply", "agent_summary", "recommended_next_action"):
        value = parsed.get(key)
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        if not stripped:
            return None
        # Hard length cap on the customer reply to keep it under the
        # 120-word guidance (≈ 800 chars). We do NOT truncate the
        # internal fields — they may legitimately be longer.
        if key == "customer_reply" and len(stripped) > 1200:
            stripped = stripped[:1200].rstrip()
        out[key] = stripped
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def generate_ai_fields(
    complaint: str,
    declared_language: Optional[str],
    *,
    case_type: str,
    evidence_verdict: str,
    severity: str,
    department: str,
    human_review_required: bool,
    reason_codes: Optional[list[str]] = None,
    confidence: Optional[float] = None,
    fallback: dict[str, str],
) -> dict[str, str]:
    """Return the three AI-owned fields, falling back when needed.

    ``fallback`` must contain all three keys with safe deterministic
    values; it is what the analyzer built itself. The function
    ALWAYS returns a dict containing all three keys — never ``None``.

    When the LLM is disabled, returns ``fallback`` verbatim.
    """
    # Short-circuit when the feature flag is off.
    from app.core.config import settings

    if not getattr(settings, "llm_enabled", False):
        return dict(fallback)

    language = detect_language(complaint or "", declared_language)
    backend_context = _build_backend_context(
        case_type=case_type,
        evidence_verdict=evidence_verdict,
        severity=severity,
        department=department,
        human_review_required=human_review_required,
        reason_codes=reason_codes,
        confidence=confidence,
        language=language,
    )

    # ---------- First attempt ----------
    parsed = await invoke_llm_json(backend_context, complaint or "")
    candidate = _normalise_llm_output(parsed) if parsed else None

    if candidate is not None:
        safe_reply = safety.sanitize_customer_reply(
            candidate["customer_reply"], language=language,
        )
        # If safety post-processor rejected the reply, fall through to retry.
        if safe_reply == candidate["customer_reply"]:
            # Also do a final check on agent summary + next action. They
            # aren't customer-facing, but they may also contain unsafe
            # content (e.g. "tell the customer to share their PIN").
            if (
                safety.is_safe(candidate["agent_summary"])
                and safety.is_safe(candidate["recommended_next_action"])
            ):
                return {
                    "customer_reply": safe_reply,
                    "agent_summary": candidate["agent_summary"],
                    "recommended_next_action": candidate["recommended_next_action"],
                }
            logger.warning(
                "AI layer: agent_summary or next_action failed safety scan; retrying"
            )
        else:
            logger.warning(
                "AI layer: customer_reply failed safety scan; retrying with stronger reminder"
            )
    else:
        logger.warning("AI layer: first LLM attempt produced no usable JSON")

    # ---------- Retry once with stronger safety reminder ----------
    safety_reminder = (
        "\n\n[SAFETY REMINDER]\n"
        "Your previous attempt contained forbidden content. You MUST:\n"
        " • Reply with the JSON object only, no prose.\n"
        " • Do NOT ask for PIN, OTP, password, CVV, card number, or any credential.\n"
        " • Do NOT promise refunds, reversals, or money recovery.\n"
        " • Do NOT include URLs, phone numbers, or external contact info.\n"
        " • customer_reply must be in the customer's language.\n"
        "If you cannot comply, reply with the single word: null."
    )
    parsed_retry = await invoke_llm_json(
        backend_context,
        (complaint or "") + safety_reminder,
    )
    retry = _normalise_llm_output(parsed_retry) if parsed_retry else None

    if retry is not None:
        safe_reply = safety.sanitize_customer_reply(
            retry["customer_reply"], language=language,
        )
        if (
            safe_reply == retry["customer_reply"]
            and safety.is_safe(retry["agent_summary"])
            and safety.is_safe(retry["recommended_next_action"])
        ):
            return {
                "customer_reply": safe_reply,
                "agent_summary": retry["agent_summary"],
                "recommended_next_action": retry["recommended_next_action"],
            }
        logger.warning("AI layer: retry still unsafe; falling back to deterministic")

    # ---------- Final fallback ----------
    return dict(fallback)


__all__ = ["generate_ai_fields"]