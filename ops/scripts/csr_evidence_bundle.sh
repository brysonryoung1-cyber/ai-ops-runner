#!/usr/bin/env bash
# csr_evidence_bundle.sh — Minimal evidence bundle from triage artifacts.
#
# Usage: csr_evidence_bundle.sh <triage_dir_or_json_path> [tail_lines] [max_bytes_per_snippet]
#
# Output: evidence_bundle.json in the triage directory.
# Exits 0 on success, 1 if triage.json is missing.
set -euo pipefail

INPUT="$1"
TAIL_LINES="${2:-30}"
MAX_BYTES="${3:-2048}"

if [ -f "$INPUT" ] && [[ "$INPUT" == *.json ]]; then
  TRIAGE_JSON="$INPUT"
  TRIAGE_DIR="$(dirname "$INPUT")"
elif [ -d "$INPUT" ] && [ -f "$INPUT/triage.json" ]; then
  TRIAGE_DIR="$INPUT"
  TRIAGE_JSON="$INPUT/triage.json"
else
  echo "ERROR: triage.json not found at $INPUT" >&2
  exit 1
fi

python3 - "$TRIAGE_JSON" "$TRIAGE_DIR" "$TAIL_LINES" "$MAX_BYTES" <<'PYEOF'
import json, sys, os
from datetime import datetime, timezone

triage_path = sys.argv[1]
triage_dir = sys.argv[2]
tail_lines = int(sys.argv[3])
max_bytes = int(sys.argv[4])

with open(triage_path) as f:
    triage = json.load(f)

def tail_safe(path, lines, cap):
    """Return last `lines` of file, capped at `cap` bytes."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            read_from = max(0, size - cap * 2)
            fh.seek(read_from)
            raw = fh.read()
        text = raw.decode("utf-8", errors="replace")
        selected = "\n".join(text.splitlines()[-lines:])
        if len(selected.encode("utf-8")) > cap:
            selected = selected.encode("utf-8")[-cap:].decode("utf-8", errors="replace")
        return selected
    except Exception as e:
        return f"<read error: {e}>"

pointers = triage.get("artifact_pointers", {})
snippets = {}
missing = []

for key, path in pointers.items():
    if path is None:
        continue
    if os.path.isfile(path):
        snippets[key] = tail_safe(path, tail_lines, max_bytes)
    else:
        missing.append(key)

bundle = {
    "error_class": triage.get("error_class"),
    "retryable": triage.get("retryable"),
    "failing_step": triage.get("failing_step"),
    "recommended_next_action": triage.get("recommended_next_action"),
    "artifact_pointers": pointers,
    "tail_snippets": snippets,
    "missing_artifacts": missing,
    "meta": {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bundle_version": "1.0",
        "source_triage": triage_path,
        "tail_lines": tail_lines,
        "max_bytes_per_snippet": max_bytes,
    },
}

out_path = os.path.join(triage_dir, "evidence_bundle.json")
with open(out_path, "w") as f:
    json.dump(bundle, f, indent=2)

print(out_path)
PYEOF
