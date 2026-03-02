#!/usr/bin/env bash
# openclaw_codex_review.sh — Automated diff-only review via OpenAI API
#
# Generates a review bundle, submits to OpenAI API (chat completions),
# stores verdict in artifacts, and prints summary.
#
# Usage:
#   ./ops/openclaw_codex_review.sh                    # Review HEAD vs origin/main
#   ./ops/openclaw_codex_review.sh --since <sha>      # Review from specific SHA
#   ./ops/openclaw_codex_review.sh --gate             # Exit nonzero on BLOCKED
#
# Gates on:
#   - Security regressions (public binds)
#   - Allowlist bypass
#   - Key handling regressions / interactive prompts
#   - Guard/doctor disablement
#   - Lockout risk
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- Defaults ---
SINCE_SHA=""
GATE_MODE=0
STAMP="$(date -u +%Y%m%d_%H%M%S)"
ARTIFACTS_DIR="$ROOT_DIR/artifacts/codex_review/${STAMP}"

# --- Parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since) SINCE_SHA="$2"; shift 2 ;;
    --gate)  GATE_MODE=1; shift ;;
    -h|--help)
      echo "Usage: openclaw_codex_review.sh [--since <sha>] [--gate]"
      exit 0
      ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# --- Resolve baseline ---
if [ -z "$SINCE_SHA" ]; then
  if git rev-parse origin/main >/dev/null 2>&1; then
    SINCE_SHA="$(git merge-base HEAD origin/main)"
  else
    BASELINE_FILE="$ROOT_DIR/docs/LAST_REVIEWED_SHA.txt"
    if [ -f "$BASELINE_FILE" ]; then
      SINCE_SHA="$(tr -d '[:space:]' < "$BASELINE_FILE")"
    else
      echo "ERROR: No --since and no origin/main or LAST_REVIEWED_SHA.txt" >&2
      exit 1
    fi
  fi
fi

HEAD_SHA="$(git rev-parse HEAD)"

echo "=== openclaw_codex_review.sh ==="
echo "  Baseline: $SINCE_SHA"
echo "  HEAD:     $HEAD_SHA"
echo "  Artifacts: $ARTIFACTS_DIR"

# --- Review configuration (LiteLLM default path) ---
# Configurable env vars for review cost/stability:
#   REVIEW_BASE_URL    — LiteLLM proxy base URL (default: use LiteLLM if available, else OpenAI direct)
#   REVIEW_API_KEY     — API key / virtual key for review endpoint
#   REVIEW_MODEL       — model for review (default: gpt-4o-mini, cheap + fast)
#   REVIEW_MAX_TOKENS  — max tokens per review request (default: 4096)
#   REVIEW_MAX_FILES   — max files per review packet (default: 20)
#   REVIEW_MAX_DIFF_BYTES — max diff size in bytes (default: 200000)

REVIEW_BASE_URL="${REVIEW_BASE_URL:-}"
REVIEW_API_KEY="${REVIEW_API_KEY:-}"
REVIEW_MODEL="${REVIEW_MODEL:-gpt-4o-mini}"
REVIEW_MAX_TOKENS="${REVIEW_MAX_TOKENS:-4096}"
REVIEW_MAX_FILES="${REVIEW_MAX_FILES:-20}"
REVIEW_MAX_DIFF_BYTES="${REVIEW_MAX_DIFF_BYTES:-200000}"
REVIEW_PATH_USED=""

# Determine which review path to use
if [ -n "$REVIEW_BASE_URL" ] && [ -n "$REVIEW_API_KEY" ]; then
  REVIEW_PATH_USED="litellm"
  echo "  Review path: LiteLLM ($REVIEW_BASE_URL)" >&2
  echo "  Review model: $REVIEW_MODEL (max_tokens=$REVIEW_MAX_TOKENS)" >&2
elif [ -n "${OPENAI_API_KEY:-}" ]; then
  REVIEW_PATH_USED="direct"
  REVIEW_BASE_URL="https://api.openai.com/v1"
  REVIEW_API_KEY="$OPENAI_API_KEY"
  echo "  Review path: Direct OpenAI" >&2
  echo "  Review model: $REVIEW_MODEL (max_tokens=$REVIEW_MAX_TOKENS)" >&2
else
  # Try to source OpenAI key as fallback
  if [ -f "$SCRIPT_DIR/ensure_openai_key.sh" ]; then
    # shellcheck source=ensure_openai_key.sh
    source "$SCRIPT_DIR/ensure_openai_key.sh" 2>/dev/null || true
  fi
  if [ -n "${OPENAI_API_KEY:-}" ]; then
    REVIEW_PATH_USED="direct"
    REVIEW_BASE_URL="https://api.openai.com/v1"
    REVIEW_API_KEY="$OPENAI_API_KEY"
    echo "  Review path: Direct OpenAI (from ensure_openai_key)" >&2
  else
    echo "ERROR: No review API configured. Set one of:" >&2
    echo "  1. REVIEW_BASE_URL + REVIEW_API_KEY (LiteLLM — recommended, cheapest)" >&2
    echo "  2. OPENAI_API_KEY (direct OpenAI)" >&2
    echo "" >&2
    echo "Fail-closed: cannot proceed without review capability." >&2
    exit 1
  fi
fi

# Print masked key fingerprint (never the actual key)
if [ -n "$REVIEW_API_KEY" ]; then
  KEY_FINGERPRINT="${REVIEW_API_KEY:0:4}…${REVIEW_API_KEY: -4}"
  echo "  API key fingerprint: $KEY_FINGERPRINT" >&2
fi

# --- Generate review bundle ---
mkdir -p "$ARTIFACTS_DIR"
BUNDLE_FILE="$ARTIFACTS_DIR/REVIEW_BUNDLE.txt"
BUNDLE_RC=0
"$SCRIPT_DIR/review_bundle.sh" --since "$SINCE_SHA" --output "$BUNDLE_FILE" || BUNDLE_RC=$?

if [ "$BUNDLE_RC" -eq 6 ]; then
  echo "WARNING: Bundle exceeds size cap; using truncated diff" >&2
  # Generate a truncated diff for API review
  {
    echo "=== REVIEW PACKET (TRUNCATED — SIZE_CAP exceeded) ==="
    echo "Repository: ai-ops-runner"
    echo "Range: ${SINCE_SHA}..${HEAD_SHA}"
    echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo ""
    echo "=== CHANGED FILES ==="
    git diff --name-status "$SINCE_SHA" "$HEAD_SHA"
    echo ""
    echo "=== DIFF (stat only) ==="
    git diff --stat "$SINCE_SHA" "$HEAD_SHA"
    echo ""
    echo "=== SECURITY-RELEVANT DIFFS ==="
    # Include only security-critical file diffs
    for pattern in "ops/openclaw_" "middleware" "allowlist" "docker-compose" "route.ts" "validate.ts"; do
      git diff "$SINCE_SHA" "$HEAD_SHA" -- "*${pattern}*" 2>/dev/null || true
    done
  } > "$BUNDLE_FILE"
elif [ "$BUNDLE_RC" -ne 0 ]; then
  echo "ERROR: review_bundle.sh failed (rc=$BUNDLE_RC)" >&2
  exit 1
fi

# --- Call OpenAI API via LLM Router ---
VERDICT_FILE="$ARTIFACTS_DIR/CODEX_VERDICT.json"

echo "==> Submitting to OpenAI API for review (via LLM router)..."

# All reviews go through the central LLM router via src.llm.review_gate.
# The router handles OpenAI (primary) + Mistral (fallback), budget caps,
# cost telemetry, and fail-closed semantics.
REVIEW_RC=0
if python3 -c "from src.llm.review_gate import run_review" 2>/dev/null; then
  python3 -m src.llm.review_gate "$VERDICT_FILE" "$BUNDLE_FILE" || REVIEW_RC=$?
else
  echo "ERROR: src.llm.review_gate not importable. Cannot proceed." >&2
  echo "  The LLM router (review_gate) is REQUIRED for all code reviews." >&2
  echo "  Ensure the repo is intact and src/llm/ modules are available." >&2
  echo "  Fail-closed: review BLOCKED." >&2
  REVIEW_RC=1
fi

if [ "$REVIEW_RC" -ne 0 ]; then
  echo "ERROR: Review submission failed (rc=$REVIEW_RC)" >&2
  exit 1
fi

if [ ! -f "$VERDICT_FILE" ]; then
  echo "ERROR: No verdict produced" >&2
  exit 1
fi

# --- Add range info to verdict meta (for pre-push hook compatibility) ---
python3 - "$VERDICT_FILE" "$SINCE_SHA" "$HEAD_SHA" <<'PYEOF'
import json, sys
vfile, since_sha, to_sha = sys.argv[1], sys.argv[2], sys.argv[3]
with open(vfile) as f:
    v = json.load(f)
meta = v.get("meta", {})
meta["since_sha"] = since_sha
meta["to_sha"] = to_sha
v["meta"] = meta
with open(vfile, "w") as f:
    json.dump(v, f, indent=2)
PYEOF

# --- Copy verdict to review_packets/ for pre-push hook ---
REVIEW_PACKETS_DIR="$ROOT_DIR/review_packets/${STAMP}"
mkdir -p "$REVIEW_PACKETS_DIR"
cp "$VERDICT_FILE" "$REVIEW_PACKETS_DIR/CODEX_VERDICT.json"

# --- Display results ---
echo ""
echo "=== Review Result ==="
python3 - "$VERDICT_FILE" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    v = json.load(f)

print(f"  Verdict: {v['verdict']}")

sc = v.get("security_checks", {})
if sc:
    print("  Security Checks:")
    for check, status in sc.items():
        icon = "PASS" if "PASS" in str(status) else "FAIL"
        print(f"    {check}: {status}")

if v.get("blockers"):
    print("  Blockers:")
    for b in v["blockers"]:
        print(f"    - {b}")

if v.get("non_blocking"):
    print("  Non-blocking:")
    for n in v["non_blocking"]:
        print(f"    - {n}")

meta = v.get("meta", {})
cost = meta.get("cost_usd", 0)
print(f"  Cost: ${cost:.6f}")
print(f"  Artifacts: {sys.argv[1]}")
PYEOF

VERDICT_VALUE="$(python3 -c "import json; print(json.load(open('$VERDICT_FILE'))['verdict'])")"

echo ""
echo "==> $VERDICT_VALUE"
echo "  Artifacts: $ARTIFACTS_DIR"

# --- Gate mode ---
if [ "$GATE_MODE" -eq 1 ] && [ "$VERDICT_VALUE" = "BLOCKED" ]; then
  echo ""
  echo "ERROR: Review BLOCKED — fix blockers before merge/deploy" >&2

  # Check specific security gates
  python3 - "$VERDICT_FILE" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    v = json.load(f)
sc = v.get("security_checks", {})
gates = ["public_binds", "allowlist_bypass", "key_handling", "guard_doctor_intact", "lockout_risk"]
failures = [g for g in gates if "FAIL" in str(sc.get(g, ""))]
if failures:
    print("  Security gate failures:", ", ".join(failures), file=sys.stderr)
PYEOF
  exit 1
fi

exit 0
