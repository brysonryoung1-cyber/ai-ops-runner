#!/usr/bin/env bash
# csr_prompt_from_bundle.sh — Generate CSR_PROMPT.txt from evidence_bundle.json.
#
# Usage: csr_prompt_from_bundle.sh <triage_dir>
# Reads evidence_bundle.json (or triage.json if bundle missing) and writes CSR_PROMPT.txt.
set -euo pipefail

TRIAGE_DIR="$1"

if [ ! -d "$TRIAGE_DIR" ]; then
  echo "ERROR: triage_dir does not exist: $TRIAGE_DIR" >&2
  exit 1
fi

python3 - "$TRIAGE_DIR" <<'PYEOF'
import json, sys, os

triage_dir = sys.argv[1]
bundle_path = os.path.join(triage_dir, "evidence_bundle.json")
triage_path = os.path.join(triage_dir, "triage.json")

data = {}
if os.path.isfile(bundle_path):
    with open(bundle_path) as f:
        data = json.load(f)
elif os.path.isfile(triage_path):
    with open(triage_path) as f:
        data = json.load(f)
else:
    sys.exit(0)

error_class = data.get("error_class", "UNKNOWN")
failing_step = data.get("failing_step", "unknown")
recommended = data.get("recommended_next_action", "triage manually")
pointers = data.get("artifact_pointers", {})

lines = [
    "MODE: IMPLEMENTER (Opus)",
    "",
    f"error_class: {error_class}",
    f"failing_step: {failing_step}",
    f"recommended_next_action: {recommended}",
    "",
    "ARTIFACT POINTERS:",
]
for key, path in pointers.items():
    lines.append(f"  {key}: {path}")

lines.extend([
    "",
    "INSTRUCTIONS:",
    "- Use the evidence bundle (evidence_bundle.json) for context.",
    "- Do NOT paste full logs; keep tailcaps (30 lines / 2KB max).",
    "- Reference artifact paths instead of inlining content.",
    "- Apply the minimal safe fix, then re-verify.",
])

out_path = os.path.join(triage_dir, "CSR_PROMPT.txt")
with open(out_path, "w") as f:
    f.write("\n".join(lines) + "\n")
PYEOF
