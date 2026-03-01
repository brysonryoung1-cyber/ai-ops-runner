"""Placeholder for central LLM router API — Implementer use only.

This stub is NOT wired to any call sites. It exists as a structural template
for the Implementer when introducing a central LLM router with API-first core
and optional local helper tier.

See docs/CSR_BRIEF.md and docs/LLM_CURRENT_STATE.md.
"""

from __future__ import annotations

from typing import Any


def generate(
    purpose: str,
    messages: list[dict[str, Any]],
    *,
    trace_id: str | None = None,
    project_id: str = "openclaw",
    action: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Central entrypoint for all LLM calls.

    Resolves provider/model from purpose (review, general, doctor, etc.).
    Returns {content, model, provider, usage, ...}.
    Raises on failure (fail-closed for review).
    """
    raise NotImplementedError(
        "llm_router_stub: Implementer should replace with real router. "
        "Do not wire this stub to call sites."
    )


def resolve_provider_model(purpose: str) -> tuple[str, str]:
    """Resolve (provider_name, model_name) for a purpose.

    review -> (openai, CODEX_REVIEW_MODEL)
    general -> config defaults
    doctor -> same as general or dedicated purpose
    """
    raise NotImplementedError(
        "llm_router_stub: Implementer should replace with real router."
    )
