# Soma Pipeline — Current Reality Map

*Diagnostic snapshot. Last updated: 2026-03-02.*

---

## Overview

The Soma pipeline manages the Zane Kajabi site (zane-mccourtney.mykajabi.com): Home User Library, Practitioner Library, Gmail video harvest, and mirror invariant (Home above-paywall → Practitioner superset).

**Two artifact trees (intentionally separate):**
- `artifacts/soma/` — Standalone CLI (`soma_kajabi_sync`): snapshot, harvest, mirror. Used by HQ connector buttons and SMS triggers. Mirror schema uses `actions` (add_category, add_item, etc.) and `summary`.
- `artifacts/soma_kajabi/` — Full pipeline: discover, phase0, auto_finish, acceptance, run_to_done, zane_finish_plan. Mirror schema uses `exceptions` (missing_in_practitioner, video_mismatch) and `pass` boolean. This is the schema used by acceptance gates.

---

## End-to-End Flow: Nothing → Soma Acceptance PASS

```
1. Precheck (drift, hostd, noVNC)
2. soma_kajabi_auto_finish (HQ exec)
   ├── connectors_status
   ├── Phase0 (snapshot + harvest + manifest)
   │   └── [WAITING_FOR_HUMAN if Cloudflare/auth]
   ├── Zane Finish Plan
   ├── Acceptance artifacts (final_library_snapshot, video_manifest, mirror_report, changelog)
   └── Fail-closed gates (mirror_exceptions empty, offer URLs, required artifacts)
3. soma_run_to_done polls until RESULT.json
4. SUCCESS = acceptance artifacts present + mirror_report.exceptions empty
```

**Success:** `artifacts/soma_kajabi/acceptance/<run_id>/` contains all four required artifacts; `mirror_report.json` has `exceptions: []`.

**Failure:** `FAILURE`, `TIMEOUT`, or `WAITING_FOR_HUMAN` (auth gate).

---

## Phase Map

| Phase | Entrypoint | Main Module(s) | Inputs | Outputs | Success Signal |
|-------|------------|----------------|--------|---------|-----------------|
| **DISCOVER** | `soma_kajabi_discover` (hostd) | `ops/scripts/kajabi_discover.py`, `services/soma_kajabi/kajabi_admin_context.py` | storage_state at `/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json` | `artifacts/soma_kajabi/discover/<run_id>/{products.json, page.html, screenshot.png, debug.json}` | `ok: true` in JSON stdout |
| **SNAPSHOT / PHASE0** | `soma_kajabi_phase0` (hostd) or `soma_kajabi_auto_finish` | `services/soma_kajabi/phase0_runner.py`, `services/soma_kajabi_sync/snapshot.py` | config `config/projects/soma_kajabi.json`, storage_state or KAJABI_SESSION_TOKEN | `artifacts/soma_kajabi/phase0/<run_id>/{kajabi_library_snapshot.json, gmail_harvest.jsonl, video_manifest.csv, result.json}` | `result.json` ok=true |
| **RUN_TO_DONE** | `soma_run_to_done` (HQ exec) | `ops/scripts/soma_run_to_done.py` | Triggers `soma_kajabi_auto_finish` via POST /api/exec | `artifacts/soma_kajabi/run_to_done/<run_id>/{PROOF.md, PROOF.json}` | Polls until RESULT.json; SUCCESS or WAITING_FOR_HUMAN |
| **ACCEPTANCE** | Inside `soma_kajabi_auto_finish` | `services/soma_kajabi/acceptance_artifacts.py` | Phase0 dir (kajabi_library_snapshot, video_manifest) | `artifacts/soma_kajabi/acceptance/<run_id>/{final_library_snapshot.json, video_manifest.csv, mirror_report.json, changelog.md}` | `pass: true` (exceptions empty) |

---

## Actions → Artifacts

| Action | Artifact Dir | Key Files |
|--------|--------------|-----------|
| `soma_kajabi_discover` | `artifacts/soma_kajabi/discover/<run_id>/` | products.json, page.html, debug.json |
| `soma_kajabi_phase0` | `artifacts/soma_kajabi/phase0/<run_id>/` | kajabi_library_snapshot.json, gmail_harvest.jsonl, video_manifest.csv, result.json |
| `soma_kajabi_auto_finish` | `artifacts/soma_kajabi/auto_finish/<run_id>/` | RESULT.json, SUMMARY.md, stage.json, WAITING_FOR_HUMAN.json (if auth gate) |
| `soma_kajabi_auto_finish` (acceptance) | `artifacts/soma_kajabi/acceptance/<run_id>/` | final_library_snapshot.json, video_manifest.csv, mirror_report.json, changelog.md |
| `soma_run_to_done` | `artifacts/soma_kajabi/run_to_done/<run_id>/` | PROOF.json, PROOF.md |
| `soma_snapshot_home` / `soma_snapshot_practitioner` | `artifacts/soma/<run_id>/` | snapshot.json (soma_kajabi_sync) |
| `soma_harvest` | `artifacts/soma/<run_id>/` | gmail_video_index.json, video_manifest.csv |
| `soma_mirror` | `artifacts/soma/<run_id>/` | mirror_report.json, changelog.md (different schema than acceptance) |

---

## Acceptance Conditions vs Code

| Check | Spec | Where Enforced | Notes |
|-------|------|----------------|--------|
| Final Library Snapshot | Required | `acceptance_artifacts.write_acceptance_artifacts` → `final_library_snapshot.json` | Written from Phase0 `kajabi_library_snapshot.json` |
| Video Manifest | Required | Same → `video_manifest.csv` | Transformed to spec columns (subject, timestamp, filename, mapped_lesson, status) |
| Mirror Report (exceptions empty) | Required | `soma_kajabi_auto_finish`: `accept_summary.get("pass", False)`; fail-closed on `MIRROR_EXCEPTIONS_NON_EMPTY` | `acceptance_artifacts` computes `_compute_mirror_exceptions`; `pass = len(exceptions)==0` |
| Changelog | Required | Same → `changelog.md` | Written |
| Offer URLs | Required | `_check_offer_urls(root)` in auto_finish | Checks `memberships_page.html` from discover (memberships page at `/memberships-soma`) |
| RAW module present | Required | Not enforced in code | Spec only; TODO for future enforcement |
| No secrets in artifacts | Required | Not explicitly validated | Best-effort |

---

## WAITING_FOR_HUMAN Gates

| Trigger | Error Class | Resume Condition |
|---------|-------------|------------------|
| Cloudflare block | `KAJABI_CLOUDFLARE_BLOCKED` | Human completes challenge via noVNC; `session_check` PASS |
| Session expired | `KAJABI_SESSION_EXPIRED`, `KAJABI_NOT_LOGGED_IN` | Same |
| Capture interactive failed | `KAJABI_CAPTURE_INTERACTIVE_FAILED` | Same |
| Session check timeout | `KAJABI_REAUTH_TIMEOUT` | Manual retry |

**Resume:** `soma_kajabi_session_check` must return `ok: true`. Auto_finish polls session_check every 12s for up to 25 min.

---

## Exec Trigger Client

All project triggers use the **shared exec trigger client** (`ops/lib/exec_trigger.py`), with a default 90 s timeout and 409 "already running" handling. Soma's `run_to_done` is one consumer of this client.

- **Default timeout**: 90 s (Host executor uses 10/20/40 s backoff, total ~70 s; 90 s ensures we never mark TRIGGER_FAILED while hostd is still probing).
- **409 semantics**: HTTP 409 → `ALREADY_RUNNING` (non-fatal). The script reports "run already in progress" and exits cleanly instead of TRIGGER_FAILED.
- **Platform invariant**: see `docs/EXEC_TRIGGER_CURRENT_STATE.md`.

---

## Entrypoints

| Type | Entrypoint | Action ID |
|------|------------|-----------|
| CLI | `python3 ops/scripts/soma_run_to_done.py` | — |
| HQ "Run" | `POST /api/projects/soma_kajabi/run` with action | `soma_run_to_done`, `soma_kajabi_auto_finish`, etc. |
| Hostd | `config/action_registry.json` | `soma_kajabi_phase0`, `soma_kajabi_discover`, `soma_kajabi_auto_finish`, etc. |
| Cron | `openclaw-soma-auto-finish.timer`, `openclaw-soma-autopilot.timer` | — |
| SMS | `RUN_SNAPSHOT`, `RUN_HARVEST`, `RUN_MIRROR` | Via hostd |

---

## LLM Integration

**Soma does NOT use the LLM router.** Per `docs/LLM_CURRENT_STATE.md`: "Soma Kajabi lane: No LLM calls. Browser automation only." No changes needed for LLM routing in Soma.

---

## Hard-Coded Paths / URLs

| Item | Value | Location |
|------|-------|----------|
| Site | zane-mccourtney.mykajabi.com | `kajabi_admin_context.py`, `kajabi_discover.py` |
| Offer URLs | /offers/q6ntyjef/checkout, /offers/MHMmHyVZ/checkout | `soma_kajabi_auto_finish.py` |
| Storage state | /etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json | Multiple |
| Product names | Home User Library, Practitioner Library | `kajabi_admin_context.py`, `kajabi_discover.py` |
