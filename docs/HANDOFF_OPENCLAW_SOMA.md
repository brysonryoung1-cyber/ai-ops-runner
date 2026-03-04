# OpenClaw / ai-ops-runner — Soma Kajabi Lane — Handoff

## 1. One-paragraph summary

**OpenClaw** is the private ops control plane in ai-ops-runner: HQ UI, hostd, doctor, guard, and project-specific lanes. The **Soma Kajabi lane** manages the Zane Kajabi site (zane-mccourtney.mykajabi.com): Home User Library, Practitioner Library, Gmail video harvest, and the mirror invariant (all above-paywall Home lessons must exist in Practitioner). **Done** means: platform health GREEN, Soma pipeline SUCCESS with all four acceptance artifacts present, mirror exceptions empty, and Business DoD validators PASS.

---

## 2. Definition of Done (DoD)

### A) Platform health GREEN

- Doctor PASS (9 checks: Tailscale, Docker, API healthz, port audit, disk pressure, key health, console bind, guard timer)
- hostd reachable (`/api/exec?check=connectivity` → 200)
- noVNC ready (openclaw_novnc_doctor DEEP mode PASS — framebuffer warm-up + WebSocket stability)
- Guard timer active with recent PASS/FAIL entries

### B) Soma pipeline SUCCESS with acceptance artifacts + Mirror PASS

- `soma_run_to_done` exits 0 with `status: "SUCCESS"`
- `artifacts/soma_kajabi/acceptance/<run_id>/` contains all four required artifacts:
  - `final_library_snapshot.json`
  - `video_manifest.csv`
  - `mirror_report.json` (with `exceptions: []`)
  - `changelog.md`
- Mirror invariant: `mirror_report.pass === true`, `exceptions.length === 0`
- Business DoD validators (8 checks) PASS

### Truthfulness contract (enforced in code)

- **SUCCESS requires Mirror PASS**: `soma_run_to_done` will return FAIL with `error_class: MIRROR_FAIL` if `mirror_pass === false` or `mirror_exceptions_count > 0`. No SUCCESS is possible without Mirror PASS.
- **Run-scoped acceptance**: Acceptance and mirror data must come from the current run's artifact dirs. No "latest directory" fallback. If acceptance is missing for the run, `soma_run_to_done` returns FAIL with `error_class: ACCEPTANCE_MISSING_FOR_RUN`.
- **Early PROOF/PRECHECK writes**: `soma_run_to_done` writes `PROOF.json` (status=RUNNING, phase=init) and `PRECHECK.json` (status=RUNNING, precheck=pending) immediately on run start, before prechecks begin. These files are updated in-place at each phase transition (precheck → trigger → polling → acceptance_verification → done). Remote helpers never encounter a missing PROOF.json.
- **Run-scoped Phase0**: `soma_kajabi_auto_finish` will not fall back to the latest Phase0 dir. If the Phase0 dir for the current run is missing, it fails with `PHASE0_MISSING_FOR_RUN`. If the dir exists but is degraded (snapshot missing), it fails with `PHASE0_DEGRADED`.
- **Status endpoint honesty**: The status endpoint never sets `mirror_pass: true` when the actual mirror state is unknown. It returns `mirror_state: "UNKNOWN_NO_ACCEPTANCE_FOR_RUN"` when no acceptance data exists for the current run.

### Not done if …

- `NOVNC_NOT_READY` — noVNC doctor FAIL; human cannot complete Cloudflare/Kajabi login
- `WAITING_FOR_HUMAN` — auth gate active; human must complete login via noVNC, then re-trigger
- `MIRROR_EXCEPTIONS_NON_EMPTY` — above-paywall Home lessons missing in Practitioner
- `MIRROR_FAIL` — `soma_run_to_done` detected mirror exceptions; run is FAIL
- `ACCEPTANCE_MISSING_FOR_RUN` — acceptance artifacts not found for the current run
- `PHASE0_MISSING_FOR_RUN` — Phase0 dir for the current run not found (no latest fallback)
- `PHASE0_DEGRADED` — Phase0 dir exists but key artifacts missing
- `TRIGGER_FAILED` — exec POST failed (hostd unreachable, timeout, etc.)
- Any acceptance artifact missing or mirror `pass: false`

---

## 3. Locked Soma/Kajabi spec (non-negotiables)

| Item | Value |
|------|-------|
| **Brand/client** | Zane McCourtney (Soma) |
| **Site** | zane-mccourtney.mykajabi.com |
| **Kajabi admin** | app.kajabi.com |
| **Tiers** | Home User Library (free), Practitioner Library (paid superset) |
| **Products** | Both must be discoverable via `soma_kajabi_discover` |
| **Offer URLs** | `/offers/q6ntyjef/checkout`, `/offers/MHMmHyVZ/checkout` — must be found on memberships page |
| **Community** | Soma Community; groups: Home Users, Practitioners |
| **Video mapping** | Gmail harvest: `from:(Zane McCourtney) has:attachment`; manifest columns: subject, timestamp, filename, mapped_lesson, status (attached \| raw_needs_review) |
| **Mirror** | All above-paywall Home lessons MUST exist in Practitioner; same module, title, description, video, published/draft state |
| **Legal** | Terms, privacy, disclaimers in Kajabi; no secrets in artifacts |
| **Constraints** | No deletions; move/hide/draft only. RAW module must be present. Fail-closed on offer URL mismatch, mirror exceptions non-empty, required artifacts missing |

---

## 4. System architecture (control plane + runtime primitives)

| Component | Role |
|-----------|------|
| **HQ UI** | Next.js console at 127.0.0.1:8787; Tailscale Serve at `https://aiops-1.tailc75c62.ts.net`; Projects, Runs, Logs, Artifacts, Actions |
| **hostd** | Python executor on VPS; runs actions from `config/action_registry.json`; probe `/health`; 10/20/40s backoff before 502 |
| **action_registry.json** | Source of truth for all allowlisted actions; schema-validated; `soma_kajabi_auto_finish`, `soma_run_to_done`, etc. |
| **doctor** | `ops/openclaw_doctor.sh` — 9 checks; JSON output to `artifacts/doctor/<timestamp>/doctor.json` |
| **canary** | `ops/scripts/canary.sh` — Reconcile + noVNC + Ask + Version |
| **apply-and-prove** | `ops/remote/aiops_apply_and_prove.sh` — Mac-side deploy + doctor + proof bundle |
| **fixpacks** | Emitted by `novnc_autorecover.py`, `novnc_fixpack_emit.sh` on unrecoverable noVNC failure |
| **human gate** | `ops/lib/human_gate.py` — per-project gate file; scripts that restart noVNC check and suppress when gate active |
| **Artifacts** | VPS: `/opt/ai-ops-runner/artifacts/`; Local (Mac): `./artifacts/`; proof bundles in `artifacts/local_apply_proofs/<run_id>/` |

---

## 5. What we built and shipped (by capability)

### 5.1 Shared exec trigger client

- **Why it exists:** Single source of truth for HQ exec POST; prevents TRIGGER_FAILED from timeout mismatch (HQ hostd probe 10–70s; old 5s timeout caused false negatives).
- **Key files:** `ops/lib/exec_trigger.py`, `docs/EXEC_TRIGGER_CURRENT_STATE.md`
- **Operational behavior:** `trigger_exec(project, action)` — 90s default timeout; 409 → `ALREADY_RUNNING` (non-fatal); 200/202 → `ACCEPTED`; 502/timeout → `FAILED`.
- **Known failure modes:** Network partition; hostd down; HQ unreachable.

### 5.2 noVNC stability hardening

- **Why it exists:** Soma requires noVNC for human Cloudflare/Kajabi login; flaky noVNC blocks WAITING_FOR_HUMAN resolution.
- **Key files:** `ops/scripts/novnc_autorecover.py`, `ops/scripts/novnc_shm_fix.sh`, `ops/scripts/novnc_restart.sh`, `ops/scripts/novnc_ws_probe.py`, `ops/openclaw_novnc_doctor.sh`, `ops/guards/novnc_framebuffer_guard.sh`, `ops/caddy/Caddyfile.frontdoor`
- **Operational behavior:** Caddy frontdoor 127.0.0.1:8788 routes /api/* → 8787, /novnc/*, /websockify → 6080. WSS probe over 443. Doctor DEEP mode: framebuffer warm-up (6×5s) + WS stability. Autorecover: doctor → shm_fix → restart → routing_fix → restart → doctor(DEEP).
- **Known failure modes:** shmget /dev/shm constraint; routing edge cases; Tailscale Serve path mapping (mitigated by single-root Serve).

### 5.3 Human Gate (pinned noVNC URL + suppression)

- **Why it exists:** When WAITING_FOR_HUMAN, noVNC must stay up for human to complete login; scripts that restart noVNC would disrupt the session.
- **Key files:** `ops/lib/human_gate.py`, `apps/openclaw-console/src/components/GuidedHumanGateBanner.tsx`, `ops/scripts/human_gate_watcher.py`
- **Operational behavior:** Gate file at `<state_dir>/human_gate/<project_id>.json`; TTL 35 min (override `OPENCLAW_HUMAN_GATE_TTL_MINUTES`). `write_gate()` / `read_gate()` / `clear_gate()`. shm_guard, shm_fix, routing_fix, novnc_restart check gate and suppress if active. `OPENCLAW_FORCE_AUTORECOVER=1` bypasses suppression for deliberate recovery.
- **Known failure modes:** Gate TTL expiry before human completes; stale gate if script crashes.

### 5.4 Business DoD validators (8 checks)

- **Why it exists:** Deterministic, no-LLM validation of business readiness before declaring Soma PASS.
- **Key files:** `services/soma_kajabi/verify_business_dod.py`
- **Operational behavior:** Writes to `artifacts/soma_kajabi/business_dod/<run_id>/`. Eight checks: (1) RAW module present, (2) site hostname, (3) landing page reachable, (4) terms + privacy URLs, (5) offer URLs on memberships page, (6) no secrets in artifacts, (7) community groups, (8) manifest dedupe.
- **Known failure modes:** Terms/privacy 404 if not yet configured in Kajabi; offer URL check depends on discover capturing memberships page.

### 5.5 Remote deploy loop (“apply_and_prove” + “run_to_done” helpers)

- **Why it exists:** Mac-side ops: deploy to VPS, run Soma, collect proof without SSH-ing into each step manually.
- **Key files:** `ops/remote/aiops_apply_and_prove.sh`, `ops/remote/aiops_soma_run_to_done.sh`, `ops/lib/aiops_remote_helpers.py`, `docs/OPS_REMOTE_APPLY.md`
- **Operational behavior:** `aiops_apply_and_prove.sh` — SSH deploy, health before/after, compose_ps, compose_logs_tail, RESULT.json. `aiops_soma_run_to_done.sh` — POST /api/exec soma_run_to_done, poll until terminal status; exit 0=SUCCESS, 1=FAIL, 2=WAITING_FOR_HUMAN. Proof to `artifacts/local_apply_proofs/<utc_run_id>/`.
- **Known failure modes:** SSH key/auth; BASE_URL unreachable (Tailscale down); bash 3.2 mapfile (uses read-loop fallback).

### 5.6 Local compatibility fixes (bash mapfile, docker group)

- **Why it exists:** macOS ships bash 3.2; `mapfile` is bash 4+. Remote scripts must run on Mac and VPS.
- **Key files:** `ops/remote/aiops_soma_run_to_done.sh` (read-loop instead of mapfile), `ops/remote/aiops_apply_and_prove.sh`
- **Operational behavior:** `while IFS= read -r line; do arr+=("$line"); done < <(...)` pattern. Docker group: VPS user must be in `docker` group for compose.
- **Known failure modes:** None documented; compatibility explicitly tested in selftests.

---

## 6. Current operational status (as-of last known proof)

### What’s green

- Full pipeline: `soma_run_to_done` → precheck → `soma_kajabi_auto_finish` → Phase0 → Zane Finish Plan → acceptance artifacts → fail-closed gates
- Shared exec trigger client (90s timeout, 409 semantics) — TRIGGER_FAILED incident resolved
- Offer URL check fixed to use memberships page (not products page)
- Acceptance lookup fixed via SUMMARY.json
- Video manifest columns aligned to spec
- Human gate suppression during WAITING_FOR_HUMAN

### What’s not done

- Soma SUCCESS not yet achieved in recent runs (blocked by noVNC readiness)
- RAW module check: not enforced in auto_finish; manual verification only
- Terms/privacy/waiver URLs: may need configuration in Kajabi; waiver text placement UNKNOWN

### Current top blocker: NOVNC_NOT_READY

- `soma_run_to_done` precheck fails `_precheck_novnc()` when openclaw_novnc_doctor (DEEP) does not PASS
- Autopilot tick: if doctor FAIL after recovery chain (shm_fix, restart, retry), BLOCKED(novnc_not_ready) — no Soma trigger
- **Resume path:** Run `soma_novnc_oneclick_recovery` or manual: `openclaw_novnc_doctor` → fix → retry; when READY, re-trigger `soma_run_to_done`

### Known mismatch: origin/main sha vs VPS build_sha

- **Why it matters:** Proof bundles are generated on VPS; local Mac may have different `origin/main`. Artifacts under `artifacts/soma_kajabi/` on VPS are source of truth for that run.
- **Why proof bundles are source of truth:** `artifacts/local_apply_proofs/<run_id>/` on Mac contains parsed result from remote; VPS `artifacts/soma_kajabi/run_to_done/<run_id>/PROOF.json` is canonical for that execution.

---

## 7. The exact ops loop being run

### Steps 1–3 (as described)

1. **Precheck:** Drift (build_sha vs origin/main) → run `deploy_pipeline.sh` if drift; hostd connectivity; noVNC doctor (DEEP). Fail → exit 1 with error_class (DRIFT_DEPLOY_FAILED, HOSTD_UNREACHABLE, NOVNC_NOT_READY).
2. **Trigger:** POST `/api/exec` with `soma_kajabi_auto_finish` via shared trigger client. 409 → ALREADY_RUNNING, exit 0 (no spam). FAILED → exit 1 TRIGGER_FAILED.
3. **Poll:** GET `/api/runs?id=<run_id>`; exponential backoff 6s → 24s cap; max 35 min or 120 polls. Read RESULT.json from artifact_dir when available.

### Terminal outcomes and required next action

| Outcome | Exit | Next action |
|---------|------|-------------|
| **SUCCESS** | 0 | Acceptance verification + mirror + Business DoD. Done. |
| **WAITING_FOR_HUMAN** | 2 (remote script) | Human opens pinned noVNC URL, completes Cloudflare/Kajabi login + 2FA, goes to Products → Courses; session_check must PASS within 25 min. Then re-trigger `soma_run_to_done`. |
| **FAIL** | 1 | Run fixpack path: `soma_fix_and_retry` or `soma_novnc_oneclick_recovery`; inspect `evidence_bundle.json`; address error_class; retry. |

---

## 8. What remains to finish Soma cleanly

### Immediate priority

- **noVNC readiness convergence:** Ensure openclaw_novnc_doctor (DEEP) PASS before Soma trigger. Use `soma_novnc_oneclick_recovery` or manual recovery chain.

### Once SUCCESS

- Acceptance verification: confirm all four artifacts present; mirror_report.exceptions empty
- Mirror: validate Home → Practitioner invariant
- Business DoD: run `soma_kajabi_verify_business_dod`; address any failing checks

### External Kajabi items likely still needed

- `/terms`, `/privacy-policy` — must return 200/3xx; if 404, configure in Kajabi
- Waiver text placement: UNKNOWN — document where waiver should appear (checkout, membership, etc.)

---

## 9. Key files / components map

| Path | Description |
|------|-------------|
| `config/action_registry.json` | Source of truth for all allowlisted actions; schema at `config/action_registry.schema.json` |
| `ops/lib/exec_trigger.py` | Shared exec trigger client; `trigger_exec()`, `hq_request()`; 90s timeout, 409=ALREADY_RUNNING |
| `ops/lib/human_gate.py` | Human gate state: write_gate, read_gate, clear_gate; TTL 35 min; suppression for noVNC restarts |
| `ops/scripts/novnc_autorecover.py` | Autorecover chain: doctor → shm_fix → restart → routing_fix → doctor(DEEP); emits fixpack on unrecoverable |
| `ops/scripts/novnc_*.sh` | novnc_restart, novnc_shm_fix, novnc_ws_probe, openclaw_novnc_routing_fix, novnc_fast_precheck |
| `ops/guards/novnc_framebuffer_guard.sh` | Guard for noVNC framebuffer health |
| `ops/scripts/soma_run_to_done.py` | Main orchestrator: precheck → trigger → poll; outputs PROOF.json, PROOF.md |
| `ops/scripts/soma_kajabi_auto_finish.py` | Phase0 → Zane Finish Plan → acceptance artifacts; WAITING_FOR_HUMAN on auth gate |
| `ops/scripts/soma_autopilot_tick.py` | Timer-driven trigger; doctor PASS → soma_run_to_done; BLOCKED on novnc_not_ready |
| `ops/remote/aiops_apply_and_prove.sh` | Mac-side deploy + doctor + proof bundle |
| `ops/remote/aiops_soma_run_to_done.sh` | Mac-side Soma run-to-done; exit 0/1/2 |
| `ops/lib/aiops_remote_helpers.py` | Parse exec trigger, run poll, health assessment; stdlib-only |
| `services/soma_kajabi/verify_business_dod.py` | 8 Business DoD checks; writes to artifacts/soma_kajabi/business_dod/ |
| `services/soma_kajabi/acceptance_artifacts.py` | Writes final_library_snapshot, video_manifest, mirror_report, changelog |
| `ops/scripts/csr_evidence_bundle.sh` | Generates evidence_bundle.json from triage dir |

---

## 10. Proof locations and how to interpret them

### Local (Mac) path template

```
artifacts/local_apply_proofs/<utc_run_id>/
├── health_public_before.json
├── health_public_after.json
├── compose_ps.txt
├── compose_logs_tail.txt
├── remote_actions.log
├── RESULT.json
├── soma_run_to_done_result.json   # when running aiops_soma_run_to_done.sh
├── soma_remote_output.log
├── trigger_response.json
├── run_poll.json
└── proof_browse.json
```

### VPS path template

```
/opt/ai-ops-runner/artifacts/
├── soma_kajabi/
│   ├── run_to_done/<run_id>/
│   │   ├── PROOF.json
│   │   ├── PROOF.md
│   │   ├── PRECHECK.json
│   │   └── TRIGGER.json
│   ├── auto_finish/<run_id>/
│   │   ├── RESULT.json
│   │   ├── SUMMARY.json
│   │   └── WAITING_FOR_HUMAN.json (if auth gate)
│   ├── acceptance/<run_id>/
│   │   ├── final_library_snapshot.json
│   │   ├── video_manifest.csv
│   │   ├── mirror_report.json
│   │   └── changelog.md
│   ├── phase0/<run_id>/
│   └── discover/<run_id>/
├── runs/<run_id>/run.json
└── doctor/<timestamp>/doctor.json
```

### Evidence checklist (for any failure)

1. **run_id** — from PROOF.json, run.json, or soma_run_to_done_result.json
2. **terminal_status** — SUCCESS, WAITING_FOR_HUMAN, FAILURE, TIMEOUT
3. **error_class** — NOVNC_NOT_READY, TRIGGER_FAILED, HOSTD_UNREACHABLE, MIRROR_EXCEPTIONS_NON_EMPTY, etc.
4. **proof_dir** — `artifacts/soma_kajabi/run_to_done/<run_id>/` or `artifacts/local_apply_proofs/<run_id>/`
5. **Links/paths** — Run `ops/scripts/csr_evidence_bundle.sh <triage_dir>` for evidence_bundle.json; cap log quotes to 30 tail lines / 2 KB.

---

## 11. Open questions / needs confirmation

- **Terms/privacy/waiver URLs:** Are `/terms` and `/privacy-policy` configured in Kajabi? Waiver text placement — where should it appear? (UNKNOWN)
- **origin/main vs VPS build_sha drift:** How often does this occur in practice? Is deploy_pipeline.sh sufficient to converge? (Sometimes mismatches; proof bundles are source of truth)
- **noVNC stability:** Does noVNC flap after extended idle? (UNKNOWN — document if observed)
- **RAW module enforcement:** Should auto_finish fail-closed if RAW module missing? (Deferred; manual verification for now)
- **Session warm effectiveness:** Does 6h session warm reduce Cloudflare blocks? (UNKNOWN)

---

## Copy/paste context drop

```
OpenClaw Soma Kajabi lane: Zane site (zane-mccourtney.mykajabi.com). Home + Practitioner libraries, Gmail harvest, mirror invariant. Done = platform GREEN + soma_run_to_done SUCCESS + acceptance artifacts (final_library_snapshot, video_manifest, mirror_report, changelog) + mirror exceptions empty. Top blocker: NOVNC_NOT_READY. Precheck: drift→deploy, hostd, noVNC doctor DEEP. Trigger: shared exec_trigger.py (90s, 409=ALREADY_RUNNING). Poll: 6s→24s backoff, 35 min max. Terminal: SUCCESS (0), WAITING_FOR_HUMAN (2, human login via noVNC), FAIL (1, fixpack). Human gate suppresses noVNC restarts during WAITING. Proof: artifacts/soma_kajabi/run_to_done/<run_id>/PROOF.json; local: artifacts/local_apply_proofs/<run_id>/. Key: ops/lib/exec_trigger.py, human_gate.py, soma_run_to_done.py, soma_kajabi_auto_finish.py, verify_business_dod.py (8 checks). Remote: aiops_apply_and_prove.sh, aiops_soma_run_to_done.sh.
```
