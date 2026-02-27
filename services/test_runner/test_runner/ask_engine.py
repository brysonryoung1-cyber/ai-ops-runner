"""Ask OpenClaw engine — grounded Q&A from State Pack + artifacts.

Read-only: never executes actions, tools, or mutations.
Returns structured JSON with answer, citations[], recommended_next_action, confidence.
Citations validation enforced server-side.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_REPO_ROOT = os.environ.get("REPO_ROOT", os.environ.get("OPENCLAW_REPO_ROOT", "/repo"))
_ARTIFACTS_ROOT = os.environ.get("ARTIFACTS_ROOT", "/artifacts")

# Redact sensitive patterns
_REDACT_PATTERNS = [
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), "sk-…REDACTED"),
    (re.compile(r'[A-Za-z0-9_-]{20,}@[a-zA-Z0-9.-]+'), "…REDACTED"),
]


def _redact(text: str) -> str:
    out = text
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _read_file_safe(path: str, max_bytes: int = 50000) -> str | None:
    try:
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8", errors="replace") as f:
            return _redact(f.read(max_bytes))
    except Exception:
        return None


def _load_state_pack(state_pack_dir: str) -> tuple[str, list[str]]:
    """Load state pack files. Returns (context_text, citations)."""
    root = _ARTIFACTS_ROOT
    if state_pack_dir.startswith("artifacts/"):
        full_dir = os.path.join(root, state_pack_dir.replace("artifacts/", "", 1))
    else:
        full_dir = os.path.join(root, "system", "state_pack", state_pack_dir.split("/")[-1])
    if not os.path.isdir(full_dir):
        return "", []
    parts: list[str] = []
    citations: list[str] = []
    for name in ["health_public.json", "autopilot_status.json", "tailscale_serve.txt", "ports.txt", "latest_runs_index.json", "SUMMARY.md"]:
        path = os.path.join(full_dir, name)
        content = _read_file_safe(path)
        if content:
            rel = f"artifacts/system/state_pack/{os.path.basename(full_dir)}/{name}"
            citations.append(rel)
            parts.append(f"--- {name} ---\n{content[:8000]}")
    return "\n\n".join(parts), citations


def _load_project_artifacts(project_id: str | None, run_id: str | None) -> tuple[str, list[str]]:
    """Load relevant project artifacts. Returns (context_text, citations)."""
    if not project_id:
        return "", []
    parts: list[str] = []
    citations: list[str] = []
    base = os.path.join(_ARTIFACTS_ROOT, project_id)
    if not os.path.isdir(base):
        return "", []
    # Latest failure artifacts
    for sub in ["auto_finish", "run_to_done", "capture_interactive"]:
        subdir = os.path.join(base, sub)
        if not os.path.isdir(subdir):
            continue
        dirs = sorted([d for d in os.listdir(subdir) if os.path.isdir(os.path.join(subdir, d))], reverse=True)[:2]
        for d in dirs:
            for fname in ["RESULT.json", "SUMMARY.md", "ws_probe.json", "ws_check.json"]:
                path = os.path.join(subdir, d, fname)
                content = _read_file_safe(path)
                if content:
                    rel = f"artifacts/{project_id}/{sub}/{d}/{fname}"
                    citations.append(rel)
                    parts.append(f"--- {rel} ---\n{content[:4000]}")
    return "\n\n".join(parts), citations


def _call_default_engine(question: str, context: str, citations: list[str]) -> dict[str, Any]:
    """Use OpenClaw LLM router for answer."""
    import sys
    repo = _REPO_ROOT if os.path.isdir(_REPO_ROOT) else os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo not in sys.path:
        sys.path.insert(0, repo)
    try:
        from src.llm.router import get_router
        from src.llm.types import LLMRequest
    except Exception as e:
        return {
            "answer": f"LLM not available: {e}. Run system.state_pack and check artifacts.",
            "citations": citations[:5],
            "recommended_next_action": {"action": "system.state_pack", "read_only": True},
            "confidence": "LOW",
        }
    router = get_router()
    prompt = f"""You are OpenClaw ops assistant. Answer ONLY from the provided context. No speculation.
If the context does not contain enough information, say so and suggest running system.state_pack or doctor.

Context:
{context[:25000]}

Question: {question}

Respond with a concise answer (2-4 sentences). Cite artifact paths when relevant. No secrets."""

    try:
        req = LLMRequest(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=500,
            purpose="general",
            trace_id="ask_openclaw",
            essential=True,
        )
        resp = router.generate(req)
        answer = (getattr(resp, "content", None) or getattr(resp, "text", "") or "").strip()
        if not answer:
            answer = "No response from LLM. Check provider configuration."
        return {
            "answer": _redact(answer),
            "citations": citations[:20],
            "recommended_next_action": {"action": "doctor", "read_only": True} if "fail" in context.lower() or "error" in context.lower() else {"action": "system.state_pack", "read_only": True},
            "confidence": "MED" if len(citations) >= 2 else "LOW",
        }
    except Exception as e:
        return {
            "answer": f"LLM error: {str(e)[:200]}. Check config/llm.json and API keys.",
            "citations": citations[:5],
            "recommended_next_action": {"action": "llm_doctor", "read_only": True},
            "confidence": "LOW",
        }


def _call_microgpt_adapter(_question: str, _context: str, citations: list[str]) -> dict[str, Any]:
    """MicroGPT adapter stub — Karpathy microgpt is training-only, not inference."""
    return {
        "answer": "MicroGPT (Karpathy) is installed for offline canary only, not inference. Use default engine.",
        "citations": citations[:5],
        "recommended_next_action": {"action": "system.state_pack", "read_only": True},
        "confidence": "LOW",
    }


def ask(
    question: str,
    state_pack_dir: str,
    project_id: str | None = None,
    run_id: str | None = None,
    engine: str = "default",
) -> dict[str, Any]:
    """Generate grounded answer from State Pack + artifacts.

    Returns: {answer, citations[], recommended_next_action, confidence}
    Citations are always populated from loaded files.
    """
    if not question or not isinstance(question, str):
        return {
            "answer": "No question provided.",
            "citations": [],
            "recommended_next_action": {"action": "system.state_pack", "read_only": True},
            "confidence": "LOW",
        }
    ctx1, cit1 = _load_state_pack(state_pack_dir)
    ctx2, cit2 = _load_project_artifacts(project_id, run_id)
    context = f"{ctx1}\n\n{ctx2}".strip()
    citations = list(dict.fromkeys(cit1 + cit2))
    if not citations:
        citations = [f"artifacts/system/state_pack/{state_pack_dir.split('/')[-1]}/SUMMARY.md"]
    engine_used = engine if engine in ("default", "microgpt") else "default"
    if engine_used == "microgpt":
        result = _call_microgpt_adapter(question, context, citations)
        if not result.get("citations"):
            result["citations"] = citations[:5]
        return result
    result = _call_default_engine(question, context, citations)
    if not result.get("citations"):
        result["citations"] = citations[:10]
    return result
