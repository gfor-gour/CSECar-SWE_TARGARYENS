"""AI layer for QueueStorm.

This package is *additive*. When the ``LLM_ENABLED`` feature flag is off
(default), the deterministic builders in :mod:`app.services.analyzer`
remain the single source of truth and this package is never invoked.

Public surface:
    generate_ai_fields(...) — returns a dict containing the three
        AI-owned fields (``customer_reply``, ``agent_summary``,
        ``recommended_next_action``). Falls back to deterministic
        values on any failure so the API never returns unsafe or
        incomplete data.
"""
from .orchestrator import generate_ai_fields

__all__ = ["generate_ai_fields"]