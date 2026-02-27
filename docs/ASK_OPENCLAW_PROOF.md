# Ask OpenClaw Proof — Example Q&As with Citations

Runtime proof artifacts are written to `artifacts/hq_proofs/ask_openclaw/<run_id>/PROOF.md`.

## Example 1: "What's broken?"

**Question:** What's broken?

**Answer:** State pack loaded. Check artifacts: artifacts/system/state_pack/run_id/health_public.json, artifacts/system/state_pack/run_id/autopilot_status.json, artifacts/system/state_pack/run_id/SUMMARY.md. For failures, run doctor or openclaw_hq_audit.

**Citations (paths only):**
- artifacts/system/state_pack/run_id/health_public.json
- artifacts/system/state_pack/run_id/autopilot_status.json
- artifacts/system/state_pack/run_id/SUMMARY.md

---

## Example 2: "Why is Soma waiting?"

**Question:** Why is Soma waiting?

**Answer:** State pack loaded. Check autopilot_status.json and latest_runs_index.json for Soma status.

**Citations (paths only):**
- artifacts/system/state_pack/run_id/autopilot_status.json
- artifacts/system/state_pack/run_id/latest_runs_index.json
- artifacts/system/state_pack/run_id/SUMMARY.md

---

## Example 3: "Is noVNC reachable?"

**Question:** Is noVNC reachable?

**Answer:** State pack loaded. Check tailscale_serve.txt and ports.txt in run_id. noVNC typically on port 6080.

**Citations (paths only):**
- artifacts/system/state_pack/run_id/tailscale_serve.txt
- artifacts/system/state_pack/run_id/ports.txt
- artifacts/system/state_pack/run_id/SUMMARY.md

---

## Invariants

- Every answer includes citations[] (artifact paths)
- /api/ask returns 422 when citations[] is empty
- recommended_next_action is always an OCL TaskRequest (read-only by default)
- No secrets in outputs; sensitive fields redacted
