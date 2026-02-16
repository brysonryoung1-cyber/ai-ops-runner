"""Provider doctor — preflight health check for OpenAI and Mistral (review gate providers).

Runs minimal safe calls to each configured provider and writes results to
artifacts/doctor/providers/<run_id>/provider_status.json. Never logs or
exposes secrets. Used by /api/llm/status and HQ panel for visibility
before gating.

CLI: python3 -m src.llm.doctor [artifact_dir]
  If artifact_dir omitted, uses artifacts/doctor/providers/<timestamp>.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

# Repo root on path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.llm.router import get_router, _classify_transient
from src.llm.types import LLMRequest

# Minimal prompt for health check (single token-ish)
DOCTOR_PROMPT = "Hi"


def _check_provider_direct(
    router, provider_name: str, model: str
) -> tuple[str, str | None]:
    """Run minimal completion for a provider (direct call). Returns (state, last_error_class).

    state: OK, DEGRADED, or DOWN.
    last_error_class: transient_quota, transient_server, transient_network, non_transient, or None.
    """
    provider = router._providers.get(provider_name)
    if not provider or not provider.is_configured():
        return "DOWN", "missing_key"
    request = LLMRequest(
        model=model,
        messages=[{"role": "user", "content": DOCTOR_PROMPT}],
        temperature=0,
        max_tokens=5,
        purpose="general",
        trace_id="doctor",
    )
    try:
        resp = provider.generate_text(request)
        if resp and getattr(resp, "content", ""):
            return "OK", None
        return "DEGRADED", None
    except RuntimeError as exc:
        cls = _classify_transient(exc)
        if cls == "non_transient":
            return "DOWN", "non_transient"
        return "DEGRADED", cls
    except Exception:
        return "DOWN", "non_transient"


def run_provider_doctor(artifact_dir: str | Path | None = None) -> dict:
    """Run health check for OpenAI and Mistral (if configured). Write artifact.

    Returns redacted status dict: { providers: { openai: { state, last_error_class }, ... }, timestamp }.
    Never includes keys or raw errors that could contain secrets.
    """
    from src.llm.openai_provider import CODEX_REVIEW_MODEL

    router = get_router()
    if artifact_dir is None:
        artifact_dir = _REPO_ROOT / "artifacts" / "doctor" / "providers" / datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    result = {"timestamp": datetime.datetime.utcnow().isoformat() + "Z", "providers": {}}

    # OpenAI (review primary)
    state, err_class = _check_provider_direct(router, "openai", CODEX_REVIEW_MODEL)
    result["providers"]["openai"] = {"state": state, "last_error_class": err_class}

    # Mistral (review fallback) — only if configured as fallback
    if router._config and router._config.review_fallback and router._config.review_fallback.provider == "mistral":
        state, err_class = _check_provider_direct(router, "mistral", router._config.review_fallback.model)
        result["providers"]["mistral"] = {"state": state, "last_error_class": err_class}
    else:
        result["providers"]["mistral"] = {"state": "DOWN", "last_error_class": None}  # not configured as fallback

    out_path = artifact_dir / "provider_status.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    return result


def main() -> int:
    if len(sys.argv) > 1:
        artifact_dir = sys.argv[1]
    else:
        artifact_dir = _REPO_ROOT / "artifacts" / "doctor" / "providers" / datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    try:
        run_provider_doctor(artifact_dir)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
