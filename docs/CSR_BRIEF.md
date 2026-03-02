# CSR Brief — Soma Kajabi Finish + Platform Verification

**Date:** 2026-03-02T02:02:49Z  
**Scope:** OpenClaw / ai-ops-runner ONLY. Primary lane: soma_kajabi.

---

## What Was Fixed (Prior Proofs)

1. **SysV SHM leak** — MIT-SHM disabled in Xvfb (`-extension MIT-SHM`); x11vnc `-noshm`; no shmget ENOSPC.
2. **Orphan GC** — `shm_gc_orphans.sh` (nattch=0, age>60s); ExecStartPre + guard timer.
3. **Guard timer** — `openclaw-novnc-shm-guard.timer` every 5min; threshold 3500 segments → GC or restart.
4. **6080 stability** — 120s WS stability hold in Phase 2a deploy; noVNC readiness gate.
5. **Exec trigger client** — `ops/lib/exec_trigger.py` (default 90s, 409 non-fatal); shared by all ops scripts.

**Proof paths:**
- `artifacts/hq_proofs/novnc_shm_permanent_fix/20260301_231700Z/PROOF.md`
- Canary PASS template: `artifacts/system/canary/canary_20260227T044010Z_000045db/PROOF.md` (last local PASS)

---

## Current Canary Status

- **Last known PASS:** canary_20260227T044010Z_000045db (2026-02-27)
- **Strict canary:** Must re-run on aiops-1 to confirm current status.
- **HQ unreachable from CSR env:** Tailscale/network; verification runs via SSH on aiops-1.

---

## What Remains to Finish

1. **Platform green** — Run strict canary on aiops-1; ensure PASS.
2. **Soma run-to-done** — Trigger `soma_kajabi_auto_finish` via `ops/scripts/soma_run_to_done.py`; poll to SUCCESS or WAITING_FOR_HUMAN.
3. **Acceptance artifacts** — On SUCCESS: `final_library_snapshot.json`, `video_manifest.csv`, `mirror_report.json`, `changelog.md`.
4. **Mirror PASS** — `mirror_report.json` must have `mirror_exceptions == []` (or `exceptions == []`).
5. **HUMAN_ONLY gate** — If WAITING_FOR_HUMAN: user completes Kajabi/Cloudflare login + 2FA via noVNC; then Resume.

---

## Recent Incident

- **SOMA_INCIDENT_20260302004627-7010:** TRIGGER_FAILED — 5s timeout too short for HQ hostd probe. Fix: exec_trigger.py now uses 90s default; soma_run_to_done uses shared client.
