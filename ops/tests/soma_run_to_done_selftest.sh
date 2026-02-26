#!/usr/bin/env bash
# Selftest: soma_run_to_done action registered, script exists, async exec routes present.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "==> soma_run_to_done_selftest"

# 1. Action in action_registry
python3 -c "
import json
with open('config/action_registry.json') as f:
    d = json.load(f)
ids = [a['id'] for a in d['actions']]
assert 'soma_run_to_done' in ids, 'soma_run_to_done not in action_registry'
a = next(x for x in d['actions'] if x['id'] == 'soma_run_to_done')
assert a.get('project_id') == 'soma_kajabi'
assert 'soma_run_to_done.py' in a.get('cmd_template', '')
print('  PASS: action in action_registry')
"

# 2. Script exists
[ -f "$ROOT_DIR/ops/scripts/soma_run_to_done.py" ] || { echo "  FAIL: script missing"; exit 1; }
echo "  PASS: script exists"

# 3. LONG_RUNNING_ACTIONS includes soma_run_to_done
grep -q 'soma_run_to_done' "$ROOT_DIR/apps/openclaw-console/src/lib/hostd.ts" || { echo "  FAIL: soma_run_to_done not in LONG_RUNNING_ACTIONS"; exit 1; }
echo "  PASS: soma_run_to_done in LONG_RUNNING_ACTIONS"

# 4. POST /api/exec/start route exists
[ -f "$ROOT_DIR/apps/openclaw-console/src/app/api/exec/start/route.ts" ] || { echo "  FAIL: exec/start route missing"; exit 1; }
echo "  PASS: POST /api/exec/start route exists"

# 5. GET /api/projects/[projectId]/status route exists
[ -f "$ROOT_DIR/apps/openclaw-console/src/app/api/projects/[projectId]/status/route.ts" ] || { echo "  FAIL: projects status route missing"; exit 1; }
echo "  PASS: GET /api/projects/soma_kajabi/status route exists"

# 6. Run record supports status running/queued
grep -q '"running"\|"queued"' "$ROOT_DIR/apps/openclaw-console/src/lib/run-recorder.ts" || { echo "  FAIL: RunRecord status running/queued not in run-recorder"; exit 1; }
echo "  PASS: RunRecord supports running/queued status"

echo "==> soma_run_to_done_selftest PASS"
