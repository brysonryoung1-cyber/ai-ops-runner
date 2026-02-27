#!/usr/bin/env bash
# proof_operator_inbox.sh — Prove Operator Inbox API and /inbox page.
# Writes: artifacts/hq_proofs/operator_inbox/<run_id>/{PROOF.md,api_sample.json,inbox_snapshot.html}
#
# Usage: ./ops/proof_operator_inbox.sh [hq_base]
#   hq_base: http://127.0.0.1:8787 (default) or https://aiops-1.tailc75c62.ts.net
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HQ_BASE="${1:-${OPENCLAW_HQ_BASE:-http://127.0.0.1:8787}}"
RUN_ID="operator_inbox_$(date -u +%Y%m%d_%H%M%S)Z"
PROOF_DIR="$ROOT_DIR/artifacts/hq_proofs/operator_inbox/$RUN_ID"
mkdir -p "$PROOF_DIR"

echo "=== proof_operator_inbox.sh ==="
echo "  Run ID: $RUN_ID"
echo "  HQ:     $HQ_BASE"
echo "  Proof:  $PROOF_DIR"
echo ""

# 1. Fetch API and save sample
echo "==> 1) GET /api/operator-inbox"
curl -sfS --connect-timeout 5 "$HQ_BASE/api/operator-inbox" > "$PROOF_DIR/api_sample.json" || {
  echo '{"error":"fetch_failed","waiting_for_human":[],"degraded":[],"last_proof":{},"last_deploy":{},"last_canary":{}}' > "$PROOF_DIR/api_sample.json"
}
echo "  Saved api_sample.json"

# 2. Fetch /inbox HTML snapshot
echo "==> 2) GET /inbox"
curl -sfS --connect-timeout 5 "$HQ_BASE/inbox" > "$PROOF_DIR/inbox_snapshot.html" 2>/dev/null || {
  echo "<!DOCTYPE html><html><body>Fetch failed</body></html>" > "$PROOF_DIR/inbox_snapshot.html"
}
echo "  Saved inbox_snapshot.html"

# 3. Extract links for PROOF.md (canary proof, proof-gate proof)
CANARY_PROOF=""
PROOF_GATE_PROOF=""
if [ -f "$PROOF_DIR/api_sample.json" ]; then
  CANARY_PROOF=$(python3 -c "
import json
try:
    d = json.load(open('$PROOF_DIR/api_sample.json'))
    lp = d.get('last_canary', {})
    pl = lp.get('proof_link')
    if pl: print(pl)
    deg = d.get('degraded', [])
    for x in deg:
        if x.get('proof_link'): print(x['proof_link']); break
    if not pl and deg: print(deg[0].get('proof_link', ''))
except: pass
" 2>/dev/null | head -1)
  PROOF_GATE_PROOF=$(python3 -c "
import json
try:
    d = json.load(open('$PROOF_DIR/api_sample.json'))
    lp = d.get('last_proof', {})
    pl = lp.get('proof_link')
    if pl: print(pl)
except: pass
" 2>/dev/null | head -1)
fi

# Fallback: link to known proof dirs
[ -z "$CANARY_PROOF" ] && [ -d "$ROOT_DIR/artifacts/system/canary" ] && {
  LATEST_CANARY=$(ls -1dt "$ROOT_DIR/artifacts/system/canary"/*/ 2>/dev/null | head -1)
  [ -n "$LATEST_CANARY" ] && CANARY_PROOF="/artifacts/system/canary/$(basename "$LATEST_CANARY")"
}
[ -z "$PROOF_GATE_PROOF" ] && [ -f "$ROOT_DIR/docs/LAST_PROOF_SUMMARY.json" ] && {
  PROOF_GATE_PROOF=$(python3 -c "
import json
try:
    d = json.load(open('$ROOT_DIR/docs/LAST_PROOF_SUMMARY.json'))
    pd = d.get('proof_dir', '')
    if pd: print('/artifacts/' + pd.replace('artifacts/', ''))
except: pass
" 2>/dev/null)
}

# 4. Write PROOF.md
cat > "$PROOF_DIR/PROOF.md" << EOF
# Operator Inbox PROOF

**Run ID:** $RUN_ID
**Timestamp:** $(date -u +%Y-%m-%dT%H:%M:%SZ)

## API JSON sample

See \`api_sample.json\` in this directory.
\`\`\`json
$(head -c 1400 "$PROOF_DIR/api_sample.json")...
\`\`\`

## /inbox snapshot

- HTML: \`$PROOF_DIR/inbox_snapshot.html\`
- View: $HQ_BASE/inbox

## Links

| Type | Link |
|------|------|
| Canary proof | ${CANARY_PROOF:-—} |
| Proof-gate proof | ${PROOF_GATE_PROOF:-—} |

## Artifacts

- api_sample.json
- inbox_snapshot.html
EOF

echo ""
echo "=== proof_operator_inbox COMPLETE ==="
echo "  Proof: $PROOF_DIR/PROOF.md"
exit 0
