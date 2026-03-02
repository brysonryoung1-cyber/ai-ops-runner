# LLM Current State — OpenClaw ai-ops-runner

*Architecture snapshot. Last updated: 2026-03-02 (post LLM router migration).*

## Overview

OpenClaw uses LLMs for two primary purposes: (1) **code diff review** (security gate, fail-closed) and (2) **grounded Q&A** (Ask OpenClaw from State Pack + artifacts). All LLM calls are routed through a central **LLM router** (`src/llm/llm_router.py`) that uses "logical roles" to abstract away provider/model details. Review is hard-pinned to OpenAI (primary) with Mistral as fallback on transient errors. General-purpose tasks use config-driven defaults.

---

## Architecture

### Central Router (`src/llm/llm_router.py`)

The single entrypoint for ALL LLM calls in the repo. Provides:

- **Role-based routing**: Call sites use logical roles, not provider/model names.
- **Structured error taxonomy**: `ConfigError`, `AuthError`, `RateLimitError`, `TransientError`.
- **Centralized logging**: Metadata-only (no prompts/responses/secrets) for every call.
- **Provider health checks**: `check_provider_health()` for doctor integration.

### Logical Roles

| Role | Purpose | Provider/Model | Notes |
|------|---------|----------------|-------|
| `core_brain` | General Q&A, planning | config `defaults.general` → `openai/gpt-4o-mini` | Config-driven |
| `review_brain` | Code/diff review | OpenAI + `CODEX_REVIEW_MODEL` (hard-pinned) | Mistral fallback on transient errors |
| `doctor_brain` | Health checks | config `defaults.doctor` → `openai/gpt-4o-mini` | Lightweight, essential |
| `fast_helper` | Cheap/fast tasks | Falls back to `general` config | Optional, for future use |

---

## Providers & Models

| Provider | Model(s) | Where Used | Purpose |
|----------|----------|------------|---------|
| **OpenAI** | `gpt-4o-mini` (default) | All roles | core_brain, review_brain (primary), doctor_brain |
| **OpenAI** | `gpt-4o` | Vision (config) | Vision tasks (not actively used) |
| **Mistral** | `labs-devstral-small-2512` | Review fallback | Fallback when OpenAI returns 429/5xx/timeout |
| **Moonshot** | `moonshot-v1-8k` | Config only (disabled) | General (optional) |
| **Ollama** | local models | Config only (disabled) | Local inference (optional) |

*Note: `CODEX_REVIEW_MODEL` is env-overridable via `OPENCLAW_REVIEW_MODEL`; default `gpt-4o-mini`.*

---

## Call Sites (all via central router)

### 1. Review Gate (`src/llm/review_gate.py`)

- **Role**: `REVIEW_BRAIN`
- **Flow**: Bundle → `run_review()` → `llm_router.generate(REVIEW_BRAIN, ...)` → OpenAI; on transient error → Mistral fallback. Both fail → fail-closed.
- **Shell entrypoint**: `ops/openclaw_codex_review.sh` requires `review_gate` (no inline fallback).

### 2. Ask OpenClaw (`services/test_runner/test_runner/ask_engine.py`)

- **Role**: `CORE_BRAIN`
- **Flow**: Question + context → `llm_router.generate(CORE_BRAIN, ...)` → response used for answer, citations, recommended_next_action.
- No hard-coded model names; router resolves from config.

### 3. Provider Doctor (`src/llm/doctor.py`)

- **Role**: `DOCTOR_BRAIN` (via `check_provider_health()`)
- **Flow**: `check_provider_health("openai", model)` / `check_provider_health("mistral", model)` → minimal completion → status artifact.
- All calls tracked via central router logging.

### 4. Mistral Key Validation (`ops/mistral_key.py`)

- **Role**: `DOCTOR_BRAIN` (via `check_provider_health()`)
- **Flow**: `check_provider_health("mistral", ...)` via the central router. Falls back to direct HTTP only if router module is not importable.
- Uses configured fallback model, not a hard-coded model.

---

## Subsystems Without LLM Usage

- **Soma Kajabi lane** (`services/soma_kajabi/`): No LLM calls. Browser automation only.
- **HQ Doctor / Guard** (`ops/openclaw_doctor.sh`, `ops/openclaw_guard.sh`): Deterministic checks; no LLM.
- **Policy evaluator** (`ops/policy/policy_evaluator.py`): Deterministic; no LLM.
- **llm.microgpt.canary**: Offline canary (training-only, no inference).

---

## Risks / Remaining Items

1. **LiteLLM proxy integration**: `OPENCLAW_LITELLM_PROXY_URL` only used by `OpenAIProvider`; Mistral/Moonshot/Ollama use their own API bases.
2. **Local model integration**: Ollama/local models are configurable but disabled. Future work to enable `fast_helper` role with local models.
3. **Metrics depth**: Current logging is metadata-only; future work for latency percentiles and cost dashboards.

---

## Resolved Issues (from previous state)

1. ~~Dual review paths~~: Inline Python fallback removed from `openclaw_codex_review.sh`. All reviews go through `review_gate` → central router.
2. ~~Direct provider calls~~: Doctor and `mistral_key.py` now use `check_provider_health()` via the central router. All calls tracked.
3. ~~Prompt duplication~~: Review system prompt exists only in `review_gate.py` (single source).
4. ~~Hard-coded model in ask_engine~~: Removed. Router resolves model from `purpose="general"` config.
5. ~~Mistral key doctor bypasses router~~: Now uses `check_provider_health()` with configured model.
6. ~~No central router for all call sites~~: All call sites now use `llm_router.generate()` or `check_provider_health()`.
7. ~~Cost/latency blind spots~~: All calls go through central router logging.

---

## Config & Env Reference

| Config/Env | Purpose |
|------------|---------|
| `config/llm.json` | Provider list, defaults (incl. doctor), review fallback, budget, review caps |
| `config/llm.schema.json` | JSON schema for llm.json |
| `config/litellm.yaml` | LiteLLM proxy config (optional) |
| `OPENAI_API_KEY` | OpenAI key (env, keychain, or `/etc/ai-ops-runner/secrets/openai_api_key`) |
| `MISTRAL_API_KEY` | Mistral key (env, keychain, or `/run/openclaw_secrets/mistral_api_key`) |
| `OPENCLAW_REVIEW_MODEL` | Override review model (default: gpt-4o-mini) |
| `OPENCLAW_ALLOW_EXPENSIVE_REVIEW` | Allow gpt-4o for review (must be `1`) |
| `OPENCLAW_LITELLM_PROXY_URL` | Route OpenAI via LiteLLM proxy |
