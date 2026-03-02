# CSR Brief — Soma / Kajabi Lane

*Diagnostic snapshot for Implementer. Last updated: 2026-03-02.*

---

## What We Have Now (Soma-Specific)

- **Full pipeline:** `soma_run_to_done` → precheck → `soma_kajabi_auto_finish` → Phase0 → Zane Finish Plan → acceptance artifacts → fail-closed gates.
- **Artifact trees:** `artifacts/soma/` (standalone CLI); `artifacts/soma_kajabi/` (discover, phase0, auto_finish, acceptance, run_to_done).
- **Acceptance artifacts:** `final_library_snapshot.json`, `video_manifest.csv`, `mirror_report.json`, `changelog.md` written under `artifacts/soma_kajabi/acceptance/<run_id>/`.
- **Mirror PASS:** `acceptance_artifacts.write_acceptance_artifacts` computes `_compute_mirror_exceptions`; `pass = len(exceptions)==0`. Auto_finish fails closed on `MIRROR_EXCEPTIONS_NON_EMPTY`.
- **WAITING_FOR_HUMAN:** Auth gates (Cloudflare, session expired) emit WAITING_FOR_HUMAN with noVNC URL; poll `session_check` for up to 25 min.
- **HQ UI:** Soma project page with connector buttons; status cards; run attribution.

---

## What Hurts (Soma-Specific Issues)

### A) Spec Mismatches

| # | Spec Reference | File + Function | Drift |
|---|----------------|-----------------|-------|
| 1 | SOMA_LOCKED_SPEC §9: Offer URLs "found on memberships page" | `ops/scripts/soma_kajabi_auto_finish.py` `_check_offer_urls()` | Checks `discover` artifacts `page.html` (products page), not memberships page. Discover never navigates to memberships. |
| 2 | SOMA_ACCEPTANCE_CHECKLIST: Video manifest columns "subject, timestamp, filename, mapped lesson, status (attached \| raw_needs_review)" | `services/soma_kajabi/phase0_runner.py` `_write_video_manifest()`, `acceptance_artifacts._write_video_manifest_artifact()` | Phase0 uses: email_id, subject, file_name, sha256, rough_topic, proposed_module, proposed_lesson_title, proposed_description, status. No "mapped lesson" column; status values differ. |
| 3 | SOMA_LOCKED_SPEC §9: RAW module present | — | Not enforced in code. |
| 4 | HANDOFF: "artifacts/soma/<run_id>/" | — | Correct for soma_kajabi_sync. But phase0/acceptance use `artifacts/soma_kajabi/`. Dual tree is documented but can confuse. |

### B) Operational Hazards

| # | Severity | Description | File |
|---|----------|-------------|------|
| 1 | HIGH | Offer URL check uses wrong page. If discover has run, `page.html` is products page; offer URLs won't be there → false FAIL or always REQUIRES_HUMAN. | `soma_kajabi_auto_finish.py` |
| 2 | HIGH | Discover uses Playwright selectors (`a[href*="/products/"]`, `tr`, `[role="row"]`). Kajabi UI changes can break discovery. | `kajabi_discover.py` |
| 3 | MEDIUM | `soma_run_to_done` looks for acceptance by `hostd_run_id` (auto_finish run_id). If run record has wrong `artifact_dir`, acceptance lookup can fail. | `soma_run_to_done.py` lines 298–305 |
| 4 | MEDIUM | `WAITING_FOR_HUMAN` resume: if session_check never PASS, 25 min timeout → `KAJABI_REAUTH_TIMEOUT`. No automatic retry after human fixes. | `soma_kajabi_auto_finish.py` |
| 5 | MEDIUM | Phase0 Gmail harvest: when OAuth missing, writes `gmail_harvest_skipped`; video_manifest can be empty. Zane finish plan marks Gmail-dependent items BLOCKED. | `phase0_runner.py` |
| 6 | LOW | `soma_kajabi_sync` mirror_report schema (actions, summary) differs from `acceptance_artifacts` mirror_report (exceptions, pass). Two different "mirror" concepts. | `soma_kajabi_sync/mirror.py`, `acceptance_artifacts.py` |

### C) LLM Integration Gaps

- **None.** Soma does not use LLM. No direct calls or router integration needed.

### D) Artifact and Logging Problems

| # | Severity | Description | Fix |
|---|----------|-------------|-----|
| 1 | MEDIUM | `mirror_report.json` uses `exceptions` key; spec says "exceptions list". Code is consistent; spec wording is fine. | No change. |
| 2 | LOW | `soma_run_to_done` PROOF.json does not include `artifact_dir` for acceptance when SUCCESS. It does include `acceptance_path`. | Minor. |
| 3 | LOW | No explicit `PROOF.md` / `RESULT.json` in acceptance dir per spec "optional" — auto_finish writes SUMMARY.json in auto_finish dir. | Optional. |
| 4 | MEDIUM | Logs missing run_id in some error paths. | Add run_id to stderr/error messages. |

---

## Implementation Status (Updated 2026-03-02)

All HIGH and MEDIUM issues fixed. LOW items addressed or explicitly deferred.

### FIXED:

1. **Offer URL check** (HIGH → FIXED): `_check_offer_urls` now reads `memberships_page.html` from discover artifacts (captured from `/memberships-soma`) instead of the products admin `page.html`. `kajabi_discover.py` navigates to the memberships page and captures it.
2. **Brittle Playwright selectors** (HIGH → FIXED): `kajabi_discover.py` now uses a layered selector strategy: href-based links → `[role="row"]` and `[data-testid]` → `get_by_role` API. CSS-class selectors are last-resort fallback.
3. **Acceptance lookup** (MEDIUM → FIXED): `soma_run_to_done.py` now reads `SUMMARY.json` from auto_finish for canonical acceptance path, with run_id and latest-dir fallbacks.
4. **WAITING_FOR_HUMAN timeout** (MEDIUM → FIXED): Timeout message now includes duration and explicit retry instructions. `SOMA_KAJABI_REAUTH_POLL_TIMEOUT` env var documented.
5. **Gmail skip logging** (MEDIUM → FIXED): Gmail harvest skip now emits structured JSON to stderr with `run_id`, `project`, `action`, and `reason`.
6. **Video manifest columns** (MEDIUM → FIXED): Acceptance `video_manifest.csv` now uses spec columns: `subject, timestamp, filename, mapped_lesson, status`. Status values normalized to `attached | raw_needs_review`.
7. **Logs missing run_id** (MEDIUM → FIXED): Error outputs in `soma_run_to_done.py`, `soma_kajabi_auto_finish.py`, and `phase0_runner.py` now include `run_id`, `project`, and `action`.
8. **Mirror gate fail-closed** (LOW → FIXED): Default for missing `pass` key changed from `True` to `False` (fail-closed semantics). TODO comment removed.
9. **Dual artifact tree docs** (LOW → FIXED): `SOMA_PIPELINE_CURRENT.md` clarifies both trees and their mirror schemas.

### DEFERRED (non-blocking, explicit TODOs):

- **RAW module check**: Not enforced in code. Would require snapshot to identify which module is "RAW". Documented as manual verification. TODO for future.
- **Dual mirror schema unification**: Intentionally separate — `soma/` uses action-based mirror for mutation planning; `soma_kajabi/` uses exception-based mirror for acceptance validation. Documented.

---

## Dependencies / Gotchas

- **Secrets:** KAJABI_SESSION_TOKEN, storage_state at `/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json`; Gmail OAuth at `gmail_oauth.json`. Resolution: env → Keychain → file.
- **Kajabi login:** Session expiry; Cloudflare blocks. noVNC + session_check required for human gate.
- **Exit node:** Optional `/etc/ai-ops-runner/config/soma_kajabi_exit_node.txt` for Mac laptop routing. If set, `with_exit_node.sh` wraps discover/snapshot/phase0.
- **Hostd:** `soma_kajabi_auto_finish` runs via hostd. `.venv-hostd` must have Playwright + Chromium.
- **Rate limits:** Kajabi/Cloudflare may throttle. No explicit backoff in discover/snapshot.
