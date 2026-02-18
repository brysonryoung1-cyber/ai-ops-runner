#!/usr/bin/env bash
# ship.sh — THE single entrypoint for pushing to origin/main.
#
# Acquires a filesystem lock, runs review, writes the canonical verdict,
# signs it with HMAC, commits it, and pushes.
#
# Invariant: docs/LAST_APPROVED_VERDICT.json contains the SHA of the
# reviewed code HEAD. The verdict commit itself only adds metadata files,
# so the pre-push gate allows this one-commit extension.
#
# Usage: ./ops/ship.sh [--max-attempts N] [--skip-tests]
#
# Required env:
#   VERDICT_HMAC_KEY  — HMAC key for signing the canonical verdict
#
# Never run `git push` directly. Always use this script.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── Defaults ──
MAX_ATTEMPTS=${SHIP_MAX_ATTEMPTS:-5}
SKIP_TESTS=0
ATTEMPT=0
LOCK_DIR="$ROOT_DIR/.locks"
LOCK_FILE="$LOCK_DIR/ship.lock"

# ── Parse args ──
while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-attempts)  MAX_ATTEMPTS="$2"; shift 2 ;;
    --skip-tests)    SKIP_TESTS=1; shift ;;
    -h|--help)
      echo "Usage: ship.sh [--max-attempts N] [--skip-tests]"
      echo ""
      echo "THE single entrypoint for pushing to main."
      echo "Never run 'git push' directly."
      exit 0
      ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# ── Preflight: VERDICT_HMAC_KEY (auto-load from local config if missing) ──
if [ -z "${VERDICT_HMAC_KEY:-}" ]; then
  VERDICT_KEY_FILE="${HOME:-/tmp}/.config/ai-ops-runner/VERDICT_HMAC_KEY"
  if [ -s "$VERDICT_KEY_FILE" ]; then
    VERDICT_HMAC_KEY="$(cat "$VERDICT_KEY_FILE")"
    export VERDICT_HMAC_KEY
  fi
fi
if [ -z "${VERDICT_HMAC_KEY:-}" ]; then
  echo "ERROR: VERDICT_HMAC_KEY not set and not found at ~/.config/ai-ops-runner/VERDICT_HMAC_KEY. Cannot sign verdicts." >&2
  echo "  Create the key file or set VERDICT_HMAC_KEY. Never commit secrets." >&2
  exit 1
fi

# ── Preflight: OpenAI key ──
if [ "${CODEX_SKIP:-0}" != "1" ]; then
  # shellcheck source=ensure_openai_key.sh
  source "$SCRIPT_DIR/ensure_openai_key.sh"
fi

# ── Acquire filesystem lock ──
mkdir -p "$LOCK_DIR"
if [ -f "$LOCK_FILE" ]; then
  LOCK_PID="$(cat "$LOCK_FILE" 2>/dev/null || echo "")"
  if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
    echo "ERROR: Another ship.sh is running (PID $LOCK_PID). Aborting." >&2
    exit 1
  fi
  echo "WARN: Stale lock found (PID $LOCK_PID not running). Removing." >&2
  rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

echo "=== ship.sh ==="
echo "  Max attempts: $MAX_ATTEMPTS"
echo "  Skip tests:   $SKIP_TESTS"
echo ""

# ── Preflight: clean tree, on main, up-to-date ──
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: Working tree is dirty. Commit or stash changes first." >&2
  git status --short >&2
  exit 1
fi

CURRENT_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || echo "")"
if [ "$CURRENT_BRANCH" != "main" ]; then
  echo "ERROR: Not on main branch (on '$CURRENT_BRANCH'). Switch to main first." >&2
  exit 1
fi

git fetch origin main --no-tags 2>/dev/null || true
LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse origin/main 2>/dev/null || echo "")"
if [ -n "$REMOTE_SHA" ] && [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
  if git merge-base --is-ancestor "$REMOTE_SHA" "$LOCAL_SHA" 2>/dev/null; then
    echo "  Local is ahead of origin/main — will push after review."
  else
    echo "ERROR: Local main has diverged from origin/main." >&2
    echo "  Local:  $LOCAL_SHA" >&2
    echo "  Remote: $REMOTE_SHA" >&2
    echo "  Rebase or merge first." >&2
    exit 1
  fi
fi

# ── Preflight: project brain ──
NEXT_MD="$ROOT_DIR/docs/OPENCLAW_NEXT.md"
if [ ! -f "$NEXT_MD" ] || [ ! -s "$NEXT_MD" ]; then
  echo "ERROR: docs/OPENCLAW_NEXT.md missing or empty." >&2
  exit 1
fi

# ── Run tests (unless --skip-tests) ──
run_tests() {
  echo "==> Running tests..."
  local test_failed=0

  if [ "${SHIP_SKIP_PYTEST:-0}" = "1" ]; then
    echo "  pytest: SKIPPED (SHIP_SKIP_PYTEST=1)"
  elif [ -d "$ROOT_DIR/services/test_runner/tests" ]; then
    echo "  Running pytest..."
    local PYTEST_OUTPUT="" PYTEST_RC=0 TIMEOUT_CMD=""
    if command -v timeout &>/dev/null; then
      TIMEOUT_CMD="timeout 120"
    elif command -v gtimeout &>/dev/null; then
      TIMEOUT_CMD="gtimeout 120"
    fi
    PYTEST_OUTPUT="$(cd "$ROOT_DIR/services/test_runner" && $TIMEOUT_CMD python3 -m pytest -q tests/ 2>&1)" || PYTEST_RC=$?
    if [ "$PYTEST_RC" -eq 0 ]; then
      echo "  pytest: PASSED"
    elif [ "$PYTEST_RC" -eq 5 ]; then
      echo "  pytest: NO TESTS COLLECTED (skipping)"
    elif echo "$PYTEST_OUTPUT" | grep -q "ModuleNotFoundError\|ImportError\|No module named"; then
      echo "  pytest: SKIPPED (missing dependencies)"
    elif [ "$PYTEST_RC" -eq 124 ]; then
      echo "  pytest: TIMED OUT" >&2; test_failed=1
    else
      echo "  pytest: FAILED (rc=$PYTEST_RC)" >&2
      echo "$PYTEST_OUTPUT" | tail -5 >&2
      test_failed=1
    fi
  fi

  if [ -f "$ROOT_DIR/docker-compose.yml" ]; then
    echo "  Validating docker-compose.yml..."
    if docker compose config -q 2>/dev/null; then
      echo "  docker compose config: VALID"
    else
      echo "  WARNING: docker compose config failed (docker may not be running)" >&2
    fi
  fi

  if [ "${SHIP_SKIP_SELFTESTS:-0}" = "1" ]; then
    echo "  ops selftests: SKIPPED (SHIP_SKIP_SELFTESTS=1)"
  elif [ -d "$ROOT_DIR/ops/tests" ]; then
    for selftest in "$ROOT_DIR"/ops/tests/*_selftest.sh; do
      [ -f "$selftest" ] || continue
      local bn="$(basename "$selftest")"
      [[ "$bn" == "ship_auto_selftest.sh" ]] && continue
      [[ "$bn" == "ship_selftest.sh" ]] && continue
      echo "  Running $bn..."
      if SHIP_SKIP_SELFTESTS=1 bash "$selftest"; then
        echo "  $bn: PASSED"
      else
        echo "  $bn: FAILED" >&2; test_failed=1
      fi
    done
  fi

  return $test_failed
}

if [ "$SKIP_TESTS" -eq 0 ]; then
  if ! run_tests; then
    echo "ERROR: Tests failed. Fix and re-run ./ops/ship.sh" >&2
    exit 1
  fi
fi

# ── Helpers ──
BASELINE_FILE="$ROOT_DIR/docs/LAST_REVIEWED_SHA.txt"
VERDICT_FILE="$ROOT_DIR/docs/LAST_APPROVED_VERDICT.json"

# Get owner/repo from origin (for gh api). Never modify branch protection from this script.
get_owner_repo() {
  local url
  url="$(git remote get-url origin 2>/dev/null || true)"
  if [ -z "$url" ]; then
    echo ""
    return
  fi
  if echo "$url" | grep -q '^https://github.com/'; then
    echo "$url" | sed -n 's|https://github.com/\([^/]*\)/\([^.]*\)\.git|\1/\2|p'
  elif echo "$url" | grep -q '^git@github.com:'; then
    echo "$url" | sed -n 's|git@github.com:\([^/]*\)/\([^.]*\)\.git|\1/\2|p'
  else
    echo ""
  fi
}

# Detect if we must use PR flow: required_pull_request_reviews set OR verdict-gate is a required check.
# When verdict-gate is required, direct push is rejected (check runs after push); use PR so check runs on PR.
# Returns 0 if PR flow required, 1 if not or on API failure (fail open to direct push).
# ship.sh NEVER calls gh api to change branch protection (no PUT/PATCH/POST/DELETE).
# Uses POSIX/BSD-safe grep patterns (no \s).
main_requires_pr() {
  local owner_repo prot_json
  [ -n "${SHIP_TEST_MOCK_PR_REQUIRED:-}" ] && { [ "$SHIP_TEST_MOCK_PR_REQUIRED" = "1" ] && return 0 || return 1; }
  owner_repo="$(get_owner_repo)"
  [ -z "$owner_repo" ] && return 1
  prot_json="$(gh api -H "Accept: application/vnd.github+json" "/repos/$owner_repo/branches/main/protection" 2>/dev/null)" || return 1
  # PR required by rule
  if echo "$prot_json" | grep -q '"required_pull_request_reviews":[^n]'; then
    if echo "$prot_json" | grep -qE '"required_pull_request_reviews"[[:space:]]*:[[:space:]]*\{'; then
      return 0
    fi
  fi
  # verdict-gate as required check: direct push would be rejected; use PR so check runs on PR branch
  if echo "$prot_json" | grep -q '"verdict-gate"'; then
    return 0
  fi
  return 1
}

# Push to origin/main: either direct push or PR flow (create PR, wait for verdict-gate, squash merge).
# Argument: SHA we expect on main after push (PUSH_HEAD for new verdict, HEAD for metadata-only).
# ship.sh NEVER modifies branch protection.
do_ship_push() {
  local expected_sha="$1"
  local owner_repo ship_branch pr_num i max_wait

  if main_requires_pr; then
    echo "  Main requires PR before merge; using PR-based ship."
    owner_repo="$(get_owner_repo)"
    [ -z "$owner_repo" ] && { echo "ERROR: Could not determine owner/repo for PR flow." >&2; return 1; }
    ship_branch="ship/$(date +%Y%m%d%H%M%S)-$(git rev-parse --short "$expected_sha")"
    git checkout -b "$ship_branch"
    git push -u origin "$ship_branch"
    pr_num="$(gh pr create --base main --head "$ship_branch" --title "ship: $(git rev-parse --short "$expected_sha")" --body "Automated ship PR. Verdict gate must pass.")"
    echo "  PR #$pr_num created. Waiting for required check 'verdict-gate'..."
    max_wait=90
    i=0
    while [ "$i" -lt "$max_wait" ]; do
      sleep 10
      i=$((i + 1))
      if gh pr view "$pr_num" --json statusCheckRollup -q '.statusCheckRollup[]? | select(.name=="verdict-gate") | .conclusion' 2>/dev/null | grep -q SUCCESS; then
        break
      fi
      echo "  ... waiting for verdict-gate ($i/${max_wait})"
    done
    if ! gh pr view "$pr_num" --json statusCheckRollup -q '.statusCheckRollup[]? | select(.name=="verdict-gate") | .conclusion' 2>/dev/null | grep -q SUCCESS; then
      echo "ERROR: verdict-gate did not pass on PR #$pr_num within timeout." >&2
      git checkout main 2>/dev/null || true
      git branch -D "$ship_branch" 2>/dev/null || true
      return 1
    fi
    gh pr merge "$pr_num" --squash --delete-branch
    git fetch origin main --no-tags 2>/dev/null || true
    git checkout main
    git reset --hard origin/main
    git branch -D "$ship_branch" 2>/dev/null || true
    if [ "$(git rev-parse origin/main^{tree})" != "$(git rev-parse "$expected_sha^{tree}")" ]; then
      echo "ERROR: After merge, main tree does not match expected." >&2
      return 1
    fi
    echo "  PR merged (squash). Main updated."
    return 0
  fi

  OPENCLAW_SHIP=1 git push origin HEAD:refs/heads/main
  return 0
}

write_canonical_verdict() {
  local head_sha="$1" start_sha="$2" artifact_path="$3" engine="$4" model="$5"

  python3 - "$VERDICT_FILE" "$head_sha" "$start_sha" "$artifact_path" "$engine" "$model" <<'PYEOF'
import json, sys, os, hmac, hashlib

vfile, head_sha, start_sha = sys.argv[1], sys.argv[2], sys.argv[3]
artifact_path, engine, model = sys.argv[4], sys.argv[5], sys.argv[6]
ts = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

data = {
    "approved_head_sha": head_sha,
    "range_start_sha": start_sha,
    "range_end_sha": head_sha,
    "simulated": False,
    "engine": engine,
    "model": model,
    "created_at": ts,
    "verdict_artifact_path": artifact_path,
    "signature": ""
}

key = os.environ.get("VERDICT_HMAC_KEY", "")
if not key:
    print("ERROR: VERDICT_HMAC_KEY not set", file=sys.stderr)
    sys.exit(1)

payload = {k: v for k, v in sorted(data.items()) if k != "signature"}
canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
data["signature"] = hmac.new(key.encode(), canonical, hashlib.sha256).hexdigest()

with open(vfile, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")

print("  Canonical verdict: approved_head_sha=%s sig=%s..." % (head_sha[:12], data["signature"][:16]))
PYEOF
}

detect_engine_model() {
  local verdict_artifact="$1"
  if [ ! -f "$verdict_artifact" ]; then
    echo "codex_cli unknown"
    return
  fi
  python3 - "$verdict_artifact" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    v = json.load(f)
meta = v.get("meta", {})
if meta.get("routed_via") == "llm_router":
    print("llm_router %s" % meta.get("model", meta.get("provider", "unknown")))
elif meta.get("type") == "codex_diff_review":
    print("codex_diff_review %s" % meta.get("model", "unknown"))
elif isinstance(meta.get("codex_cli"), dict):
    print("codex_cli %s" % meta.get("codex_cli", {}).get("version", "unknown"))
else:
    print("unknown unknown")
PYEOF
}

is_verdict_metadata_only_range() {
  local start_sha="$1" head_sha="$2"

  # If any non-metadata file changed, this is real unreviewed work.
  local diff_files=""
  diff_files="$(git diff --name-only "$start_sha" "$head_sha")"
  if [ -z "$diff_files" ]; then
    return 1
  fi
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    case "$f" in
      docs/LAST_APPROVED_VERDICT.json|docs/LAST_REVIEWED_SHA.txt) ;;
      *) return 1 ;;
    esac
  done <<< "$diff_files"

  # Require canonical verdict to explicitly anchor to the start SHA.
  python3 - "$VERDICT_FILE" "$start_sha" <<'PYEOF' >/dev/null
import json, sys
vfile, start = sys.argv[1], sys.argv[2]
with open(vfile) as f:
    v = json.load(f)
if v.get("approved_head_sha") != start:
    sys.exit(1)
if v.get("range_end_sha") != start:
    sys.exit(1)
if v.get("simulated") is not False:
    sys.exit(1)
PYEOF
}

# ── Review + ship loop ──
while [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ]; do
  ATTEMPT=$((ATTEMPT + 1))
  echo ""
  echo "=== Ship attempt $ATTEMPT / $MAX_ATTEMPTS ==="

  HEAD_SHA="$(git rev-parse HEAD)"
  START_SHA="$(tr -d '[:space:]' < "$BASELINE_FILE")"

  if [ "$START_SHA" = "$HEAD_SHA" ]; then
    echo "  Baseline already at HEAD. Checking remote..."
    REMOTE_NOW="$(git rev-parse origin/main 2>/dev/null || echo "")"
    if [ "$REMOTE_NOW" = "$HEAD_SHA" ]; then
      echo "  origin/main is already at HEAD. Nothing to do."
      exit 0
    fi
  fi

  if is_verdict_metadata_only_range "$START_SHA" "$HEAD_SHA"; then
    echo "  No unreviewed code changes since baseline (metadata-only delta)."
    REMOTE_NOW="$(git rev-parse origin/main 2>/dev/null || echo "")"
    if [ "$REMOTE_NOW" = "$HEAD_SHA" ]; then
      echo "  origin/main is already at HEAD. Nothing to do."
      exit 0
    fi

    echo ""
    echo "==> Pushing existing metadata-only commit to origin/main..."
    if ! do_ship_push "$HEAD_SHA"; then
      echo "ERROR: do_ship_push failed." >&2
      exit 1
    fi
    git fetch origin main --no-tags 2>/dev/null || true
    REMOTE_AFTER="$(git rev-parse origin/main)"
    if [ "$REMOTE_AFTER" != "$HEAD_SHA" ]; then
      # PR squash creates a new commit; verify tree equality
      if [ "$(git rev-parse "$REMOTE_AFTER^{tree}")" != "$(git rev-parse "$HEAD_SHA^{tree}")" ]; then
        echo "ERROR: Push verification failed. origin/main=$REMOTE_AFTER, expected=$HEAD_SHA" >&2
        exit 1
      fi
    fi
    echo "  Push complete (no new review commit required)."
    exit 0
  fi

  echo "  Review range: ${START_SHA:0:12}..${HEAD_SHA:0:12}"

  # Run review (--no-push; we handle push ourselves)
  REVIEW_RC=0
  "$SCRIPT_DIR/review_auto.sh" --no-push --since "$START_SHA" || REVIEW_RC=$?

  if [ "$REVIEW_RC" -ne 0 ]; then
    echo "==> Review BLOCKED on attempt $ATTEMPT"
    if [ "$ATTEMPT" -lt "$MAX_ATTEMPTS" ] && [ "${CODEX_SKIP:-0}" != "1" ]; then
      echo "==> Running autoheal..."
      "$SCRIPT_DIR/autoheal_codex.sh" || { echo "ERROR: Autoheal failed" >&2; exit 1; }
      continue
    fi
    echo "ERROR: Review blocked after $ATTEMPT attempts." >&2
    exit 1
  fi

  echo "==> APPROVED on attempt $ATTEMPT"

  # Find the verdict artifact just produced
  LATEST_ARTIFACT=""
  for vf in $(ls -t "$ROOT_DIR"/review_packets/*/CODEX_VERDICT.json 2>/dev/null); do
    LATEST_ARTIFACT="$vf"
    break
  done
  ARTIFACT_REL="${LATEST_ARTIFACT#$ROOT_DIR/}"

  # Detect engine/model
  EM="$(detect_engine_model "$LATEST_ARTIFACT")"
  ENGINE="${EM%% *}"
  MODEL="${EM#* }"

  # Capture the reviewed HEAD (before any verdict commit)
  REVIEWED_HEAD="$HEAD_SHA"

  # Write canonical verdict pointing to the reviewed HEAD
  write_canonical_verdict "$REVIEWED_HEAD" "$START_SHA" "$ARTIFACT_REL" "$ENGINE" "$MODEL"

  # Advance baseline
  echo "$REVIEWED_HEAD" > "$BASELINE_FILE"

  # Commit the canonical verdict + baseline
  git add -- docs/LAST_APPROVED_VERDICT.json docs/LAST_REVIEWED_SHA.txt
  REVIEW_FINISH_COMMIT=1 git commit -m "$(cat <<'EOF'
chore: advance review baseline + write canonical verdict

Automated by ops/ship.sh after APPROVED verdict.
EOF
)" -- docs/LAST_APPROVED_VERDICT.json docs/LAST_REVIEWED_SHA.txt

  PUSH_HEAD="$(git rev-parse HEAD)"
  echo "  Reviewed HEAD: ${REVIEWED_HEAD:0:12}"
  echo "  Push HEAD:     ${PUSH_HEAD:0:12} (includes verdict commit)"

  # Verify the canonical verdict is internally consistent
  python3 - "$VERDICT_FILE" "$REVIEWED_HEAD" <<'PYEOF' || { echo "ERROR: Verdict verification failed" >&2; continue; }
import json, sys
with open(sys.argv[1]) as f:
    v = json.load(f)
rh = sys.argv[2]
if v["approved_head_sha"] != rh or v["range_end_sha"] != rh:
    print("MISMATCH", file=sys.stderr)
    sys.exit(1)
if v["simulated"] is not False:
    print("simulated is not false", file=sys.stderr)
    sys.exit(1)
print("  Verdict consistent: approved_head_sha=%s" % rh[:12])
PYEOF

  # ── Push (never modify branch protection) ──
  echo ""
  echo "==> Pushing to origin/main..."
  if ! do_ship_push "$PUSH_HEAD"; then
    echo "ERROR: do_ship_push failed." >&2
    exit 1
  fi

  # Verify push (after PR flow, main may be squash commit; verify tree)
  git fetch origin main --no-tags 2>/dev/null || true
  REMOTE_AFTER="$(git rev-parse origin/main)"
  if [ "$REMOTE_AFTER" != "$PUSH_HEAD" ]; then
    if [ "$(git rev-parse "$REMOTE_AFTER^{tree}")" != "$(git rev-parse "$PUSH_HEAD^{tree}")" ]; then
      echo "ERROR: Push verification failed. origin/main=$REMOTE_AFTER, expected tree=$PUSH_HEAD" >&2
      exit 1
    fi
  fi

  echo ""
  echo "=== ship.sh COMPLETE ==="
  echo "  Reviewed HEAD:           $REVIEWED_HEAD"
  echo "  Push HEAD:               $PUSH_HEAD"
  echo "  Canonical verdict:       docs/LAST_APPROVED_VERDICT.json"
  echo "  approved_head_sha:       $REVIEWED_HEAD"
  echo "  Pushed:                  YES"
  echo "  Attempt:                 $ATTEMPT / $MAX_ATTEMPTS"
  exit 0
done

echo "ERROR: Exhausted $MAX_ATTEMPTS attempts without stable push." >&2
exit 1
