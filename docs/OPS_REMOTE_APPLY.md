# OPS Remote Apply + Prove (Mac-side)

Run from repo root on your Mac.

## One-command deploy + doctor + prove

```bash
./ops/remote/aiops_apply_and_prove.sh
```

Custom target:

```bash
./ops/remote/aiops_apply_and_prove.sh --host aiops-1 --base-url https://aiops-1.tailc75c62.ts.net --repo-dir /opt/ai-ops-runner
```

Proof bundle written to:

```bash
artifacts/local_apply_proofs/<utc_run_id>/
```

Includes:
- `health_public_before.json`
- `health_public_after.json`
- `compose_ps.txt`
- `compose_logs_tail.txt`
- `remote_actions.log`
- `RESULT.json`

## One-command Soma run-to-done

```bash
./ops/remote/aiops_soma_run_to_done.sh
```

Custom target:

```bash
./ops/remote/aiops_soma_run_to_done.sh --host aiops-1 --base-url https://aiops-1.tailc75c62.ts.net --repo-dir /opt/ai-ops-runner
```

Exit codes:
- `0` = `SUCCESS`
- `2` = `WAITING_FOR_HUMAN` (prints pinned noVNC URL)
- `1` = `FAIL`

Local result artifact:

```bash
artifacts/local_apply_proofs/<utc_run_id>/soma_run_to_done_result.json
```
