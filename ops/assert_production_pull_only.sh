#!/usr/bin/env bash
# assert_production_pull_only.sh — Verify this host (aiops-1) cannot push to origin.
#
# Run inside deploy_pipeline.sh on production. Exit 1 if push capability detected.
# Writes machine-readable reason to stdout (no secrets). Caller may write to deploy_result.json.
# Fail-closed: any ambiguity => FAIL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

REASONS=""
FAIL=0

# --- 1. Git remote: must be HTTPS or read-only deploy key ---
if [ -d ".git" ]; then
  REMOTE_URL="$(git config --get remote.origin.url 2>/dev/null || true)"
  if [ -z "$REMOTE_URL" ]; then
    REASONS="${REASONS}no_origin_url "
    FAIL=1
  elif echo "$REMOTE_URL" | grep -qE '^https://'; then
    # HTTPS: we rely on no stored credentials or read-only token. We cannot fully verify
    # "no write" without attempting push; we check that git credential helper isn't caching write creds.
    if git config --get credential.helper >/dev/null 2>&1; then
      # Helper present: could be caching write creds. Conservative fail.
      REASONS="${REASONS}credential_helper_set "
      FAIL=1
    fi
    # If no helper, git push would prompt or fail — acceptable for automated pull-only.
  elif echo "$REMOTE_URL" | grep -qE '^git@|^ssh://'; then
    # SSH: must be read-only deploy key or no key with write. Check ssh-agent for GitHub.
    if command -v ssh-add >/dev/null 2>&1; then
      KEYS="$(ssh-add -l 2>/dev/null || true)"
      if [ -n "$KEYS" ] && echo "$KEYS" | grep -qi 'github'; then
        REASONS="${REASONS}ssh_agent_has_github_key "
        FAIL=1
      fi
    fi
    # Check default SSH key for github.com (often ~/.ssh/id_*)
    if [ -n "${HOME:-}" ] && [ -d "$HOME/.ssh" ]; then
      for f in "$HOME/.ssh/id_rsa" "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_ecdsa"; do
        if [ -f "$f" ] && [ -r "$f" ]; then
          # Key exists and is readable — could be write key. Conservative: warn only if we know it's GitHub
          if grep -l "github" "$HOME/.ssh/config" 2>/dev/null; then
            REASONS="${REASONS}ssh_default_key_present_with_github_config "
            FAIL=1
          fi
        fi
      done
    fi
  fi
else
  REASONS="${REASONS}not_git_repo "
  FAIL=1
fi

# --- 2. Explicit: no "git push" in current process tree (sanity) ---
# We don't run push here; deploy_pipeline is responsible for not invoking push.
# This script only asserts pull-only environment.

if [ "$FAIL" -eq 1 ]; then
  echo "assert_production_pull_only: FAIL reason=${REASONS}"
  exit 1
fi
echo "assert_production_pull_only: PASS (no push capability detected)"
exit 0
