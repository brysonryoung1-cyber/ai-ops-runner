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

from src.llm.llm_router import check_provider_health, get_router


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
    state, err_class = check_provider_health("openai", CODEX_REVIEW_MODEL)
    result["providers"]["openai"] = {"state": state, "last_error_class": err_class}

    # Mistral (review fallback) — only if configured as fallback
    config = router._config
    if config and config.review_fallback and config.review_fallback.provider == "mistral":
        state, err_class = check_provider_health("mistral", config.review_fallback.model)
        result["providers"]["mistral"] = {"state": state, "last_error_class": err_class}
    else:
        result["providers"]["mistral"] = {"state": "DOWN", "last_error_class": None}

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
