# Tier-2 Confirmation Contract

The Tier-2 confirmation harness validates top-K candidates from Tier-1 bulk backtest screening by running them through NinjaTrader 8's Strategy Analyzer or Walk-Forward Optimizer with deterministic, auditable artifact output.

## topk.json Schema

The harness consumes a single `topk.json` file per candidate. The schema is canonical and enforced by `schemas/topk.schema.json`.

### Required Fields

| Field | Type | Description |
|---|---|---|
| `candidate_id` | string | Stable unique identifier (`^[A-Za-z0-9_-]+$`). Must be idempotent across re-runs. |
| `strategy_name` | string | Exact NinjaTrader strategy class name. |
| `strategy_version` | string | Strategy version (e.g. `"1.0.3"`). |
| `instrument` | string | NinjaTrader instrument (e.g. `"NQ 03-26"`). |
| `timeframe` | string | Bar period (e.g. `"5 Min"`). |
| `date_ranges` | array | One or more `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}` objects. |
| `sessions` | string | NinjaTrader session template name. |
| `params` | object | Strategy parameters, keyed by **exact case-sensitive** property name. Each value is `{"type": "<int|double|bool|string>", "value": <typed>}`. |
| `fees_slippage` | object | `{"commission_per_side": <number>, "slippage_ticks": <int>}` |
| `BACKTEST_ONLY` | boolean | **Must be `true`**. Fail-closed gate. |

### Example

```json
{
  "candidate_id": "orb-nq-v103-001",
  "strategy_name": "ORBStrategy",
  "strategy_version": "1.0.3",
  "instrument": "NQ 03-26",
  "timeframe": "5 Min",
  "date_ranges": [
    {"start": "2025-01-02", "end": "2025-06-30"},
    {"start": "2025-07-01", "end": "2025-12-31"}
  ],
  "sessions": "CME US Index Futures RTH",
  "params": {
    "OrbMinutes": {"type": "int", "value": 30},
    "ProfitTarget": {"type": "double", "value": 50.0},
    "UseTrailing": {"type": "bool", "value": true}
  },
  "fees_slippage": {
    "commission_per_side": 2.05,
    "slippage_ticks": 2
  },
  "BACKTEST_ONLY": true
}
```

## Output Artifacts

All artifacts are written under `<output_dir>/tier2/`:

```
tier2/
├── results.csv        # One row per candidate with normalized metrics
├── summary.json       # Metadata, verdict (PASS/FAIL), reasons, best_candidate
├── raw_exports/       # Copy-through of raw NT8 export files
└── done.json          # Run completion marker
```

### results.csv Columns

| Column | Description |
|---|---|
| `candidate_id` | Matches topk.json candidate_id |
| `pnl` | Net profit/loss |
| `pf` | Profit factor |
| `sharpe` | Sharpe ratio |
| `max_dd` | Maximum drawdown |
| `trades` | Total trade count |
| `winrate` | Win rate (0-1) |
| `avg_trade` | Average trade P&L |
| `expectancy` | Expected value per trade |
| `profit_factor` | Gross profit / gross loss |
| `time_in_market` | Fraction of time with open position (if available) |

### summary.json

```json
{
  "schema_version": "tier2_summary.v2",
  "run_id": "t2-orb-nq-v103-001-a1b2c3d4e5f6",
  "candidate_id": "orb-nq-v103-001",
  "verdict": "PASS|FAIL|NT8_AUTOMATION_NOT_IMPLEMENTED",
  "reasons": ["..."],
  "best_candidate": "orb-nq-v103-001",
  "error_class": "..."
}
```

### done.json

```json
{
  "done": true,
  "run_id": "t2-orb-nq-v103-001-a1b2c3d4e5f6",
  "candidate_id": "orb-nq-v103-001",
  "status": "PASS|FAIL|NT8_AUTOMATION_NOT_IMPLEMENTED",
  "exit_code": 0,
  "started_at": "2026-02-18T12:00:00+00:00",
  "finished_at": "2026-02-18T12:05:00+00:00"
}
```

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Confirmation completed, candidate confirmed |
| `1` | Validation error or gate failure (artifacts written with details) |
| `2` | Usage error (bad arguments) |
| `3` | NT8_AUTOMATION_NOT_IMPLEMENTED (Phase-0 stub; artifact skeleton produced) |

## Idempotency Rules

1. **`candidate_id` is the primary key.** The `run_id` is deterministically derived from `candidate_id` + `output_dir`, so re-runs produce the same `run_id`.
2. **Re-runs overwrite artifacts in-place.** The harness writes atomically within `tier2/`; it never appends or creates duplicate subdirectories.
3. **`done.json` is the completion marker.** Consumers should check for `done.json` existence and `done === true` before reading other artifacts.

## Fail-Closed Gates

The harness enforces two independent backtest-only checks:

1. **Schema gate**: `BACKTEST_ONLY` must be `true` in `topk.json` (enforced as `const: true`).
2. **Environment gate**: The `BACKTEST_ONLY` environment variable must be set to `"true"`.
3. **NT8 connection scan** (optional): If `--nt8-user-dir` is provided and a `connections.xml` exists, the harness checks that only simulated/playback connections are configured. If it cannot determine the connection state, it fails closed with `LIVE_CONNECTIONS_UNKNOWN`.

## Invocation

### From the Windows executor (or any host):

```bash
export BACKTEST_ONLY=true

python -m tools.tier2_confirm_entrypoint \
    --topk /path/to/topk.json \
    --output-dir /path/to/artifacts/backtests/run-001 \
    --mode strategy_analyzer \
    --nt8-user-dir "$HOME/Documents/NinjaTrader 8"
```

### Collecting artifacts:

```bash
# Check completion
cat /path/to/artifacts/backtests/run-001/tier2/done.json

# Read verdict
cat /path/to/artifacts/backtests/run-001/tier2/summary.json

# Process results
cat /path/to/artifacts/backtests/run-001/tier2/results.csv
```

### Validation only (no execution):

```bash
python -m tools.validate_topk /path/to/topk.json
```

## Phase-0 Stub Behavior

When full NT8 automation is not yet implemented, the harness:

1. Validates `topk.json` (full schema + typed params + case-sensitive checks)
2. Enforces the backtest-only gate
3. Writes the complete artifact skeleton with `verdict: "NT8_AUTOMATION_NOT_IMPLEMENTED"`
4. Exits with code `3`

This ensures the contract is exercised end-to-end and downstream consumers can integrate against the real artifact structure immediately.
