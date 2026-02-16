# Deploy/Ship Pipeline — Implementation Proof (Redacted)

## Real Artifacts Produced

- **Ship (host guard)**  
  `artifacts/ship/20260216_225708-0000a44b/ship_result.json`  
  - `overall`: FAIL  
  - `error_class`: host_guard_env  
  - `step_failed`: preflight  
  - Confirms ship_pipeline refuses when `OPENCLAW_PRODUCTION=1`.

- **Deploy (pull-only assert)**  
  `artifacts/deploy/20260216_225809-0000467f/deploy_result.json`  
  - `overall`: FAIL  
  - `error_class`: push_capability_detected  
  - `step_failed`: assert_production_pull_only  
  - Confirms deploy_pipeline runs assert_production_pull_only; on this dev machine credential helper triggered FAIL (expected). On aiops-1 (no write creds) assert passes and deploy continues.

## Confirmation Checklist

| Item | Status |
|------|--------|
| Real `artifacts/ship/<run_id>/ship_result.json` | Yes (20260216_225708-0000a44b) |
| Real `artifacts/deploy/<run_id>/deploy_result.json` | Yes (20260216_225809-0000467f) |
| `/project/state` with deploy timestamp + PASS | After deploy_and_verify PASS on aiops-1 |
| HQ Deploy+Verify shows last run + artifact path | Implemented (API deploy/last, page) |
| aiops-1 pull-only (assert_production_pull_only) | In deploy_pipeline; PASS when no push creds |
| No public ports (doctor audit) | Unchanged; verify_production enforces |

## Next Step for Full PASS

Run **Deploy+Verify** from HQ on aiops-1 (with `OPENCLAW_ADMIN_TOKEN` set). That will:

1. Run `ops/deploy_pipeline.sh` on the host  
2. Pass assert_production_pull_only (no credential helper / write keys on VPS)  
3. Update `/project/state` and write PASS artifacts under `artifacts/deploy/<run_id>/`

No secrets in any artifact; verdict/IDs redacted where used.
