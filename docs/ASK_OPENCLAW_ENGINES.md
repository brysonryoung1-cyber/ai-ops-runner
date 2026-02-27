# Ask OpenClaw Engine Abstraction

## Overview

Ask OpenClaw uses an engine abstraction so different LLM backends can answer grounded questions. The default engine is the existing OpenClaw LLM integration (OpenAI/Mistral via `src/llm/router`). MicroGPT (Karpathy) is documented as an optional backend but is **not** used for Ask inference.

## Engine Selection

- **Default**: `ASK_ENGINE=default` (or unset). Uses the OpenClaw LLM router.
- **Optional**: `ASK_ENGINE=microgpt`. When enabled, attempts to use the MicroGPT adapter. If it fails, auto-fallback to default.

Per-request override is allowed only when `ASK_ENGINE_PER_REQUEST=1` in config.

## Installed MicroGPT (Karpathy)

| Location | Purpose |
|----------|---------|
| `ops/scripts/microgpt_canary.sh` | Offline canary job — fetches microgpt.py from pinned gist, verifies SHA256, runs training canary |
| `ops/scripts/microgpt_canary_submit.sh` | Submits `llm.microgpt.canary` to test_runner |
| `configs/job_allowlist.yaml` | `llm.microgpt.canary` job definition |
| `services/test_runner/tests/fixtures/microgpt_stub.py` | Test stub |

**Important**: Karpathy's microgpt is a character-level training script, not an inference API. It is used for offline canary validation only. The Ask OpenClaw MicroGPT adapter is a stub that returns "MicroGPT not available for inference" when selected — the default OpenClaw LLM is used instead.

## Safety (MicroGPT Adapter)

When a MicroGPT inference adapter is implemented in the future:

1. It receives only: question + selected file contents/paths from State Pack + artifacts
2. It must NOT run tools, shell, browser, network calls, or any system mutations
3. It must return structured JSON: `{answer, citations[], recommended_next_action, confidence}`
4. Server-side citations validation is enforced — never trust engine output blindly

## Default Engine (OpenClaw LLM)

- **Path**: `src/llm/router.py`, `src/llm/openai_provider.py`, etc.
- **Config**: `config/llm.json`
- **Invocation**: Via test_runner API `POST /ask` (has repo + secrets mount) or Python subprocess when RUNNER_API_URL is not set
