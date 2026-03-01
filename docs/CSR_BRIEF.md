# CSR Brief — LLM Layer (Implementer Handoff)

*1–2 page summary for the Implementer. See `docs/LLM_CURRENT_STATE.md` for full inventory.*

---

## What We Have Now

- **Purpose-based router** (`src/llm/router.py`): `ModelRouter` selects provider/model by `purpose` (review, general, vision). Review is hard-pinned to OpenAI; Mistral is fallback on transient errors.
- **Review gate** (`src/llm/review_gate.py`): Security-focused diff review via `router.generate(purpose="review")`. Writes verdict JSON + cost telemetry. Fail-closed on any failure.
- **Ask OpenClaw** (`services/test_runner/ask_engine.py`): Grounded Q&A from State Pack + artifacts via `router.generate(purpose="general")`.
- **Provider doctor** (`src/llm/doctor.py`): Direct provider calls (OpenAI, Mistral) for health check; bypasses router.
- **Legacy path** (`ops/openclaw_codex_review.sh`): Inline Python fallback when `review_gate` not importable — duplicates prompt, no Mistral fallback, no router cost telemetry.
- **Config**: `config/llm.json` (providers, defaults, review fallback, budget); optional LiteLLM proxy (`config/litellm.yaml`).

---

## What Hurts

1. **Dual review paths**: Inline Python in `openclaw_codex_review.sh` duplicates logic and skips router benefits (fallback, telemetry).
2. **Direct provider calls**: Doctor and `mistral_key.py` bypass the router; no unified cost/logging.
3. **Prompt duplication**: Review system prompt in both `review_gate.py` and shell heredoc.
4. **Model sprawl**: `CODEX_REVIEW_MODEL` (env), `config/llm.json` defaults, and hard-coded `gpt-4o-mini` in ask_engine — multiple sources of truth.
5. **Fail-closed gaps**: Inline path has no Mistral fallback; Mistral key doctor uses `open-mistral-7b` instead of configured fallback model.
6. **Cost/latency blind spots**: Inline review and Mistral key doctor not tracked via cost_guard.

---

## Minimum Viable Path to a Sane LLM Layer

1. **Introduce a central LLM router module** as the single entrypoint for all LLM calls. The existing `ModelRouter` is close; extend it to be the *only* path (no direct provider calls from doctor, mistral_key, or shell).
2. **Identify the canonical "core brain" model**: Review = OpenAI + `CODEX_REVIEW_MODEL` (env or config). General = config `defaults.general`. No hard-coded model names in call sites.
3. **Define which tasks could use a local helper model later**: General Q&A (Ask OpenClaw) and doctor health checks are candidates for Ollama/local; review must remain OpenAI (fail-closed).
4. **Basic logging/metrics**: All calls go through router → `log_usage` in cost_tracker. No silent bypasses.
5. **Avoid silent model swaps**: Config-driven model selection; env overrides only for `OPENCLAW_REVIEW_MODEL` and `OPENCLAW_ALLOW_EXPENSIVE_REVIEW`. Log which model was used in every response.
6. **Remove inline review path**: Make `openclaw_codex_review.sh` depend on `review_gate`; fail fast if not importable.
7. **Single prompt source**: Extract `REVIEW_SYSTEM_PROMPT` to `src/llm/prompts.py` (or similar); import everywhere.
8. **Optional stub**: `src/llm/llm_router_stub.py` — placeholder with `generate()` and `resolve_provider_model()` docstrings only; no wiring. For Implementer to replace with real router API.

---

## Optional Light Touches (Diagnostic Run — Applied)

- **TODO(LLM_ROUTER) comments** added at: `src/llm/doctor.py`, `ops/mistral_key.py`, `ops/openclaw_codex_review.sh`, `services/test_runner/ask_engine.py`.
- **Stub file**: `src/llm/llm_router_stub.py` — not imported anywhere.

---

## Dependencies / Gotchas

- **Env vars + secrets**: `OPENAI_API_KEY`, `MISTRAL_API_KEY`; resolution order: env → keychain → `/etc/ai-ops-runner/secrets` (host) or `/run/openclaw_secrets` (container). Never log keys.
- **Tight coupling**: HQ UI (`/api/llm/status`, `/api/ai-status`) and `update_project_state.py` read `llm_primary_provider`, `llm_primary_model`, etc. from config/state. Changing how these are derived may affect UI.
- **Host-executor**: `test_runner` API runs in container with repo + secrets mount; `/api/llm/status` and `/ask` call into `src.llm.router`. Ensure container has `config/llm.json` and key files.
- **Tests**: `ops/tests/test_llm_router.py` mocks providers and asserts `CODEX_REVIEW_MODEL`, `review`→OpenAI, fallback behavior. Tests assume specific model names in some cases.
- **LiteLLM**: If `OPENCLAW_LITELLM_PROXY_URL` is set, `OpenAIProvider` routes through it. Mistral/Moonshot/Ollama use their own API bases; proxy is OpenAI-only unless LiteLLM config routes them.
- **Review guard**: `gpt-4o` requires `OPENCLAW_ALLOW_EXPENSIVE_REVIEW=1`; otherwise fail-closed. Enforced in router, `openclaw_doctor.sh`, `openclaw_codex_review.sh`, and `/api/llm/status`.

