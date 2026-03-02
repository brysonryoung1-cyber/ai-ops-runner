# LLM Router Design

*Architecture reference for `src/llm/llm_router.py`.*

## Responsibilities

1. **Role-based routing** — map logical roles to provider/model via config
2. **Fail-closed safety** — review always fails closed; errors are classified, never swallowed
3. **Centralized logging** — metadata-only (no prompts/responses/secrets) for every call
4. **Provider health checks** — `check_provider_health()` for doctor integration
5. **Error taxonomy** — structured errors with `error_code` and `role` attributes

## Architecture

```
Call sites                     Central Router                    Providers
─────────                     ──────────────                    ─────────
review_gate.py ─┐
ask_engine.py  ──┼─→ llm_router.generate(role, messages)
doctor.py      ──┤       │
mistral_key.py ─┘       ▼
                    ┌─────────────┐
                    │ Role→Purpose │  core_brain → general
                    │   Mapping    │  review_brain → review
                    └──────┬──────┘  doctor_brain → doctor
                           ▼         fast_helper → general
                    ┌─────────────┐
                    │ ModelRouter  │  Resolves provider + model
                    │ (router.py) │  Enforces budget, review caps
                    └──────┬──────┘
                           ▼
                   ┌───────┴───────┐
                   ▼               ▼
             OpenAIProvider   MistralProvider   (+ Moonshot, Ollama)
```

## Logical Roles

| Role | Config Purpose | Default Provider/Model | Safety |
|------|---------------|----------------------|--------|
| `CORE_BRAIN` | `general` | `openai/gpt-4o-mini` | Cost guard applies |
| `REVIEW_BRAIN` | `review` | `openai/CODEX_REVIEW_MODEL` | Hard-pinned, fail-closed, Mistral fallback |
| `DOCTOR_BRAIN` | `doctor` | `openai/gpt-4o-mini` | Essential (bypasses cost guard) |
| `FAST_HELPER` | `fast_helper` | Falls back to `general` | Cost guard applies |

## Error Taxonomy

```
RuntimeError
└── LLMRouterError (error_code, role)
    ├── ConfigError      — missing key, bad config, provider not initialized
    ├── AuthError        — HTTP 401/403
    ├── RateLimitError   — HTTP 429
    └── TransientError   — timeout, HTTP 5xx
ReviewFailClosedError    — both primary + fallback reviewers failed
```

## Adding a New Logical Role

1. Add constant in `llm_router.py`: `MY_ROLE = "my_role"`
2. Add to `ALL_ROLES` and `_ROLE_TO_PURPOSE`
3. Add purpose to `KNOWN_PURPOSES` in `config.py`
4. Optionally add default route in `config/llm.json`
5. Use `llm_router.generate(MY_ROLE, messages)` from call site

## Adding a New Provider

1. Implement `BaseProvider` subclass (see `openai_provider.py` as reference)
2. Add provider name to `KNOWN_PROVIDERS` in `config.py`
3. Add provider config to `config/llm.json`
4. Register in `ModelRouter._setup_providers()`
5. The central router will route to it automatically via config defaults

## Logging Format

Every call logs a JSON metadata entry to stderr:

```json
{"ts":"2026-03-02T...Z","role":"core_brain","provider":"openai","model":"gpt-4o-mini","ok":true,"prompt_tok":500,"compl_tok":100,"total_tok":600}
```

Fields: `ts` (timestamp), `role`, `provider`, `model`, `ok` (success), `err` (error code, if failed), `prompt_tok`, `compl_tok`, `total_tok`.

Never includes: prompts, responses, API keys, or any secret material.
