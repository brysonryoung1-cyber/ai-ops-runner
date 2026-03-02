#!/usr/bin/env bash
# csr_human_gate_check.sh — Check if a human gate login window is active.
#
# Usage: csr_human_gate_check.sh [project_id]
# Exit 0 if gate ACTIVE (not expired). Exit 1 otherwise.
# Avoids jq: uses python3 + ops/lib/human_gate.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_ID="${1:-soma_kajabi}"

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$ROOT_DIR"

python3 -c "
from ops.lib.human_gate import read_gate
import json, sys
result = read_gate('$PROJECT_ID')
if result.get('active'):
    gate = result['gate']
    print(json.dumps({'active': True, 'expires_at': gate.get('expires_at', ''), 'run_id': gate.get('run_id', ''), 'reason': gate.get('reason', '')}))
    sys.exit(0)
else:
    print(json.dumps({'active': False}))
    sys.exit(1)
"
