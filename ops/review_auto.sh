#!/usr/bin/env bash
# review_auto.sh — One-command Codex review with auto packet-mode fallback
# Usage: ./ops/review_auto.sh [--no-push] [--since <sha>]
#
# CODEX_SKIP=1 produces a SIMULATED verdict (meta.simulated=true).
# Simulated verdicts are NEVER valid for the push gate.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- defaults ---
NO_PUSH=0
SINCE_SHA=""
MAX_PACKETS=20

# --- parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-push) NO_PUSH=1; shift ;;
    --since)   SINCE_SHA="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: review_auto.sh [--no-push] [--since <sha>]"
      exit 0
      ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# --- ensure OpenAI API key (not needed for CODEX_SKIP simulated mode) ---
if [ "${CODEX_SKIP:-0}" != "1" ]; then
  # shellcheck source=ensure_openai_key.sh
  source "$SCRIPT_DIR/ensure_openai_key.sh"
  # Print masked key fingerprint for audit trail (never the actual key)
  python3 "$SCRIPT_DIR/openai_key.py" status >&2
fi

# --- preflight: clean repo ---
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: Working tree is dirty. Commit or stash changes first." >&2
  git status --short >&2
  exit 1
fi

# --- resolve baseline ---
# Use merge-base with origin/main as the canonical baseline (matches pre-push gate).
# Falls back to docs/LAST_REVIEWED_SHA.txt if origin/main is unreachable.
if [ -z "$SINCE_SHA" ]; then
  if git rev-parse origin/main >/dev/null 2>&1; then
    SINCE_SHA="$(git merge-base HEAD origin/main)"
  else
    BASELINE_FILE="$ROOT_DIR/docs/LAST_REVIEWED_SHA.txt"
    if [ -f "$BASELINE_FILE" ]; then
      SINCE_SHA="$(tr -d '[:space:]' < "$BASELINE_FILE")"
      echo "WARNING: origin/main not reachable, using LAST_REVIEWED_SHA.txt as baseline" >&2
    else
      echo "ERROR: No --since provided and neither origin/main nor docs/LAST_REVIEWED_SHA.txt found" >&2
      exit 1
    fi
  fi
fi

HEAD_SHA="$(git rev-parse HEAD)"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
PACK_DIR="$ROOT_DIR/review_packets/${STAMP}"
mkdir -p "$PACK_DIR"

echo "=== review_auto.sh ==="
echo "  Baseline: $SINCE_SHA"
echo "  HEAD:     $HEAD_SHA"
echo "  Pack dir: $PACK_DIR"

# --- resolve codex command ---
resolve_codex_cmd() {
  if command -v codex >/dev/null 2>&1; then
    echo "codex"
  elif command -v npx >/dev/null 2>&1; then
    echo "npx -y @openai/codex"
  else
    echo ""
  fi
}

CODEX_CMD="$(resolve_codex_cmd)"

run_codex_review() {
  local bundle_file="$1"
  local verdict_file="$2"

  if [ -z "$CODEX_CMD" ]; then
    echo "ERROR: Neither 'codex' nor 'npx' found. Cannot run review." >&2
    exit 1
  fi

  local prompt
  prompt="$(cat "$bundle_file")"

  # Run codex in non-interactive mode with modern CLI flags:
  #   --full-auto    = non-interactive, sandboxed execution
  #   -s read-only   = read-only sandbox (review never writes code)
  #   --output-last-message = capture the agent's final message to a file
  local raw_output_file="${verdict_file}.raw"
  local codex_rc=0
  $CODEX_CMD exec \
    --full-auto \
    -s read-only \
    --output-last-message "$raw_output_file" \
    "$prompt" \
    2>/dev/null || codex_rc=$?

  if [ "$codex_rc" -ne 0 ] && [ ! -f "$raw_output_file" ]; then
    echo "ERROR: Codex exec failed (rc=$codex_rc) and produced no output" >&2
    return 1
  fi

  # Extract JSON from the captured last message
  local raw_content=""
  if [ -f "$raw_output_file" ]; then
    raw_content="$(cat "$raw_output_file")"
  fi

  local json_output
  json_output="$(echo "$raw_content" | python3 -c "
import sys, json, re

text = sys.stdin.read()
# Find JSON object in output — handles nested objects
match = re.search(r'\{[^{}]*(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}[^{}]*)*\}', text, re.DOTALL)
if match:
    obj = json.loads(match.group())
    print(json.dumps(obj))
else:
    sys.exit(1)
" 2>/dev/null || echo "")"

  if [ -z "$json_output" ]; then
    # Fallback: Codex sometimes returns plain "APPROVED" or "BLOCKED"
    local raw_trimmed
    raw_trimmed="$(echo "$raw_content" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' | head -1)"
    if [ "$raw_trimmed" = "APPROVED" ]; then
      echo '{"verdict":"APPROVED","blockers":[],"non_blocking":["Codex returned plain APPROVED; minimal verdict written"],"tests_run":"bundle"}' > "$verdict_file"
      rm -f "$raw_output_file"
      return 0
    fi
    if [ "$raw_trimmed" = "BLOCKED" ]; then
      echo '{"verdict":"BLOCKED","blockers":["Codex returned BLOCKED without details"],"non_blocking":[],"tests_run":"bundle"}' > "$verdict_file"
      rm -f "$raw_output_file"
      return 0
    fi
    echo "ERROR: Failed to extract valid JSON from Codex output" >&2
    echo "Raw output saved to ${raw_output_file}" >&2
    return 1
  fi

  echo "$json_output" > "$verdict_file"
  rm -f "$raw_output_file"
}

# Validate the complete verdict (with meta) against the schema contract.
# Prints the verdict value on success, errors on stderr + exits 1 on failure.
validate_verdict() {
  local verdict_file="$1"

  if [ ! -f "$verdict_file" ]; then
    echo "ERROR: Verdict file not found: $verdict_file" >&2
    return 1
  fi

  python3 - "$verdict_file" <<'PYEOF'
import json, sys

vfile = sys.argv[1]
try:
    with open(vfile) as f:
        v = json.load(f)
except (json.JSONDecodeError, FileNotFoundError) as e:
    print("ERROR: %s" % e, file=sys.stderr)
    sys.exit(1)

errors = []

# --- top-level keys ---
required_top = ["verdict", "blockers", "non_blocking", "tests_run", "meta"]
for key in required_top:
    if key not in v:
        errors.append("Missing required key: %s" % key)
extra_top = set(v.keys()) - set(required_top)
if extra_top:
    errors.append("Extra top-level keys not allowed: %s" % extra_top)

# --- verdict value ---
if v.get("verdict") not in ["APPROVED", "BLOCKED"]:
    errors.append("Invalid verdict: %s" % v.get("verdict"))

# --- type checks ---
if not isinstance(v.get("blockers"), list):
    errors.append("blockers must be an array")
if not isinstance(v.get("non_blocking"), list):
    errors.append("non_blocking must be an array")
if not isinstance(v.get("tests_run"), str):
    errors.append("tests_run must be a string")

# --- meta validation ---
meta = v.get("meta")
if isinstance(meta, dict):
    meta_required = ["since_sha", "to_sha", "generated_at", "review_mode", "simulated"]
    for key in meta_required:
        if key not in meta:
            errors.append("meta.%s missing" % key)
    meta_allowed = set(meta_required + ["codex_cli"])
    meta_extra = set(meta.keys()) - meta_allowed
    if meta_extra:
        errors.append("Extra meta keys not allowed: %s" % meta_extra)

    if meta.get("review_mode") not in ["bundle", "packet"]:
        errors.append("Invalid meta.review_mode: %s" % meta.get("review_mode"))

    if not isinstance(meta.get("simulated"), bool):
        errors.append("meta.simulated must be boolean")

    # When simulated=false, codex_cli MUST be a non-null object
    if meta.get("simulated") is False:
        cli = meta.get("codex_cli")
        if not isinstance(cli, dict):
            errors.append("meta.codex_cli required (non-null object) when simulated=false")
        else:
            if not cli.get("version"):
                errors.append("meta.codex_cli.version must be non-empty")
            if not cli.get("command"):
                errors.append("meta.codex_cli.command must be non-empty")
elif meta is not None:
    errors.append("meta must be an object or is malformed")

if errors:
    for e in errors:
        print("ERROR: %s" % e, file=sys.stderr)
    sys.exit(1)

print(v["verdict"])
PYEOF
}

# --- Try single bundle mode first ---
BUNDLE_FILE="$PACK_DIR/REVIEW_BUNDLE.txt"
VERDICT_FILE="$PACK_DIR/CODEX_VERDICT.json"
META_FILE="$PACK_DIR/META.json"

REVIEW_MODE="bundle"
BUNDLE_RC=0
"$SCRIPT_DIR/review_bundle.sh" --since "$SINCE_SHA" --output "$BUNDLE_FILE" || BUNDLE_RC=$?

if [ "$BUNDLE_RC" -eq 6 ]; then
  echo "==> Bundle exceeds size cap, switching to packet mode..."
  REVIEW_MODE="packet"
elif [ "$BUNDLE_RC" -ne 0 ]; then
  echo "ERROR: review_bundle.sh failed with exit code $BUNDLE_RC" >&2
  exit 1
fi

GENERATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- CODEX_SKIP path: simulated verdict (tests ONLY) ---
if [ "${CODEX_SKIP:-0}" = "1" ]; then
  echo ""
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║  SIMULATED VERDICT — NOT VALID FOR PUSH GATE            ║"
  echo "║  CODEX_SKIP=1 is for selftests only.                    ║"
  echo "╚══════════════════════════════════════════════════════════╝"
  echo ""

  python3 - "$VERDICT_FILE" "$SINCE_SHA" "$HEAD_SHA" "$GENERATED_AT" <<'PYEOF'
import json, sys
vfile, since, to, ts = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
verdict = {
    "verdict": "APPROVED",
    "blockers": [],
    "non_blocking": ["CODEX_SKIP=1: simulated verdict — NOT valid for push gate"],
    "tests_run": "skipped (CODEX_SKIP=1)",
    "meta": {
        "since_sha": since,
        "to_sha": to,
        "generated_at": ts,
        "review_mode": "bundle",
        "simulated": True,
        "codex_cli": None
    }
}
with open(vfile, "w") as f:
    json.dump(verdict, f, indent=2)
PYEOF

else
  # --- Real Codex review path ---

  # Capture codex version for provenance
  if [ -z "$CODEX_CMD" ]; then
    echo "ERROR: Neither 'codex' nor 'npx' found. Cannot run review." >&2
    exit 1
  fi
  CODEX_VERSION="$($CODEX_CMD --version 2>&1 | head -1 | tr -d '\n' || true)"
  if [ -z "$CODEX_VERSION" ]; then
    echo "WARNING: Cannot determine codex version, using 'unknown'" >&2
    CODEX_VERSION="unknown"
  fi
  CODEX_CMD_RECORD="$CODEX_CMD exec --full-auto -s read-only --output-last-message <verdict>"

  if [ "$REVIEW_MODE" = "bundle" ]; then
    echo "==> Running Codex review (single bundle)..."
    run_codex_review "$BUNDLE_FILE" "$VERDICT_FILE"
  else
    echo "==> Running Codex review (packet mode)..."
    # Generate per-file packets
    FILE_LIST="$(git diff --name-only "$SINCE_SHA" "$HEAD_SHA")"
    PACKET_NUM=0
    ALL_VERDICTS=()

    while IFS= read -r filepath; do
      [ -z "$filepath" ] && continue
      PACKET_NUM=$((PACKET_NUM + 1))
      if [ "$PACKET_NUM" -gt "$MAX_PACKETS" ]; then
        echo "WARNING: Exceeded max packets ($MAX_PACKETS), stopping" >&2
        break
      fi

      PACKET_FILE="$PACK_DIR/PACKET_${PACKET_NUM}.txt"
      PACKET_VERDICT="$PACK_DIR/VERDICT_${PACKET_NUM}.json"

      FILE_DIFF="$(git diff "$SINCE_SHA" "$HEAD_SHA" -- "$filepath")"
      cat > "$PACKET_FILE" <<PKTEOF
=== REVIEW PACKET (${PACKET_NUM}/${MAX_PACKETS}) ===
Repository: ai-ops-runner
Range: ${SINCE_SHA}..${HEAD_SHA}
File: ${filepath}
Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)

=== DIFF ===
${FILE_DIFF}

=== INSTRUCTIONS ===
You are reviewing changes to a single file in the ai-ops-runner repository.
Output ONLY valid JSON. Verdict: APPROVED or BLOCKED.
Schema: {"verdict":"...","blockers":[...],"non_blocking":[...],"tests_run":"..."}
BLOCK only for: deploy/compile failures, unsafe behavior, logging regressions, non-idempotent ops.
PKTEOF

      echo "  Reviewing packet $PACKET_NUM: $filepath"
      run_codex_review "$PACKET_FILE" "$PACKET_VERDICT"
      ALL_VERDICTS+=("$PACKET_VERDICT")
    done <<< "$FILE_LIST"

    # Aggregate verdicts
    python3 - "$PACK_DIR" "$VERDICT_FILE" <<'PYEOF'
import json, sys, glob, os

pack_dir = sys.argv[1]
out_file = sys.argv[2]
verdict_files = sorted(glob.glob(os.path.join(pack_dir, "VERDICT_*.json")))

if not verdict_files:
    print("ERROR: No verdict files found", file=sys.stderr)
    sys.exit(1)

final = {
    "verdict": "APPROVED",
    "blockers": [],
    "non_blocking": [],
    "tests_run": "Reviewed %d packets" % len(verdict_files)
}

for vf in verdict_files:
    with open(vf) as f:
        v = json.load(f)
    if v.get("verdict") == "BLOCKED":
        final["verdict"] = "BLOCKED"
    final["blockers"].extend(v.get("blockers", []))
    final["non_blocking"].extend(v.get("non_blocking", []))

with open(out_file, "w") as f:
    json.dump(final, f, indent=2)
print(final["verdict"])
PYEOF
  fi

  # --- Quick check: did codex produce a valid verdict? ---
  if [ ! -f "$VERDICT_FILE" ]; then
    echo "ERROR: Codex review did not produce a verdict file." >&2
    echo "  No verdict artifact left behind." >&2
    exit 1
  fi

  RAWCHECK_RC=0
  python3 - "$VERDICT_FILE" <<'PYEOF' || RAWCHECK_RC=$?
import json, sys
with open(sys.argv[1]) as f:
    v = json.load(f)
for key in ["verdict", "blockers", "non_blocking", "tests_run"]:
    if key not in v:
        print("ERROR: codex output missing required key: %s" % key, file=sys.stderr)
        sys.exit(1)
if v["verdict"] not in ["APPROVED", "BLOCKED"]:
    print("ERROR: codex output has invalid verdict: %s" % v["verdict"], file=sys.stderr)
    sys.exit(1)
PYEOF
  if [ "$RAWCHECK_RC" -ne 0 ]; then
    echo "ERROR: Codex output validation failed. Removing invalid verdict." >&2
    rm -f "$VERDICT_FILE"
    exit 1
  fi

  # --- Add meta to the verdict ---
  python3 - "$VERDICT_FILE" "$SINCE_SHA" "$HEAD_SHA" "$GENERATED_AT" "$REVIEW_MODE" "$CODEX_VERSION" "$CODEX_CMD_RECORD" <<'PYEOF'
import json, sys

vfile = sys.argv[1]
since, to, ts = sys.argv[2], sys.argv[3], sys.argv[4]
mode, version, command = sys.argv[5], sys.argv[6], sys.argv[7]

with open(vfile) as f:
    v = json.load(f)

v["meta"] = {
    "since_sha": since,
    "to_sha": to,
    "generated_at": ts,
    "review_mode": mode,
    "simulated": False,
    "codex_cli": {
        "version": version,
        "command": command
    }
}

with open(vfile, "w") as f:
    json.dump(v, f, indent=2)
PYEOF
fi

# --- validate complete verdict (with meta) ---
VERDICT_RESULT=""
VALIDATE_RC=0
VERDICT_RESULT="$(validate_verdict "$VERDICT_FILE")" || VALIDATE_RC=$?
if [ "$VALIDATE_RC" -ne 0 ]; then
  echo "ERROR: Verdict validation failed. Removing invalid verdict." >&2
  rm -f "$VERDICT_FILE"
  exit 1
fi

# --- write META.json (logging convenience — NOT source of truth) ---
python3 - "$META_FILE" "$SINCE_SHA" "$HEAD_SHA" "$GENERATED_AT" "$REVIEW_MODE" "$VERDICT_RESULT" <<'PYEOF'
import json, sys
mfile = sys.argv[1]
meta = {
    "since_sha": sys.argv[2],
    "head_sha": sys.argv[3],
    "timestamp": sys.argv[4],
    "mode": sys.argv[5],
    "verdict": sys.argv[6]
}
with open(mfile, "w") as f:
    json.dump(meta, f, indent=2)
PYEOF

echo ""
echo "=== Review Result ==="
echo "  Verdict: $VERDICT_RESULT"
echo "  Artifacts: $PACK_DIR"
python3 - "$VERDICT_FILE" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    v = json.load(f)
if v["blockers"]:
    print("  Blockers:")
    for b in v["blockers"]:
        print("    - %s" % b)
if v["non_blocking"]:
    print("  Non-blocking:")
    for n in v["non_blocking"]:
        print("    - %s" % n)
meta = v.get("meta", {})
print("  Simulated: %s" % meta.get("simulated", "unknown"))
PYEOF

if [ "$VERDICT_RESULT" = "APPROVED" ]; then
  echo ""
  echo "==> APPROVED"

  if [ "$NO_PUSH" -eq 0 ]; then
    echo "==> Advancing baseline and pushing..."
    "$SCRIPT_DIR/review_finish.sh"
  else
    echo "==> --no-push: Skipping baseline advance and push."
    echo "    Run: ./ops/review_finish.sh"
  fi
  exit 0
else
  echo ""
  echo "==> BLOCKED — fix the blockers above and re-run:"
  echo "    ./ops/review_auto.sh"
  exit 1
fi
