#!/usr/bin/env bash
# check_fail_closed_push.sh — Enforce fail-closed push: no --no-verify, HEAD covered by APPROVED verdict.
# Run before push (e.g. from ship_auto.sh). Exit 1 if any check fails.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

FAIL=0

# --- 1. Forbid --no-verify in ops and githooks (no bypass of pre-push gate) ---
# Exclude this script (it documents the rule) and comment-only lines.
VIOLATIONS=""
while IFS= read -r line; do
  file="${line%%:*}"
  rest="${line#*:}"
  # Skip this script and lines that are comments or document the rule
  case "$file" in *check_fail_closed_push.sh) continue ;; esac
  case "$rest" in \#*) continue ;; esac
  # Allow lines that only document the rule (forbid, check message, etc.)
  if echo "$rest" | grep -qE 'forbid|FORBIDDEN|without.*verify|do not use|check.*no-verify|Fail-closed check'; then continue; fi
  VIOLATIONS="${VIOLATIONS:+$VIOLATIONS$'\n'}$line"
done < <(grep -rn --exclude-dir=_tmp --exclude-dir=.venv --exclude-dir=node_modules -- '--no-verify' "$ROOT_DIR/ops" "$ROOT_DIR/.githooks" 2>/dev/null || true)
if [ -n "$VIOLATIONS" ]; then
  echo "$VIOLATIONS" >&2
  echo "FAIL: --no-verify is forbidden in ops/ and .githooks/. Remove it to preserve fail-closed push gate." >&2
  FAIL=1
fi

# --- 2. Optional: verify HEAD is covered by APPROVED verdict (same logic as pre-push) ---
# When CHECK_VERDICT=1, ensures current HEAD has a valid APPROVED verdict for the range.
if [ "${CHECK_VERDICT:-0}" = "1" ]; then
  if ! git rev-parse origin/main >/dev/null 2>&1; then
    echo "WARN: origin/main not reachable; skipping verdict check." >&2
  elif [ -d "$ROOT_DIR/review_packets" ]; then
    BASE="$(git merge-base HEAD origin/main)"
    TO="$(git rev-parse HEAD)"
    FOUND=""
    for vf in $(ls -t "$ROOT_DIR"/review_packets/*/CODEX_VERDICT.json 2>/dev/null); do
      [ -f "$vf" ] || continue
      RC=0
      python3 - "$vf" "$BASE" "$TO" <<'PYEOF' || RC=$?
import json, sys
with open(sys.argv[1]) as f: v = json.load(f)
if v.get("verdict") != "APPROVED": sys.exit(1)
meta = v.get("meta")
if not isinstance(meta, dict) or meta.get("simulated") is not False: sys.exit(1)
if meta.get("since_sha") != sys.argv[2] or meta.get("to_sha") != sys.argv[3]: sys.exit(1)
cli = meta.get("codex_cli")
if not isinstance(cli, dict) or not cli.get("version"): sys.exit(1)
sys.exit(0)
PYEOF
      if [ "$RC" -eq 0 ]; then FOUND=1; break; fi
    done
    if [ -z "${FOUND:-}" ]; then
      echo "FAIL: No valid APPROVED verdict for range ${BASE:0:12}..${TO:0:12}. Run ./ops/review_auto.sh" >&2
      FAIL=1
    fi
  fi
fi

if [ "$FAIL" -eq 1 ]; then
  exit 1
fi
echo "check_fail_closed_push: OK (no --no-verify; verdict check ${CHECK_VERDICT:-0})"
exit 0
