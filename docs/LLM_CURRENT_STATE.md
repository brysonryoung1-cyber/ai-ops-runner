# LLM Current State — OpenClaw ai-ops-runner

*Diagnostic snapshot. Last updated: 2026-03-02.*

## Overview

OpenClaw uses LLMs for two primary purposes: (1) **code diff review** (security gate, fail-closed) and (2) **grounded Q&A** (Ask OpenClaw from State Pack + artifacts). A purpose-based `ModelRouter` in `src/llm/` selects provider and model. Review is hard-pinned to OpenAI (primary) with Mistral Codestral as fallback on transient errors. General-purpose tasks use config-driven defaults. LiteLLM proxy is optional for unified routing and caching.

---

## Providers & Models

| Provider | Model(s) | Where Used | Purpose |
|----------|----------|------------|---------|
| **OpenAI** | `gpt-4o-mini` (default) | Review gate, Ask engine, Doctor | Review primary, general Q&A, health check |
| **OpenAI** | `gpt-4o` | Vision (config) | Vision tasks (not actively used in call sites) |
| **Mistral** | `labs-devstral-small-2512` | Review fallback | Fallback when OpenAI returns 429/5xx/timeout |
| **Mistral** | `codestral-2501` | Config fallback (legacy) | Alternative fallback model |
| **Moonshot** | `moonshot-v1-8k` | Config only (disabled) | General (optional) |
| **Ollama** | local models | Config only (disabled) | Local inference (optional) |

*Note: `CODEX_REVIEW_MODEL` is env-overridable via `OPENCLAW_REVIEW_MODEL`; default `gpt-4o-mini`.*

---

## Call Sites

### 1. Review Gate (Code Diff Security Review)

| File | Function/Module | Provider/Model | Purpose |
|------|----------------|----------------|---------|
| `src/llm/review_gate.py` | `run_review()` | OpenAI (primary), Mistral (fallback) | Security-focused diff review; outputs JSON verdict (APPROVED/BLOCKED) with 5 security checks |
| `ops/openclaw_codex_review.sh` | (invokes review_gate or inline Python) | OpenAI / LiteLLM | Shell entrypoint; prefers `src.llm.review_gate`, falls back to direct `chat/completions` if router unavailable |

**Flow:** Bundle → `run_review()` → `router.generate(purpose="review")` → OpenAI; on transient error → Mistral fallback. Both fail → fail-closed.

### 2. Ask OpenClaw (Grounded Q&A)

| File | Function/Module | Provider/Model | Purpose |
|------|----------------|----------------|---------|
| `services/test_runner/test_runner/ask_engine.py` | `_call_default_engine()` | Router `purpose="general"` → gpt-4o-mini | Answer questions from State Pack + project artifacts; read-only, no actions |

**Flow:** Question + context → `LLMRequest(purpose="general")` → `router.generate()` → response used for answer, citations, recommended_next_action.

### 3. Provider Doctor (Health Check)

| File | Function/Module | Provider/Model | Purpose |
|------|----------------|----------------|---------|
| `src/llm/doctor.py` | `_check_provider_direct()`, `run_provider_doctor()` | OpenAI (CODEX_REVIEW_MODEL), Mistral (fallback model) | Minimal completion ("Hi") to verify provider reachability; writes `artifacts/doctor/providers/<run_id>/provider_status.json` |

**Flow:** Direct provider calls (bypasses full router flow); used by `llm_doctor` action and `/api/llm/status` for HQ visibility.

### 4. Mistral Key Validation (Standalone)

| File | Function/Module | Provider/Model | Purpose |
|------|----------------|----------------|---------|
| `ops/mistral_key.py` | `assert_mistral_api_key_valid()` | Mistral `open-mistral-7b` | Key smoke test; direct `chat/completions` call, not via router |

**Flow:** CLI `mistral_key.py doctor`; validates key without using LLM router.

### 5. Legacy / Fallback Path (openclaw_codex_review.sh)

| File | Function/Module | Provider/Model | Purpose |
|------|----------------|----------------|---------|
| `ops/openclaw_codex_review.sh` | Inline Python (heredoc) | `REVIEW_BASE_URL` + `REVIEW_API_KEY` (OpenAI or LiteLLM) | Direct `urllib` POST to `chat/completions` when `src.llm.review_gate` not importable |

**Flow:** Duplicates review prompt and logic; no router, no Mistral fallback, no cost telemetry via router.

---

## Subsystems Without LLM Usage

- **Soma Kajabi lane** (`services/soma_kajabi/`): No LLM calls. Uses browser automation (Playwright) for Kajabi login/capture and Gmail harvest.
- **HQ Doctor / Guard** (`ops/openclaw_doctor.sh`, `ops/openclaw_guard.sh`): Deterministic checks; no LLM.
- **Policy evaluator** (`ops/policy/policy_evaluator.py`): Deterministic; no LLM.
- **llm.microgpt.canary**: Offline canary using Karpathy microgpt (training-only, no inference); no LLM providers.

---

## Risks / Smells

1. **Dual review paths**: `openclaw_codex_review.sh` has two code paths — router (`review_gate`) and inline Python. Inline path duplicates prompt, skips Mistral fallback, and does not write cost telemetry via router.
2. **Hard-coded model in ask_engine**: `ask_engine.py` passes `model="gpt-4o-mini"` explicitly; router overrides via `purpose="general"`, but the hard-code is redundant and could drift.
3. **Mistral key doctor bypasses router**: `mistral_key.py` uses direct Mistral API with `open-mistral-7b`; not aligned with `config/llm.json` fallback model.
4. **No central router for all call sites**: Doctor and mistral_key call providers directly; only review_gate and ask_engine use the router.
5. **LiteLLM vs native router**: Optional LiteLLM proxy (`config/litellm.yaml`) can sit in front of providers, but `OPENCLAW_LITELLM_PROXY_URL` is only used by `OpenAIProvider`; other providers (Mistral, Moonshot) use their own API bases.
6. **Prompt duplication**: Review system prompt exists in both `review_gate.py` and inline Python in `openclaw_codex_review.sh`.
7. **Cost/latency blind spots**: Cost tracking exists (`cost_tracker.py`, `budget.py`) but the inline review path does not log via router; Mistral key doctor is not tracked.

---

## Quick Wins

1. **Remove inline review path**: Make `openclaw_codex_review.sh` fail clearly if `review_gate` is not importable, instead of falling back to duplicated logic.
2. **Single source for review prompt**: Move `REVIEW_SYSTEM_PROMPT` to a shared module (e.g. `src/llm/prompts.py`) and import from both `review_gate` and any remaining callers.
3. **Route mistral_key doctor through router**: Use `router.resolve_review_fallback()` + provider for Mistral validation, or add a `purpose="doctor"` that hits Mistral with the configured fallback model.
4. **Route doctor through router**: Have `doctor.py` use `router.generate()` with `purpose="general"` or a dedicated `purpose="doctor"` instead of direct provider calls, for consistency and cost tracking.
5. **Drop hard-coded model in ask_engine**: Use `model=""` and let router resolve; already supported by `LLMRequest`.
6. **Add TODO(LLM_ROUTER) comments**: At each direct provider call site (doctor, mistral_key, inline review) to mark future router migration.

---

## Config & Env Reference

| Config/Env | Purpose |
|------------|---------|
| `config/llm.json` | Provider list, defaults, review fallback, budget, review caps |
| `config/llm.schema.json` | JSON schema for llm.json |
| `config/litellm.yaml` | LiteLLM proxy config (optional) |
| `OPENAI_API_KEY` | OpenAI key (env, keychain, or `/etc/ai-ops-runner/secrets/openai_api_key`) |
| `MISTRAL_API_KEY` | Mistral key (env, keychain, or `/run/openclaw_secrets/mistral_api_key`) |
| `OPENCLAW_REVIEW_MODEL` | Override review model (default: gpt-4o-mini) |
| `OPENCLAW_ALLOW_EXPENSIVE_REVIEW` | Allow gpt-4o for review (must be `1`) |
| `OPENCLAW_LITELLM_PROXY_URL` | Route OpenAI via LiteLLM proxy (e.g. `http://127.0.0.1:4000/v1`) |
