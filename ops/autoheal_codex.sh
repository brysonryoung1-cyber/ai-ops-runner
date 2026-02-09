#!/usr/bin/env bash
# autoheal_codex.sh — Read blockers from last verdict, generate + apply fixes
# Constrained to allowlist: ops/ docs/ services/ configs/ only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# --- allowlist of modifiable paths ---
ALLOWED_PATHS=("ops/" "docs/" "services/" "configs/" ".gitignore" "docker-compose.yml" "README.md")

# --- find most recent verdict ---
LATEST_VERDICT=""
if [ -d "$ROOT_DIR/review_packets" ]; then
  for verdict_file in $(ls -t "$ROOT_DIR"/review_packets/*/CODEX_VERDICT.json 2>/dev/null); do
    LATEST_VERDICT="$verdict_file"
    break
  done
fi

if [ -z "$LATEST_VERDICT" ] || [ ! -f "$LATEST_VERDICT" ]; then
  echo "ERROR: No verdict file found in review_packets/" >&2
  exit 1
fi

# --- extract blockers ---
BLOCKERS="$(python3 -c "
import json
with open('$LATEST_VERDICT') as f:
    v = json.load(f)
blockers = v.get('blockers', [])
if not blockers:
    print('NONE')
else:
    for b in blockers:
        print(b)
")"

if [ "$BLOCKERS" = "NONE" ]; then
  echo "INFO: No blockers found in verdict. Nothing to heal."
  exit 0
fi

echo "=== autoheal_codex.sh ==="
echo "  Verdict: $LATEST_VERDICT"
echo "  Blockers:"
echo "$BLOCKERS" | while IFS= read -r line; do
  echo "    - $line"
done

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

if [ -z "$CODEX_CMD" ]; then
  echo "ERROR: Neither 'codex' nor 'npx' found. Cannot autoheal." >&2
  exit 1
fi

# --- generate patch plan ---
ALLOWED_LIST="${ALLOWED_PATHS[*]}"
HEAL_PROMPT="You are fixing blockers in the ai-ops-runner repository.

BLOCKERS TO FIX:
$BLOCKERS

CONSTRAINTS:
- You may ONLY modify files under these paths: $ALLOWED_LIST
- Do NOT modify any other files.
- Make minimal, targeted fixes.
- After applying fixes, all tests should pass.
- Output a description of what you changed.

Apply the fixes now."

echo ""
echo "==> Running Codex autoheal..."
HEAL_OUTPUT="$($CODEX_CMD exec \
  -c approval_policy=never \
  --prompt "$HEAL_PROMPT" \
  2>/dev/null || true)"

echo "$HEAL_OUTPUT"

# --- verify no forbidden files were modified ---
MODIFIED_FILES="$(git diff --name-only 2>/dev/null || true)"
if [ -n "$MODIFIED_FILES" ]; then
  while IFS= read -r modified_file; do
    [ -z "$modified_file" ] && continue
    ALLOWED=0
    for allowed_path in "${ALLOWED_PATHS[@]}"; do
      if [[ "$modified_file" == "$allowed_path"* ]] || [[ "$modified_file" == "$allowed_path" ]]; then
        ALLOWED=1
        break
      fi
    done
    if [ "$ALLOWED" -eq 0 ]; then
      echo "ERROR: Autoheal modified forbidden file: $modified_file" >&2
      echo "  Reverting all changes..." >&2
      git checkout -- .
      exit 1
    fi
  done <<< "$MODIFIED_FILES"
fi

# --- commit fixes if any ---
if [ -n "$(git status --porcelain)" ]; then
  git add -A
  git commit -m "$(cat <<'EOF'
fix: autoheal blockers from review verdict

Automated fixes applied by autoheal_codex.sh.
EOF
)"
  echo "==> Autoheal fixes committed"
else
  echo "INFO: No files modified by autoheal"
fi

echo "==> Autoheal complete"
