"""LLM invocation wrapper for the AI layer.

This is the ONLY place in the codebase that talks to the Anthropic SDK.
Keeping the surface small makes it easy to:

* mock the LLM in tests (see ``tests/test_ai_layer.py``),
* swap providers later,
* enforce a single timeout policy and single error-handling policy.

Public surface:
    :func:`invoke_llm_json` — sends the prompt and returns parsed JSON
        (a dict) or ``None`` on any failure.

Failures (network, timeout, parse error, refusal, etc.) are NEVER
raised to the caller. The AI layer is best-effort: any failure
silently bubbles up to :func:`app.services.ai.generate_ai_fields`,
which falls back to the deterministic builder outputs.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from app.core.config import settings
from app.services.ai.prompts import FEW_SHOT_EXAMPLES, SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_messages(
    backend_context: dict[str, Any],
    complaint: str,
) -> list[dict[str, str]]:
    """Construct the message list: few-shot examples + final user turn.

    Few-shot examples are prepended so the model has a strong inductive
    bias to follow the same shape and safety constraints. They are
    illustrative — the model is not required to copy them verbatim.
    """
    messages: list[dict[str, str]] = []

    for ex in FEW_SHOT_EXAMPLES:
        messages.append({"role": "user", "content": ex["user"]})
        messages.append({"role": "assistant", "content": ex["assistant"]})

    # Final user turn. The complaint text is wrapped in plain quotes
    # and the backend context in a clearly labelled block so the model
    # can see they are DATA, not instructions. The complaint text is
    # truncated only as a safety bound; the orchestrator should pass
    # short complaints, but a hard cap protects us from giant payloads.
    safe_complaint = (complaint or "").strip()
    if len(safe_complaint) > 4000:
        safe_complaint = safe_complaint[:4000] + " …"

    user_payload = (
        "backend_context = "
        + json.dumps(backend_context, ensure_ascii=False)
        + "\ncomplaint = "
        + json.dumps(safe_complaint, ensure_ascii=False)
    )
    messages.append({"role": "user", "content": user_payload})
    return messages


def _extract_text(content: Any) -> str:
    """Pull a plain string out of a Messages API content block.

    The Anthropic SDK returns content as either a string (older
    releases) or a list of typed blocks (newer releases). We handle
    both shapes.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    parts.append(str(block["text"]))
                elif "text" in block:
                    parts.append(str(block["text"]))
            else:
                # Some SDK versions expose attributes rather than dicts.
                text = getattr(block, "text", None)
                if text is not None:
                    parts.append(str(text))
        return "".join(parts)
    return str(content or "")


def _parse_json_payload(raw: str) -> Optional[dict[str, Any]]:
    """Parse the model output as JSON, tolerating minor wrappers.

    The model is told to output raw JSON. In practice it may still
    occasionally wrap the answer in code fences or prefix it with a
    short apology. We strip the most common wrappers before parsing.
    """
    if not raw:
        return None
    text = raw.strip()

    # Strip ```json ... ``` and ``` ... ``` fences.
    if text.startswith("```"):
        # Drop the first line ("```json" or "```").
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # Find the first '{' and the matching '}' to ignore any preamble.
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        text = text[start : end + 1]

    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def invoke_llm_json(
    backend_context: dict[str, Any],
    complaint: str,
) -> Optional[dict[str, Any]]:
    """Call the LLM and return parsed JSON, or ``None`` on failure.

    This function NEVER raises. Any error is logged at WARNING and
    returned as ``None`` so the orchestrator can fall back gracefully.
    """
    if not settings.anthropic_api_key:
        logger.warning("AI layer: ANTHROPIC_API_KEY not set; skipping LLM call")
        return None

    try:
        # Imported lazily so that test environments without the SDK
        # (or without network) can still import this module.
        from anthropic import AsyncAnthropic  # type: ignore
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("AI layer: anthropic SDK unavailable: %s", exc)
        return None

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    messages = _build_messages(backend_context, complaint)
    timeout = float(settings.llm_timeout_seconds or 8.0)

    last_exc: Optional[BaseException] = None
    for attempt, model in enumerate(
        (settings.anthropic_model, settings.anthropic_fallback_model)
    ):
        try:
            response = await asyncio.wait_for(
                client.messages.create(
                    model=model,
                    max_tokens=600,
                    system=SYSTEM_PROMPT,
                    messages=messages,  # type: ignore[arg-type]
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            last_exc = exc
            logger.warning("AI layer: LLM timeout (attempt %d, model %s)", attempt + 1, model)
            continue
        except Exception as exc:  # noqa: BLE001 — best-effort wrapper
            last_exc = exc
            logger.warning(
                "AI layer: LLM call failed (attempt %d, model %s): %s",
                attempt + 1, model, exc,
            )
            continue

        text = _extract_text(getattr(response, "content", None))
        parsed = _parse_json_payload(text)
        if parsed is not None:
            return parsed
        logger.warning(
            "AI layer: LLM returned unparseable output (attempt %d, model %s): %r",
            attempt + 1, model, text[:200],
        )

    if last_exc is not None:
        logger.warning("AI layer: giving up after retries (%s)", last_exc)
    return None


__all__ = ["invoke_llm_json"]