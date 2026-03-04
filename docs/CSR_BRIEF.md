# CSR_BRIEF — Soma Kajabi blockers after SUCCESS run 20260303221322-7111

## Doctor Matrix 4-fail root cause (2026-03-04)

| Check | Root cause |
|-------|------------|
| CORE.HQ_HEALTH | remote_localhost returned HTTP 200 but empty body; check required both frontdoor AND remote_localhost ok. Fix: PASS when frontdoor ok. |
| CORE.RUN_DIR_RESOLUTION_CONTRACT | Pointer resolved from local artifacts_root (Mac); LATEST_RUN.json only exists on VPS. Fix: browse fallback when local pointer missing in LIVE mode. |
| PROJECT.SOMA_POINTER_PRESENT | LATEST_RUN.json missing on VPS; browse returns 404. Fix: bootstrap step in apply_and_prove creates pointer from latest run_to_done_* dir. |
| PROJECT.SOMA_RUN_TO_DONE_PROOF_SHAPE | Cascade from pointer 404; cannot validate proof without pointer. Fix: same bootstrap. |

---

## 1) Observed facts (with evidence paths)

- **run_id:** `20260303221322-7111` (auto_finish / hostd run)
- **local proof JSON:** `artifacts/local_apply_proofs/20260303T221321Z_soma_f14b1c/soma_run_to_done_result.json`
  - `terminal_status`: `"SUCCESS"`
  - `remote_run_id`: `"20260303221322-7111"`
  - `finished_at`: `"2026-03-03T22:22:03.283459+00:00"`
- **acceptance_dir_vps:** `/opt/ai-ops-runner/artifacts/soma_kajabi/acceptance/20260303221322-7111/`
  - `mirror_report.json` shows `mirror_exceptions_count: 1` (or `exceptions.length === 1`) → `mirror_pass: false`
- **business DoD artifact:** `/opt/ai-ops-runner/artifacts/soma_kajabi/business_dod/bdod_20260303T223454Z_8dedaa66`
  - FAIL 3/8
  - **failed_checks:** `raw_module_present`, `terms_privacy_urls`, `offer_urls_present`, `community_groups_exist`, `manifest_dedupe`
- **status endpoint:** returns stale `mirror_pass: true`, `exceptions_count: 0` despite acceptance having 1 exception
- **key finding:** "no auto_finish run has completed as SUCCESS today"; many attempts hit `KAJABI_UI_NOT_PRESENT` or `WAITING_FOR_HUMAN`
- **acceptance files present:** `final_library_snapshot.json`, `video_manifest.csv`, `mirror_report.json`, `changelog.md` (per VPS path)

---

## 2) Root-cause classification (tight)

### Mirror FAIL despite run_to_done SUCCESS

`soma_run_to_done` reports SUCCESS when `RESULT.json` (from `soma_kajabi_auto_finish`) has `status: "SUCCESS"`. It does **not** require `mirror_pass === true` before returning SUCCESS. The script reads `mirror_report.json` from the acceptance dir and writes `mirror_pass` / `exceptions_count` into PROOF.json, but it still exits 0 and prints `"status": "SUCCESS"` regardless of mirror state. So a run can be declared SUCCESS even when acceptance has mirror exceptions.

**Code path:** `ops/scripts/soma_run_to_done.py` lines 376–444: when `terminal_status == "SUCCESS"`, it computes `mirror_pass` from acceptance and writes PROOF, but always `return 0`.

### Degraded Phase0 → invalid acceptance artifacts

`ops/scripts/soma_kajabi_auto_finish.py` lines 818–826:

```python
phase0_dir = phase0_root / phase0_run_id if phase0_run_id else None
if not phase0_dir or not phase0_dir.exists():
    dirs = sorted([d for d in phase0_root.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    phase0_dir = dirs[0] if dirs else None
```

When Phase0 fails (e.g. `KAJABI_UI_NOT_PRESENT`, `WAITING_FOR_HUMAN`), `phase0_run_id` may be empty or point to a failed run. The fallback uses the **latest** Phase0 dir by name sort, which can be from a different run or a stale/degraded snapshot. Acceptance is then generated from that Phase0 and passed to `write_acceptance_artifacts`. If the gate passes (e.g. old snapshot happened to have 0 exceptions), auto_finish returns SUCCESS even though the acceptance was built from non-run-scoped, potentially degraded data.

### Stale-cache status endpoint

`apps/openclaw-console/src/app/api/projects/[projectId]/status/route.ts`:

- Lines 156–178: iterates `run_to_done` PROOF dirs (sorted by name desc), reads first PROOF with `mirror_pass` / `exceptions_count`.
- Lines 182–204: fallback to latest acceptance dir if no `acceptancePath`.
- **Lines 206–209:** `if (lastStatus === "SUCCESS" && mirrorPass === null) { mirrorPass = true; exceptionsCount = 0; }`

The status endpoint can show stale mirror values because:
1. It may read an older PROOF (e.g. from a prior SUCCESS run) before the latest one.
2. The SUCCESS fallback overwrites `mirrorPass` with `true` when `mirrorPass === null`, even when the latest acceptance has exceptions.
3. There is no per-run cache key; resolution is by “latest” dirs, not by the run_id of the current run.

---

## 3) Minimal permanent fix plan (ordered, smallest blast radius)

### A) Truthful terminal state

**Goal:** `soma_run_to_done` MUST NOT report SUCCESS unless:
- an `auto_finish` step succeeded with live Kajabi UI access, OR acceptance artifacts were produced from a non-degraded Phase0 for that run; AND
- mirror verification is executed and passes (or is explicitly reported as FAIL and `run_to_done` returns FAIL).

**Candidate file:** `ops/scripts/soma_run_to_done.py`

**Planned changes:**
1. After resolving acceptance dir and reading `mirror_report.json`, if `mirror_pass === false` (exceptions_count > 0), treat as FAIL: write PROOF with `status: "FAILURE"`, `error_class: "MIRROR_EXCEPTIONS_NON_EMPTY"`, exit 1.
2. Add a check: if acceptance was produced from a Phase0 dir that does not match the current run’s `phase0_run_id` (e.g. via `SUMMARY.json` or artifact_dirs), treat as `DEGRADED_SOURCE` and fail.

**Acceptance criteria:** SUCCESS only when mirror_report.exceptions.length === 0; otherwise FAIL with explicit error_class.

**Failure modes:** False FAIL if mirror_report schema changes; ensure schema is stable.

---

### B) Run-scoped acceptance

**Goal:** Acceptance generation must use Phase0 data tied to the same `run_id`, OR explicitly mark `DEGRADED_SOURCE` and fail.

**Candidate file:** `ops/scripts/soma_kajabi_auto_finish.py`

**Planned changes:**
1. Remove the “latest phase0” fallback (lines 822–824). If `phase0_run_id` is missing or `phase0_dir` does not exist, fail with `PHASE0_ARTIFACTS_MISSING` or new `DEGRADED_SOURCE`.
2. Optionally: add a `phase0_source_run_id` (or similar) to acceptance metadata; if it differs from current `run_id`, mark `DEGRADED_SOURCE` and fail the gate.

**Acceptance criteria:** No acceptance produced from “latest” Phase0; only from Phase0 of the current run.

**Failure modes:** More FAILs when Phase0 fails; expected and correct.

---

### C) Status endpoint correctness

**Goal:** Status endpoint mirror fields must be derived from the latest acceptance artifacts for the relevant `run_id` (or explicitly `"UNKNOWN"`), never from a stale cached `true`.

**Candidate file:** `apps/openclaw-console/src/app/api/projects/[projectId]/status/route.ts`

**Planned changes:**
1. Remove the SUCCESS fallback (lines 206–209) that sets `mirrorPass = true` when `mirrorPass === null`.
2. Resolve acceptance dir by run_id: use `artifact_dirs.acceptance` from the latest auto_finish SUMMARY.json, or from the run record’s `artifact_dir` → SUMMARY → acceptance path.
3. Read `mirror_report.json` from that acceptance dir; set `mirrorPass` and `exceptionsCount` from it.
4. If no acceptance found for the run: set `mirrorPass: null`, `exceptionsCount: null` (or `"UNKNOWN"`).

**Invalidation policy:** Per-request recompute (no long-lived cache). If a cache is added later, key it by `run_id` or acceptance dir path.

**Acceptance criteria:** Status reflects the acceptance dir for the current/latest run; no false `mirror_pass: true` when acceptance has exceptions.

**Failure modes:** `UNKNOWN` when no acceptance exists; acceptable.

---

## 4) Verification plan (deterministic)

1. **apply_and_prove** → canary → **run_to_done** → acceptance → **bdod**
2. Run: `ops/remote/aiops_apply_and_prove.sh` then `ops/remote/aiops_soma_run_to_done.sh` (or equivalent).
3. **PASS criteria:**
   - `artifacts/soma_kajabi/acceptance/<run_id>/mirror_report.json`: `exceptions_count === 0`, `pass === true`
   - `soma_run_to_done` exits 0 only when mirror_pass is true; otherwise exits 1 with `MIRROR_EXCEPTIONS_NON_EMPTY`
   - Status endpoint: `mirror_pass` and `exceptions_count` match the acceptance dir for the latest run
   - Business DoD: PASS 8/8 or expected external-only fails clearly labeled (e.g. terms/privacy if not yet configured in Kajabi)

4. **Artifacts to inspect:**
   - `artifacts/local_apply_proofs/<run_id>/soma_run_to_done_result.json`
   - `artifacts/soma_kajabi/run_to_done/<run_id>/PROOF.json`
   - `artifacts/soma_kajabi/acceptance/<run_id>/mirror_report.json`
   - `artifacts/soma_kajabi/business_dod/<run_id>/business_dod_checks.json`

---

## 5) Decision / Open questions

### Must fix in code

- **Truthful terminal state:** `soma_run_to_done` must fail when mirror has exceptions.
- **Run-scoped acceptance:** Remove “latest phase0” fallback; fail on degraded source.
- **Status endpoint:** Remove SUCCESS fallback; derive mirror from acceptance for the run.

### External Kajabi content required

- **terms_privacy_urls:** `/terms`, `/privacy-policy` must return 200/3xx. If 404, configure in Kajabi.
- **offer_urls_present:** Depends on discover capturing memberships page; may need Kajabi content.
- **community_groups_exist:** Soma Community groups (Home Users, Practitioners) must exist in Kajabi.
- **manifest_dedupe:** Validator logic; confirm if bug or external data issue.
- **raw_module_present:** RAW module must exist in Kajabi library; content-side.

### Validator vs external

- Terms/privacy: **external** — URLs must be configured in Kajabi.
- RAW module: **external** — content must exist.
- Offer URLs, community groups: **external** — Kajabi configuration.
- Manifest dedupe: **validator** — confirm logic in `verify_business_dod.py`; fix if bug.

---

## Repo discovery (file paths + functions)

| Concern | File | Function / Location |
|--------|------|--------------------|
| `soma_run_to_done` terminal_status SUCCESS | `ops/scripts/soma_run_to_done.py` | `main()` lines 341–444; SUCCESS block at 376–444; returns 0 regardless of mirror_pass |
| Acceptance artifacts generation | `ops/scripts/soma_kajabi_auto_finish.py` | Lines 818–858; `write_acceptance_artifacts(root, run_id, phase0_dir)` |
| Phase0 directory selection | `ops/scripts/soma_kajabi_auto_finish.py` | Lines 818–826; fallback to `dirs[0]` (latest) when `phase0_run_id` dir missing |
| `write_acceptance_artifacts` | `services/soma_kajabi/acceptance_artifacts.py` | `write_acceptance_artifacts(root, run_id, phase0_dir)` lines 217–259 |
| HQ status endpoint mirror fields | `apps/openclaw-console/src/app/api/projects/[projectId]/status/route.ts` | `GET` handler lines 79–323; PROOF iteration 156–178; SUCCESS fallback 206–209 |
| Soma last run resolver | `apps/openclaw-console/src/lib/soma-last-run-resolver.ts` | `resolveSomaLastRun()`, `getLatestSomaRunRecord()`, `resolveArtifactDir()` |

---

**STATUS (2026-03-03):** All three fixes implemented and tested:
- **A) Truthful terminal state**: `soma_run_to_done.py` now FAILs (exit 1, `MIRROR_FAIL`) when `mirror_pass === false`; FAILs (`ACCEPTANCE_MISSING_FOR_RUN`) when acceptance dir missing; removed "latest" acceptance fallback.
- **B) Run-scoped acceptance**: `soma_kajabi_auto_finish.py` removed "latest phase0" fallback; fails with `PHASE0_MISSING_FOR_RUN` or `PHASE0_DEGRADED`.
- **C) Status endpoint correctness**: `route.ts` removed `SUCCESS → mirrorPass=true` fallback; returns `mirror_state: "UNKNOWN_NO_ACCEPTANCE_FOR_RUN"` when data missing; added `latest_acceptance_run_id` for transparency.

**STATUS (2026-03-04):** Early PROOF/PRECHECK write fix shipped:
- **D) PROOF_MISSING_FOR_RUN race fix**: `soma_run_to_done.py` now writes `PROOF.json` (status=RUNNING) and `PRECHECK.json` (status=RUNNING) immediately after creating the run output directory, before any prechecks begin. Both files are updated in-place via `_update_proof()`/`_update_precheck()` at each phase transition. Remote helpers (`aiops_soma_run_to_done.sh`) will never see a 404 for these files. Phase lifecycle: init → precheck → trigger → polling → acceptance_verification → done.

---

## 6) New blocker after novnc_readiness ship — FileNotFoundError in proof loop

### Observed facts

- **Evidence dirs:** `artifacts/local_apply_proofs/20260303T234534Z_32194c` (apply_and_proof), `artifacts/local_apply_proofs/20260303T234948Z_soma_2cccfa` (soma proof)
- **soma_remote.log:** Only 2 lines (bash_version); `soma_run_to_done_result.json` absent — proof loop failed before writing result
- **run_poll.json** (from 20260303T234225Z_soma_477ce4): `artifact_dir: "artifacts/hostd/20260303_234225_e5a591bb"` — hostd dir, not run_to_done
- **precheck_browse.json:** `{"ok":false,"error":"Forbidden"}` — browse to hostd path returns 403/404; PRECHECK never successfully fetched
- **missing_path:** Browse requests `hostd/<run_id>/PROOF.json` and `hostd/<run_id>/PRECHECK.json` — neither exists under hostd; PROOF/PRECHECK live in `artifacts/soma_kajabi/run_to_done/<run_id>/`
- **origin:** `aiops_soma_run_to_done.sh` lines 167–220: uses `artifact_dir` from `/api/runs` (hostd) to build browse path; `parse_run_poll_response` returns hostd dir; Python `Path(sys.argv[1]).read_text()` in browse-parse block reads curl output file (exists); the **conceptual** FileNotFoundError is the browse API 404 for the wrong path

### Layer classification: **A) Remote helper browse path incorrect**

- `artifact_dir` from `/api/runs` for `soma_run_to_done` is hostd (`artifacts/hostd/YYYYMMDD_HHMMSS_hex`); hostd never writes PROOF.json or PRECHECK.json
- `soma_run_to_done.py` on VPS writes PROOF/PRECHECK to `artifacts/soma_kajabi/run_to_done/<run_id>/`; that dir is not returned by `/api/runs` for this action
- `precheck_rel_path` / `proof_rel_path` are derived from hostd `artifact_dir` → browse always 404 → `novnc_readiness_artifact_dir` never populated

### Minimal fix plan (1–2 edits)

1. **aiops_soma_run_to_done.sh:** When `artifact_dir` from run is hostd (`artifacts/hostd/*`), resolve the run_to_done dir instead:
   - Option A: Try `artifacts/soma_kajabi/run_to_done/<run_id>` using Console run_id → run_to_done run_id mapping (timestamp match: `20260303234225` → `run_to_done_20260303T234225Z_*`)
   - Option B: Add a fallback browse: if PROOF/PRECHECK 404 on hostd path, list `artifacts/soma_kajabi/run_to_done/` (or call an API) and try latest run_to_done dir by timestamp
2. **Alternative:** Enhance `/api/runs` (or `soma-last-run-resolver`) so that for `soma_run_to_done`, `artifact_dir` points to `artifacts/soma_kajabi/run_to_done/<d>` when that dir exists and matches the run; then aiops script needs no change

### Verification plan

1. Rerun: `./ops/remote/aiops_soma_run_to_done.sh`
2. **PASS criteria:**
   - `terminal_status` is FAIL with `NOVNC_*` (or SUCCESS/WAITING_FOR_HUMAN) — **not** FileNotFoundError or missing result file
   - `soma_run_to_done_result.json` exists and includes `novnc_readiness_artifact_dir` when precheck fails (NOVNC_NOT_READY)
   - Browse for PRECHECK.json returns 200 when precheck failed (path = run_to_done, not hostd)
3. **Exact verification commands:**
   ```bash
   ./ops/remote/aiops_soma_run_to_done.sh
   # Inspect latest proof dir:
   PROOF_DIR=$(ls -td artifacts/local_apply_proofs/*_soma_* 2>/dev/null | head -1)
   cat "$PROOF_DIR/soma_run_to_done_result.json" | jq '.terminal_status, .novnc_readiness_artifact_dir'
   # Expect: terminal_status in (SUCCESS|FAIL|WAITING_FOR_HUMAN); novnc_readiness_artifact_dir present when FAIL with NOVNC_*
   ```
