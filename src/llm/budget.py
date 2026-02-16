"""Budget estimation and enforcement for LLM calls.

Provides per-call cost estimation from token counts and provider pricing.
Enforces MAX_USD_PER_REVIEW and MAX_USD_PER_RUN caps (fail-closed for gates).
Writes telemetry to run artifacts for HQ LLM panel visibility.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.llm.provider import _log

# Default budget caps (USD) — sane defaults, overridable via config
DEFAULT_MAX_USD_PER_REVIEW = 0.50
DEFAULT_MAX_USD_PER_RUN = 5.00

# Pricing per 1M tokens (USD) — updated periodically
# Source: provider pricing pages, conservative estimates
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60},
    "gpt-4o": {"input_per_1m": 2.50, "output_per_1m": 10.00},
    "labs-devstral-small-2512": {"input_per_1m": 0.10, "output_per_1m": 0.30},
    "codestral-2501": {"input_per_1m": 0.30, "output_per_1m": 0.90},
}


@dataclass
class BudgetConfig:
    """Budget caps and pricing configuration."""

    max_usd_per_review: float = DEFAULT_MAX_USD_PER_REVIEW
    max_usd_per_run: float = DEFAULT_MAX_USD_PER_RUN
    pricing: dict[str, dict[str, float]] = field(
        default_factory=lambda: dict(DEFAULT_PRICING)
    )

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "BudgetConfig":
        pricing = dict(DEFAULT_PRICING)
        if "pricing" in data:
            for model_name, price_data in data["pricing"].items():
                pricing[model_name] = {
                    "input_per_1m": price_data.get("inputPer1M", 0.0),
                    "output_per_1m": price_data.get("outputPer1M", 0.0),
                }
        return BudgetConfig(
            max_usd_per_review=data.get(
                "maxUsdPerReview", DEFAULT_MAX_USD_PER_REVIEW
            ),
            max_usd_per_run=data.get("maxUsdPerRun", DEFAULT_MAX_USD_PER_RUN),
            pricing=pricing,
        )


@dataclass
class CostEstimate:
    """Cost estimate for a single LLM call."""

    model: str
    provider: str
    estimated_input_tokens: int
    max_output_tokens: int
    estimated_cost_usd: float
    input_cost_usd: float
    output_cost_usd: float
    pricing_found: bool


def estimate_cost(
    model: str,
    prompt_text: str,
    max_output_tokens: int,
    pricing: dict[str, dict[str, float]] | None = None,
    provider: str = "openai",
) -> CostEstimate:
    """Estimate cost for an LLM call before making it.

    Uses rough token estimation (chars / 4) and max_output_tokens for output.
    This is a conservative (high) estimate — actual cost will be lower
    since output rarely hits max_output_tokens.
    """
    if pricing is None:
        pricing = DEFAULT_PRICING

    est_input_tokens = max(len(prompt_text) // 4, 1)
    model_pricing = pricing.get(model)

    if model_pricing:
        input_cost = (est_input_tokens / 1_000_000) * model_pricing["input_per_1m"]
        output_cost = (max_output_tokens / 1_000_000) * model_pricing["output_per_1m"]
        total = input_cost + output_cost
        pricing_found = True
    else:
        input_cost = 0.0
        output_cost = 0.0
        total = 0.0
        pricing_found = False

    return CostEstimate(
        model=model,
        provider=provider,
        estimated_input_tokens=est_input_tokens,
        max_output_tokens=max_output_tokens,
        estimated_cost_usd=total,
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        pricing_found=pricing_found,
    )


def check_budget(
    estimate: CostEstimate,
    cap_usd: float,
    purpose: str,
) -> tuple[bool, str]:
    """Check if estimated cost is within budget cap.

    Returns (allowed, reason). If not allowed, reason explains why.
    For gate purposes (review), exceeding the cap is fail-closed.
    """
    if not estimate.pricing_found:
        _log(
            f"Budget: no pricing for model={estimate.model}, "
            f"allowing (unknown cost) for purpose={purpose}"
        )
        return True, "no pricing data — allowed by default"

    if estimate.estimated_cost_usd <= cap_usd:
        return True, (
            f"estimated ${estimate.estimated_cost_usd:.4f} "
            f"<= cap ${cap_usd:.2f}"
        )

    return False, (
        f"estimated ${estimate.estimated_cost_usd:.4f} "
        f"exceeds cap ${cap_usd:.2f} for purpose={purpose}"
    )


def actual_cost(
    model: str,
    usage: dict[str, int],
    pricing: dict[str, dict[str, float]] | None = None,
) -> float:
    """Calculate actual cost from usage data (post-call).

    Returns 0.0 if no pricing found for the model.
    """
    if pricing is None:
        pricing = DEFAULT_PRICING

    model_pricing = pricing.get(model)
    if not model_pricing:
        return 0.0

    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    input_cost = (input_tokens / 1_000_000) * model_pricing["input_per_1m"]
    output_cost = (output_tokens / 1_000_000) * model_pricing["output_per_1m"]

    return input_cost + output_cost


def write_cost_telemetry(
    artifact_dir: str,
    model: str,
    provider: str,
    usage: dict[str, int],
    estimated_cost_usd: float,
    actual_cost_usd: float,
    purpose: str,
    trace_id: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Write per-call cost telemetry to artifacts for HQ visibility.

    Writes to <artifact_dir>/cost_telemetry.json (append-friendly JSONL).
    """
    import datetime

    entry = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "model": model,
        "provider": provider,
        "purpose": purpose,
        "usage": usage,
        "estimated_cost_usd": round(estimated_cost_usd, 6),
        "actual_cost_usd": round(actual_cost_usd, 6),
        "trace_id": trace_id,
    }
    if extra:
        entry.update(extra)

    path = Path(artifact_dir) / "cost_telemetry.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        _log(f"Budget: failed to write telemetry: {exc}")
