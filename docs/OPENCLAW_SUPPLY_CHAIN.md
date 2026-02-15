# OpenClaw Supply-Chain Verification

**Date**: 2026-02-15
**Scope**: Determine if openclaw.ai is open-source and safe to consider for ops texting. DO NOT INSTALL.

## Summary

| Question | Answer |
|----------|--------|
| Open source? | **YES** — MIT license |
| GitHub repo | [openclaw/openclaw](https://github.com/openclaw/openclaw) (196k+ stars) |
| License | MIT |
| Do we adopt it for OpenClaw ops control? | **NO** — see rationale below |

## What Is OpenClaw (openclaw.ai)?

OpenClaw is a **personal AI assistant gateway** that connects messaging apps (WhatsApp, Telegram, Discord, Slack, Signal, iMessage, etc.) to AI models (Claude, GPT, etc.). It is self-hosted and open source.

It is **NOT** an ops/infrastructure control tool. It is a chat relay for AI assistants.

## Installer Analysis (NOT executed — read-only review)

### macOS/Linux installer (`curl -fsSL https://openclaw.ai/install.sh | bash`)

What the installer does:
1. Installs **Homebrew** (macOS) if missing — broad system package manager
2. Installs **Node.js 22+** if missing — via Homebrew or system package manager
3. Installs `openclaw` globally via **npm** (`npm install -g openclaw@latest`)
4. Runs `openclaw doctor --non-interactive` (for upgrades/migrations)
5. Prompts to run `openclaw onboard` (new installs)

### Privilege escalation
- Homebrew install may require sudo
- npm global install may require sudo on some systems
- No direct root actions beyond package installation

### Network fetches during install
- GitHub (Homebrew)
- npm registry (openclaw package + all transitive dependencies)
- Node.js release binaries (if not installed)

### Runtime network access
- Connects to AI provider APIs (Anthropic, OpenAI) via user-provided keys
- WebSocket listeners for messaging channels
- Gateway binds to `127.0.0.1:18789` by default

## Security Assessment

### Strengths
- MIT license, fully transparent source
- Large community (196k stars, 34k forks)
- Default bind to loopback (127.0.0.1)
- Supports Tailscale Serve for exposure (similar to our approach)
- Has its own `doctor` and security features

### Risks (relevant to our ops plane)
1. **Massive dependency tree** — npm package with hundreds of transitive deps; supply-chain attack surface
2. **Chat gateway surface** — connects to external messaging APIs; inbound message processing from untrusted sources
3. **Agent execution** — can run arbitrary commands on the host (tools: bash, browser, canvas, system.run)
4. **Not ops-focused** — designed for personal assistant use, not infrastructure health/guard
5. **Installer pipes curl to bash** — classic supply-chain risk vector

## Decision: DO NOT ADOPT

**Recommendation: NO — do not install openclaw.ai into the ops plane.**

### Rationale
1. **Attack surface**: OpenClaw is an AI chat gateway that processes inbound messages and can execute arbitrary commands. Adding it to our tightly controlled VPS (aiops-1) would massively expand the attack surface.

2. **We already have better**: Our existing OpenClaw ops console (`apps/openclaw-console/`) is:
   - Allowlist-only (7 predefined commands, no arbitrary execution)
   - Token-authenticated with CSRF protection
   - Bound to 127.0.0.1 only, exposed via Tailscale Serve
   - Audit-logged with action locking
   - Phone-accessible via Tailscale (HTTPS on ts.net)

3. **Scope violation**: Our ops plane must remain minimal and fail-closed. A chat gateway with hundreds of npm deps violates the automation-first, fail-closed security model.

4. **Future path**: If we want texting/messaging integration later, the safe approach is:
   - Outbound-only Pushover notifications (already implemented)
   - Optional Telegram long-poll bot (outbound + simple inbound commands, rate-limited, allowlist-only)
   - Keep the bot OUTSIDE the ops VPS (separate container/service)

## Conclusion

OpenClaw is a legitimate, well-maintained open-source project. It is NOT appropriate for our ops control plane. Our existing private console + Pushover notifications + Tailscale Serve provides phone access without the attack surface of a full chat gateway.

**Boundary**: We are NOT installing openclaw.ai assistant into the ops plane. The name "OpenClaw" in our project refers to our private-only ops runner, doctor, guard, and console — not the openclaw.ai assistant.
