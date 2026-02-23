#!/usr/bin/env bash
# E2E-ish selftest: soma_kajabi_auto_finish action is registered and visible in console routes.
# Does NOT run real Kajabi — verifies action_registry, PROJECT_ACTIONS, and script exists.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

echo "==> soma_kajabi_auto_finish_selftest"

# 1. Action in action_registry
python3 -c "
import json
with open('config/action_registry.json') as f:
    d = json.load(f)
ids = [a['id'] for a in d['actions']]
assert 'soma_kajabi_auto_finish' in ids, 'soma_kajabi_auto_finish not in action_registry'
a = next(x for x in d['actions'] if x['id'] == 'soma_kajabi_auto_finish')
assert a.get('project_id') == 'soma_kajabi'
assert 'soma_kajabi_auto_finish.py' in a.get('cmd_template', '')
print('  PASS: action in action_registry')
"

# 2. Script exists
[ -f "$ROOT_DIR/ops/scripts/soma_kajabi_auto_finish.py" ] || { echo "  FAIL: script missing"; exit 1; }
echo "  PASS: script exists"

# 3. PROJECT_ACTIONS includes soma_kajabi_auto_finish for soma_kajabi
python3 -c "
import json
# action_registry.generated.ts is generated; check source
with open('config/action_registry.json') as f:
    d = json.load(f)
soma_actions = [a['id'] for a in d['actions'] if a.get('project_id') == 'soma_kajabi']
assert 'soma_kajabi_auto_finish' in soma_actions
print('  PASS: soma_kajabi_auto_finish in project soma_kajabi')
"

# 4. Daily script and timer exist
[ -f "$ROOT_DIR/ops/scripts/soma_kajabi_auto_finish_daily.sh" ] || { echo "  FAIL: daily script missing"; exit 1; }
[ -f "$ROOT_DIR/ops/systemd/openclaw-soma-auto-finish.service" ] || { echo "  FAIL: systemd service missing"; exit 1; }
[ -f "$ROOT_DIR/ops/systemd/openclaw-soma-auto-finish.timer" ] || { echo "  FAIL: systemd timer missing"; exit 1; }
echo "  PASS: daily script and systemd units exist"

echo "==> soma_kajabi_auto_finish_selftest PASS"
