# Soma Autopilot (Doctor-Gated, 0-LLM)

## Purpose

`ops/system/project_autopilot.py` is a deterministic project lane for `soma_kajabi`.

It runs `soma_run_to_done` end-to-end via localhost HQ APIs and stops at terminal states:

- `SUCCESS`
- `WAITING_FOR_HUMAN`
- `FAIL`

No LLM calls are used.

## Contract

- **Fail-closed gate:** runs Doctor Matrix first (`python3 ops/system/doctor_matrix.py --mode core`) and requires `PASS`.
- **Least privilege:** uses localhost HQ endpoints (`http://127.0.0.1:8787`) on aiops-1.
- **Bounded runtime:** hard cap via `--max-seconds` (default `2100`).
- **Bounded polling:** backoff interval range via `--poll-interval` (default `6..24` seconds).
- **No secrets:** artifacts and Discord alerts never include tokens/webhook URLs.

## Runtime Flow

1. Doctor Matrix core gate (`PASS` required).
2. Trigger `POST /api/exec` action `soma_run_to_done`.
3. Poll `GET /api/runs?id=<run_id>` until terminal (or runtime cap).
4. Resolve run-to-done artifacts (`PROOF.json`, `PRECHECK.json`) when available.
5. Optional deterministic validators (if action exists): `soma_kajabi_verify_business_dod`.

## WAITING_FOR_HUMAN Behavior

On `WAITING_FOR_HUMAN`:

- Sends Discord alert (deduped by `project + terminal_status + error_class + run_id`) with:
  - pinned noVNC URL
  - `run_id`
  - proof path
- Exits `0` so systemd timer run is not marked failed.

## FAIL Behavior

On `FAIL`:

- Sends Discord alert (deduped key above) with `error_class` and proof path.
- Exits non-zero.

## Artifacts

Per run bundle:

`artifacts/system/project_autopilot/<run_id>/`

Contains:

- `RESULT.json`
- `SUMMARY.md`
- `raw/` (trigger/poll/browse responses)
- `run_to_done_PROOF.json` (if available)
- `run_to_done_PRECHECK.json` (if available)

## Systemd Units

- Service: `ops/systemd/openclaw-soma-autopilot.service`
- Timer: `ops/systemd/openclaw-soma-autopilot.timer`
- Cadence: every 30 minutes with randomized delay (`RandomizedDelaySec=300`)

## Manual Run

```bash
python3 ops/system/project_autopilot.py --project soma_kajabi --action soma_run_to_done
```
