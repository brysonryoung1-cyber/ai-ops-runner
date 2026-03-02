# CSR Brief — LLM Layer (Post-Implementation)

*Updated 2026-03-02 after LLM router implementation.*

---

## What We Have Now (Implemented)

- **Central LLM router** (`src/llm/llm_router.py`): Single entrypoint for ALL LLM calls. Uses "logical roles" (`core_brain`, `review_brain`, `doctor_brain`, `fast_helper`) mapped to providers/models via `config/llm.json`. Structured error taxonomy and metadata-only logging for all calls.
- **Review gate** (`src/llm/review_gate.py`): Security-focused diff review via `llm_router.generate(REVIEW_BRAIN, ...)`. Fail-closed on any failure.
- **Ask OpenClaw** (`services/test_runner/ask_engine.py`): Grounded Q&A via `llm_router.generate(CORE_BRAIN, ...)`. No hard-coded model names.
- **Provider doctor** (`src/llm/doctor.py`): Uses `llm_router.check_provider_health()` for health checks. All calls tracked via central logging.
- **Mistral key validation** (`ops/mistral_key.py`): Uses `llm_router.check_provider_health()` with configured model. Falls back to direct HTTP only if router not importable.
- **Shell review** (`ops/openclaw_codex_review.sh`): Inline Python fallback removed. Requires `review_gate` (fail-closed).
- **Config**: `config/llm.json` (providers, defaults incl. doctor, review fallback, budget, review caps).

---

## What Was Fixed

1. **[DONE] Central LLM router**: `src/llm/llm_router.py` is the single entrypoint. All call sites migrated.
2. **[DONE] Role-based routing**: `core_brain`, `review_brain`, `doctor_brain`, `fast_helper` replace hard-coded model/purpose references.
3. **[DONE] Dual review paths eliminated**: Inline Python fallback removed from `openclaw_codex_review.sh`.
4. **[DONE] Direct provider calls eliminated**: Doctor and `mistral_key.py` now use the router.
5. **[DONE] Hard-coded model removed**: `ask_engine.py` no longer passes `model="gpt-4o-mini"`.
6. **[DONE] Prompt duplication resolved**: Review system prompt only in `review_gate.py`.
7. **[DONE] Cost/logging blind spots**: All calls go through central router logging.
8. **[DONE] Structured error taxonomy**: `ConfigError`, `AuthError`, `RateLimitError`, `TransientError`.

---

## Remaining Follow-ups (Non-Blocking)

1. **Local model integration**: Enable `fast_helper` role with Ollama/local models for non-critical tasks.
2. **Latency/cost dashboards**: Extend metadata logging with latency percentiles and HQ dashboard integration.
3. **LiteLLM unification**: Route Mistral/Moonshot through LiteLLM proxy for unified routing.
4. **Single prompt source**: Consider extracting `REVIEW_SYSTEM_PROMPT` to a shared `prompts.py` module if more callers emerge.
5. **Rate limit retry**: Add configurable retry with backoff for transient errors on non-review calls.

---

## Dependencies / Gotchas

- **Env vars + secrets**: `OPENAI_API_KEY`, `MISTRAL_API_KEY`; resolution order: env → keychain → file. Never log keys.
- **Tight coupling**: HQ UI reads `llm_primary_provider`, `llm_primary_model` from config/state. The router's `get_all_status()` provides this.
- **Host-executor**: Container must have `config/llm.json` and key files.
- **Tests**: `ops/tests/test_llm_router.py` covers role mapping, provider routing, error taxonomy, health checks, and call-site integration.
- **Config validation**: `config.py` now accepts `doctor` and `fast_helper` purposes in addition to `general`, `review`, `vision`.
