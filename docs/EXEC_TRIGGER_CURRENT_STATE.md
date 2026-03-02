# Exec Trigger Client — Platform Invariant

*Last updated: 2026-03-02.*

---

## Overview

`ops/lib/exec_trigger.py` is the **single source of truth** for how Python CLI / ops scripts trigger HQ exec actions via `POST /api/exec`.

Every project lane (Soma, pred_markets, system utilities, future projects) **must** use the shared `trigger_exec()` helper instead of inline HTTP POST calls with ad-hoc timeout and status-code handling.

---

## API

```python
from ops.lib.exec_trigger import trigger_exec, hq_request, TriggerResult

# Trigger an exec action
result: TriggerResult = trigger_exec(
    project="soma_kajabi",
    action="soma_kajabi_auto_finish",
    payload=None,          # optional extra fields merged into POST body
    timeout=None,          # defaults to DEFAULT_TRIGGER_TIMEOUT (90s)
)

# Low-level HQ request (for GET calls, polling, lock checks, etc.)
status_code, body = hq_request("GET", "/api/runs?id=abc", timeout=15)
```

### TriggerResult

| Field         | Type            | Description                                  |
|---------------|-----------------|----------------------------------------------|
| `status_code` | `int`           | HTTP status code (or `-1` for network error) |
| `state`       | `str`           | `ACCEPTED`, `ALREADY_RUNNING`, or `FAILED`   |
| `message`     | `str`           | Human-readable summary with diagnostics      |
| `run_id`      | `str` or `None` | HQ run_id (or active_run_id for 409)         |
| `body`        | `dict`          | Full parsed response body                    |

---

## Default Timeout

**`DEFAULT_TRIGGER_TIMEOUT = 90` seconds.**

### Rationale

HQ's `exec/route.ts` probes hostd connectivity with backoff: 10 s → 20 s → 40 s (total ~70 s) before returning 502 "HOSTD_UNREACHABLE". A trigger client timeout shorter than this window causes `TRIGGER_FAILED` while HQ is still probing — a false negative.

90 s provides the full backoff window plus margin for network latency.

### Invariant

A test in `ops/tests/test_exec_trigger.py` asserts `DEFAULT_TRIGGER_TIMEOUT >= 60`. This test will fail if someone "optimizes" the timeout back down to an unsafe value.

---

## 409 — ALREADY_RUNNING Semantics

When HQ returns HTTP 409, it means the requested action already has an active run (action lock held). The exec trigger client classifies this as:

```
state = "ALREADY_RUNNING"
```

This is **non-fatal**. Consumer scripts should:

- Print a clear message: "Run already in progress for project=X. Not starting a second run."
- Exit with code 0 (or neutral) — **not** TRIGGER_FAILED.
- Optionally include `active_run_id` so the user/UI can join the existing run via `/api/runs?id=<active_run_id>`.

---

## Status Code Classification

| HTTP Status | TriggerResult.state | Meaning                                      |
|-------------|---------------------|----------------------------------------------|
| 200         | `ACCEPTED`          | Action completed synchronously               |
| 202         | `ACCEPTED`          | Action dispatched; poll `/api/runs?id=` for status |
| 409         | `ALREADY_RUNNING`   | Active run exists; non-fatal                 |
| 502         | `FAILED`            | Hostd unreachable after backoff              |
| 4xx / 5xx   | `FAILED`            | Other server error                           |
| -1          | `FAILED`            | Network error / timeout (client-side)        |

---

## Migrated Scripts

| Script                               | Project       | Action(s) Triggered                |
|--------------------------------------|---------------|------------------------------------|
| `ops/scripts/soma_run_to_done.py`    | soma_kajabi   | `soma_kajabi_auto_finish`          |
| `ops/scripts/soma_fix_and_retry.py`  | soma_kajabi   | `soma_run_to_done`, `openclaw_novnc_shm_fix`, `openclaw_novnc_restart` |
| `ops/scripts/soma_autopilot_tick.py` | soma_kajabi   | `soma_run_to_done`, `openclaw_novnc_shm_fix`, `openclaw_novnc_restart` |
| `ops/scripts/soma_novnc_oneclick_recovery.py` | soma_kajabi | `soma_run_to_done`          |

---

## Adding a New Project / Action

When creating a new ops script that triggers an HQ action:

1. Import the shared client:
   ```python
   from ops.lib.exec_trigger import trigger_exec
   ```
2. Call `trigger_exec(project="your_project", action="your_action")`.
3. Handle the three states:
   - `ACCEPTED` — proceed to poll or report success.
   - `ALREADY_RUNNING` — report "already in progress", do not error.
   - `FAILED` — report TRIGGER_FAILED with `result.message` (includes diagnostics).
4. Do **not** hard-code a timeout below 60 s unless you have a documented reason.
5. Do **not** define a local `_curl()` function — use `hq_request()` for non-trigger HTTP calls.

---

## Related Docs

- `docs/SOMA_PIPELINE_CURRENT.md` — Soma end-to-end flow
- `docs/CSR_BRIEF_SOMA.md` — Soma issues and fix history
- `docs/SOMA_INCIDENT_20260302004627-7010.md` — The 5 s timeout incident that motivated this client
- `apps/openclaw-console/src/app/api/exec/route.ts` — HQ exec route (hostd probe backoff)
