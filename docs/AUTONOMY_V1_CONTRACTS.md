# OpenClaw Autonomy Max v1 Contracts
Scope: shared integration contract for observe-only v1. Changes require integrator approval.

## 1. API Contracts
### GET /api/ui/autonomy_mode
200:
```json
{"ok":true,"mode":"OFF","updated_at":"2026-03-07T10:15:00.000Z","updated_by":"integrator","path":"artifacts/system/autonomy_mode.json"}
```
### POST /api/ui/autonomy_mode
Request:
```json
{"mode":"OFF"}
```
200:
```json
{"ok":true,"mode":"OFF","updated_at":"2026-03-07T10:15:00.000Z","updated_by":"operator:local","path":"artifacts/system/autonomy_mode.json"}
```
Contract: fresh bootstrap defaults to `OFF`; for autonomous runners `OFF` means observe-only.

### GET /api/ui/inbox_summary
200:
```json
{"ok":true,"autonomy_mode":{"mode":"OFF","updated_at":"2026-03-07T10:15:00.000Z","updated_by":"integrator"},"canary_core_status":"PASS","canary_optional_status":"WARN","projects":[{"project_id":"soma_kajabi","name":"Soma Kajabi","description":"Human-gated funnel operations.","autonomy_mode":"OFF","core_status":"PASS","optional_status":"WARN","needs_human":false,"approvals_pending":1,"last_run":{"run_id":"run_20260307T101500Z","action":"noop.review_approvals","status":"SUCCESS","finished_at":"2026-03-07T10:15:20.000Z","artifact_dir":"artifacts/system/playbook_runs/playbook_20260307T101500Z"},"proof_links":[{"label":"Last run proof","href":"/api/artifacts/system/playbook_runs/playbook_20260307T101500Z"}],"cards":[{"id":"approval_20260307T101500Z","type":"APPROVAL_REQUIRED","title":"Resume Kajabi publish","summary":"Operator approval required.","project_id":"soma_kajabi","approval_id":"approval_20260307T101500Z","proof_links":[],"tone":"warn"}],"widgets":[],"playbooks":[],"recommended_playbook":{"id":"soma.review_approvals","title":"Review approvals","rationale":"Resolve pending gates first.","expected_outputs":["approval_resolution.json"]},"business_dod_pass":true}]}
```

### POST /api/ui/playbooks/run
Request:
```json
{"project_id":"soma_kajabi","playbook_id":"soma.resume_publish","user_role":"admin"}
```
202 example:
```json
{"ok":true,"status":"APPROVAL_REQUIRED","playbook_run_id":"playbook_20260307T101900Z","proof_bundle":"artifacts/system/playbook_runs/playbook_20260307T101900Z","proof_bundle_url":"/api/artifacts/system/playbook_runs/playbook_20260307T101900Z","approval_id":"approval_20260307T101901Z","message":"Approval required before execution.","policy":{"decision":"APPROVAL","reason":"This playbook requires explicit operator approval before execution.","required_approval":true,"allowed":false,"guardrails":{"autonomy_mode":"OFF","read_only_allowed":false}}}
```
Contract: read-only playbooks may return `RUNNING`, `JOINED_EXISTING_RUN`, or `REVIEW_READY`; mutating playbooks selected during observe-only scheduler ticks are recorded but not executed.

### GET /api/ui/approvals
200:
```json
{"ok":true,"approvals":[{"id":"approval_20260307T101901Z","project_id":"soma_kajabi","playbook_id":"soma.resume_publish","playbook_title":"Resume Kajabi publish","primary_action":"soma_run_to_done","status":"PENDING","rationale":"Operator approval required.","created_at":"2026-03-07T10:19:01.000Z","created_by":"operator:local","resolved_at":null,"resolved_by":null,"note":null,"proof_bundle":"artifacts/system/playbook_runs/playbook_20260307T101900Z","proof_bundle_url":"/api/artifacts/system/playbook_runs/playbook_20260307T101900Z","request_path":"artifacts/system/approvals/approval_20260307T101901Z/request.json","request_url":"/api/artifacts/system/approvals/approval_20260307T101901Z/request.json","resolution_path":null,"resolution_url":null,"policy_decision":"APPROVAL","autonomy_mode":"OFF","run_id":null}]}
```

### POST /api/ui/approvals/{id}/approve
Request:
```json
{"note":"Approved by operator","user_role":"admin"}
```
200:
```json
{"ok":true,"approval":{"id":"approval_20260307T101901Z","status":"APPROVED","resolved_at":"2026-03-07T10:21:00.000Z","resolved_by":"operator:local","note":"Approved by operator","run_id":"run_20260307T102100Z"},"run":{"ok":true,"status":"RUNNING","playbook_run_id":"playbook_20260307T101900Z","proof_bundle":"artifacts/system/playbook_runs/playbook_20260307T101900Z","run_id":"run_20260307T102100Z"}}
```

### POST /api/ui/approvals/{id}/reject
Request:
```json
{"note":"Blocked pending evidence"}
```
200:
```json
{"ok":true,"approval":{"id":"approval_20260307T101901Z","status":"REJECTED","resolved_at":"2026-03-07T10:22:00.000Z","resolved_by":"operator:local","note":"Blocked pending evidence","run_id":null}}
```

## 2. Artifact Contracts
### artifacts/system/autonomy_scheduler/<run_id>/tick_summary.json
```json
{"run_id":"tick_20260307T103000Z","started_at":"2026-03-07T10:30:00.000Z","finished_at":"2026-03-07T10:30:04.000Z","autonomy_mode":"OFF","observe_only":true,"projects_considered":4,"decisions_written":6,"executed_written":2,"mutating_candidates_blocked":3,"latest_paths":{"tick_summary":"artifacts/system/autonomy_scheduler/LATEST_tick_summary.json","decisions":"artifacts/system/autonomy_scheduler/LATEST_decisions.jsonl","executed":"artifacts/system/autonomy_scheduler/LATEST_executed.jsonl"}}
```
### decisions.jsonl record schema
```json
{"ts":"2026-03-07T10:30:01.000Z","tick_run_id":"tick_20260307T103000Z","project_id":"soma_kajabi","playbook_id":"soma.resume_publish","primary_action":"soma_run_to_done","mutates_external":true,"policy_decision":"APPROVAL","eligible_to_execute":false,"observe_only_blocked":true,"reason":"Mutating task recorded but not executed while observe-only.","proof_bundle":"artifacts/system/playbook_runs/playbook_20260307T103001Z"}
```
### executed.jsonl record schema
```json
{"ts":"2026-03-07T10:30:02.000Z","tick_run_id":"tick_20260307T103000Z","project_id":"infra","playbook_id":"infra.review_approvals","primary_action":"noop.review_approvals","mutates_external":false,"result_status":"REVIEW_READY","run_id":null,"proof_bundle":"artifacts/system/playbook_runs/playbook_20260307T103002Z"}
```
### approvals/<id>/request.json + resolution.json
```json
{"id":"approval_20260307T101901Z","project_id":"soma_kajabi","playbook_id":"soma.resume_publish","playbook_title":"Resume Kajabi publish","primary_action":"soma_run_to_done","status":"PENDING","rationale":"Operator approval required.","created_at":"2026-03-07T10:19:01.000Z","created_by":"operator:local","proof_bundle":"artifacts/system/playbook_runs/playbook_20260307T101900Z","policy_decision":"APPROVAL","autonomy_mode":"OFF"}
{"status":"APPROVED","resolved_at":"2026-03-07T10:21:00.000Z","resolved_by":"operator:local","note":"Approved by operator","run_id":"run_20260307T102100Z"}
```
### transitions/<project_id>.json
```json
{"project_id":"soma_kajabi","updated_at":"2026-03-07T10:21:00.000Z","state_hash":"sha256:abc123","event_type":"APPROVAL_RESOLVED","summary":"Resume Kajabi publish approved","proof_path":"artifacts/system/playbook_runs/playbook_20260307T101900Z","hq_path":"/inbox","notification_channels":["hq","discord"]}
```

## 3. Observe-Only Rules
1. Scheduler/autopilot ticks MUST treat `autonomy_mode=OFF` as observe-only by default.
2. Read-only tasks may execute.
3. Mutating tasks MUST appear in `decisions.jsonl`.
4. Mutating tasks MUST NOT appear in `executed.jsonl` while observe-only.
5. Approval creation is allowed while observe-only because it records intent, not external mutation.
6. Any exception requires integrator-approved contract revision.

## 4. Acceptance Checks
1. Golden tick produces `tick_summary.json`, `decisions.jsonl`, `executed.jsonl`, and matching `LATEST_*` pointers under `artifacts/system/autonomy_scheduler/`.
2. Golden tick in observe-only shows at least one mutating candidate in `decisions.jsonl` and zero matching mutating entries in `executed.jsonl`.
3. UI default route is Inbox (`/` redirects to `/inbox`).
4. Soma page shows no more than 5 visible action buttons by default.
