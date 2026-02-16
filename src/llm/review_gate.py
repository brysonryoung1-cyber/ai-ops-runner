#!/usr/bin/env python3
"""Review gate — submit a review bundle to OpenAI via the LLM router.

This module is callable from both Python and the shell review pipeline.
It ALWAYS uses the ModelRouter with purpose="review", which is hard-pinned
to OpenAI + CODEX_REVIEW_MODEL (primary), with Mistral Codestral as fallback
on transient errors. Both fail => fail-closed.

Review caps (max_output_tokens, temperature) are enforced by the router.
Budget cap is checked before each call (fail-closed).
Cost telemetry is written to artifacts.

CLI usage (from openclaw_codex_review.sh):
  python3 -m src.llm.review_gate <verdict_file> <bundle_file>

Exits non-zero on any failure (fail-closed).
Never logs secrets.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

# Ensure repo root is on path for imports
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.llm.router import get_router
from src.llm.types import LLMRequest
from src.llm.budget import actual_cost, write_cost_telemetry


REVIEW_SYSTEM_PROMPT = """You are a security-focused code reviewer for the ai-ops-runner repository (OpenClaw control plane).

Review the diff below and output ONLY valid JSON matching this schema:
{
  "verdict": "APPROVED" or "BLOCKED",
  "blockers": ["array of blocking issues"],
  "non_blocking": ["array of suggestions"],
  "security_checks": {
    "public_binds": "PASS or FAIL — any new listeners on 0.0.0.0/:: ?",
    "allowlist_bypass": "PASS or FAIL — any way to execute non-allowlisted commands?",
    "key_handling": "PASS or FAIL — any secrets printed/logged/in argv?",
    "guard_doctor_intact": "PASS or FAIL — guard/doctor logic disabled or weakened?",
    "lockout_risk": "PASS or FAIL — SSH changes safe if Tailscale down?"
  },
  "tests_run": "summary of what you checked"
}

BLOCK only for:
- Security regressions: public binds, allowlist bypass, secret exposure
- Guard/doctor disablement or weakening
- Lockout risk (SSH changes without Tailscale check)
- Interactive prompts in runtime paths
- Non-idempotent operations that could cause drift

If no blocking issues, verdict MUST be "APPROVED".
Be concise — max 600 tokens total."""


def run_review(bundle_path: str, verdict_path: str) -> str:
    """Submit bundle for review via the LLM router. Returns verdict value.

    Uses purpose="review" which resolves to OpenAI (primary) with Mistral
    fallback on transient errors. Router enforces review caps and budget.
    Writes structured verdict JSON + cost telemetry to verdict_path dir.
    Raises RuntimeError on any failure (fail-closed).
    """
    with open(bundle_path) as f:
        bundle = f.read()

    if not bundle.strip():
        raise RuntimeError("Review bundle is empty")

    router = get_router()
    request = LLMRequest(
        model="",  # Router overrides with CODEX_REVIEW_MODEL
        messages=[
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": bundle},
        ],
        temperature=0.0,
        purpose="review",
        trace_id=f"review_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        response_format={"type": "json_object"},
    )

    response = router.generate(request)

    # Parse and validate verdict
    try:
        verdict = json.loads(response.content)
    except json.JSONDecodeError as exc:
        # Save raw response for debugging
        with open(verdict_path + ".raw", "w") as f:
            f.write(response.content)
        raise RuntimeError(f"Failed to parse review verdict JSON: {exc}") from None

    # Validate required fields
    required = ["verdict", "blockers", "non_blocking"]
    for key in required:
        if key not in verdict:
            raise RuntimeError(f"Missing required key in verdict: {key}")

    if verdict["verdict"] not in ["APPROVED", "BLOCKED"]:
        raise RuntimeError(f"Invalid verdict value: {verdict['verdict']}")

    # Calculate actual cost
    cost_usd = actual_cost(
        model=response.model,
        usage=response.usage,
        pricing=router.budget.pricing,
    )

    # Add metadata with full provenance
    verdict["meta"] = {
        "model": response.model,
        "provider": response.provider,
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "type": "codex_diff_review",
        "routed_via": "llm_router",
        "simulated": False,
        "usage": response.usage,
        "cost_usd": round(cost_usd, 6),
    }

    # Record if this was a fallback response
    if response.provider != "openai":
        verdict["meta"]["fallback_used"] = True
        verdict["meta"]["fallback_reason"] = "primary_transient_error"

    with open(verdict_path, "w") as f:
        json.dump(verdict, f, indent=2)

    # Write cost telemetry to artifact dir
    artifact_dir = str(Path(verdict_path).parent)
    write_cost_telemetry(
        artifact_dir=artifact_dir,
        model=response.model,
        provider=response.provider,
        usage=response.usage,
        estimated_cost_usd=0.0,  # pre-call estimate is in router logs
        actual_cost_usd=cost_usd,
        purpose="review",
        trace_id=request.trace_id,
        extra={
            "fallback_used": response.provider != "openai",
            "verdict": verdict["verdict"],
        },
    )

    return verdict["verdict"]


def main() -> int:
    """CLI entrypoint for review gate."""
    if len(sys.argv) != 3:
        print(
            "Usage: python3 -m src.llm.review_gate <verdict_file> <bundle_file>",
            file=sys.stderr,
        )
        return 1

    verdict_path = sys.argv[1]
    bundle_path = sys.argv[2]

    if not os.path.isfile(bundle_path):
        print(f"ERROR: Bundle file not found: {bundle_path}", file=sys.stderr)
        return 1

    try:
        verdict_value = run_review(bundle_path, verdict_path)
        print(verdict_value)
        return 0
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: Unexpected failure: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
