# Brain Loop (0-LLM, deterministic)

## What It Does

`ops/system/brain_loop.py` runs Doctor Matrix on a schedule with zero LLM calls.

- Runs Doctor Matrix (`--mode all|core`).
- Builds a per-run proof bundle at `artifacts/system/brain_loop/<run_id>/`.
- Compares current matrix status to prior state.
- Sends Discord alert only on state changes:
  - `PASS_TO_FAIL`
  - `FAIL_TO_PASS`
  - `FAIL_CHECKS_CHANGED`
  - `FIRST_FAIL` (only when first run is failing and first-fail alert is enabled)
- Dedupes repeated alerts using `last_alert_hash`.

No deploys, restarts, triggers, or mutation commands are executed by this loop.

## Proof Bundle

Each run writes:

- `RESULT.json`
- `SUMMARY.md`
- `doctor_matrix_ref.json`

Path:

`artifacts/system/brain_loop/<brain_loop_YYYYMMDDTHHMMSSZ_<hex>>/`

## State File

Default state path:

`/opt/ai-ops-runner/state/brain_loop/last_state.json`

Override for tests/local runs:

`--state-root <path>`

State writes are atomic (tmp file + rename).

## Discord Webhook Secret

Webhook resolution order:

1. `OPENCLAW_DISCORD_WEBHOOK_URL`
2. `/etc/ai-ops-runner/secrets/discord_webhook_url`

Do not commit webhook URLs. They are loaded only at runtime and never written to artifacts.

## Systemd Units

- Service: `ops/systemd/openclaw-brain-loop.service`
- Timer: `ops/systemd/openclaw-brain-loop.timer`
- Cadence: every 15 minutes (`OnUnitActiveSec=15min`)

## Enable / Disable

Enable:

```bash
sudo cp ops/systemd/openclaw-brain-loop.service /etc/systemd/system/
sudo cp ops/systemd/openclaw-brain-loop.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openclaw-brain-loop.timer
sudo systemctl status openclaw-brain-loop.timer --no-pager
```

Disable:

```bash
sudo systemctl disable --now openclaw-brain-loop.timer
```

Manual run:

```bash
python3 ops/system/brain_loop.py --mode all
```

