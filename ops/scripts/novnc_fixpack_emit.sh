#!/usr/bin/env bash
# novnc_fixpack_emit.sh — Generate a fixpack for noVNC failures.
#
# Writes triage.json, runs csr_evidence_bundle.sh, generates CSR_PROMPT.txt,
# and writes ERROR_SUMMARY.txt. All outputs land in the triage_dir.
#
# Usage: novnc_fixpack_emit.sh <triage_dir> <error_class> <failing_step> <recommended_next_action> [artifact_pointer:path ...]
#
# Extra arguments after the fourth are key:path pairs added to artifact_pointers.
# Exit 0 on success, 1 on usage error.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ $# -lt 4 ]; then
  echo "Usage: novnc_fixpack_emit.sh <triage_dir> <error_class> <failing_step> <recommended_next_action> [key:path ...]" >&2
  exit 1
fi

TRIAGE_DIR="$1"
ERROR_CLASS="$2"
FAILING_STEP="$3"
RECOMMENDED="$4"
shift 4

mkdir -p "$TRIAGE_DIR"

# Build artifact_pointers from remaining args (key:path pairs)
POINTERS_JSON="{}"
for arg in "$@"; do
  key="${arg%%:*}"
  val="${arg#*:}"
  POINTERS_JSON="$(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
d[sys.argv[2]] = sys.argv[3]
print(json.dumps(d))
" "$POINTERS_JSON" "$key" "$val")"
done

# Determine retryable from error class
RETRYABLE="true"
case "$ERROR_CLASS" in
  NOVNC_DOCTOR_MISSING|CONFIG_INVALID) RETRYABLE="false" ;;
esac

# 1. Write triage.json
python3 -c "
import json, sys
from datetime import datetime, timezone
triage = {
    'error_class': sys.argv[1],
    'retryable': sys.argv[2] == 'true',
    'failing_step': sys.argv[3],
    'recommended_next_action': sys.argv[4],
    'artifact_pointers': json.loads(sys.argv[5]),
    'timestamp': datetime.now(timezone.utc).isoformat(),
}
with open(sys.argv[6] + '/triage.json', 'w') as f:
    json.dump(triage, f, indent=2)
" "$ERROR_CLASS" "$RETRYABLE" "$FAILING_STEP" "$RECOMMENDED" "$POINTERS_JSON" "$TRIAGE_DIR"

# 2. Run csr_evidence_bundle.sh
BUNDLE_SCRIPT="$SCRIPT_DIR/csr_evidence_bundle.sh"
if [ -f "$BUNDLE_SCRIPT" ]; then
  bash "$BUNDLE_SCRIPT" "$TRIAGE_DIR" 30 2048 2>/dev/null || true
fi

# 3. Generate CSR_PROMPT.txt
"$SCRIPT_DIR/csr_prompt_from_bundle.sh" "$TRIAGE_DIR" 2>/dev/null || true

# 4. Write ERROR_SUMMARY.txt
cat > "$TRIAGE_DIR/ERROR_SUMMARY.txt" << EOF
error_class: $ERROR_CLASS
failing_step: $FAILING_STEP
recommended_next_action: $RECOMMENDED
fixpack_path: $TRIAGE_DIR
triage_json: $TRIAGE_DIR/triage.json
evidence_bundle: $TRIAGE_DIR/evidence_bundle.json
csr_prompt: $TRIAGE_DIR/CSR_PROMPT.txt
EOF

echo "$TRIAGE_DIR"
