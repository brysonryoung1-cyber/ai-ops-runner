# OpenCode Integration (Code Lane Agent)

**Status:** Integrated as optional patch generator. Disabled by default.

## Phase 0 — Research (Verified)

### Source
- **Canonical upstream:** [anomalyco/opencode](https://github.com/anomalyco/opencode) (opencode.ai)
- **Install:** `npm i -g opencode-ai`, `brew install anomalyco/tap/opencode`, or Docker `ghcr.io/anomalyco/opencode`

### Pinned Version
- **CLI/npm:** `opencode-ai@1.2.15` (pinned in Dockerfile)
- **Container:** Custom image `ai-ops-runner/opencode-runner:1.2.15` built from Dockerfile that installs pinned npm package
- **Fallback:** `ghcr.io/anomalyco/opencode:latest` if custom build unavailable (document only; prefer pinned)

### Deployment Method
- **Containerized:** Docker image with `opencode run` (non-interactive CLI)
- **Sandbox:** No host secrets by default; provider env via optional mount at `/etc/ai-ops-runner/secrets/opencode/provider_env`

## Non-Negotiables
- OpenCode is **OPTIONAL** and **disabled by default**
- OpenCode is **sandboxed** (container); cannot access host secrets unless explicitly mounted
- **Fail-closed:** Missing binary/provider → clear error, no side effects
- OpenCode **never merges or deploys**; only proposes patches
- `ship_deploy_verify` remains mandatory for merges/deploys
