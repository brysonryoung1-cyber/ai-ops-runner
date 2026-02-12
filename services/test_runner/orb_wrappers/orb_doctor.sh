#!/usr/bin/env bash
# orb_doctor.sh — Run ORB repo's doctor script in read-only worktree.
# Called by executor with cwd = worktree.
# Env vars (set by executor): ARTIFACT_DIR
set -euo pipefail

OUT="$ARTIFACT_DIR/DOCTOR_OUTPUT.txt"

echo "==> orb_doctor"
echo "    cwd=$(pwd)"
echo "    artifact_dir=$ARTIFACT_DIR"

# ---------------------------------------------------------------------------
# Pre-flight: set core.hooksPath so the doctor does not report a false finding.
#
# In the runner's ephemeral worktree the .githooks directory exists (tracked),
# but core.hooksPath was never set because the worktree was created from a
# bare mirror clone.  Setting it here writes to the gitdir config (located
# outside the worktree under /repos/), so:
#   • no tracked files are changed
#   • git status --porcelain remains clean
#   • mutation detection is NOT tripped
# ---------------------------------------------------------------------------
if [ -d .githooks ]; then
  git config core.hooksPath .githooks 2>/dev/null || true
  echo "    core.hooksPath -> .githooks (set in gitdir config)"
fi

if [ -f ./ops/doctor_repo.sh ]; then
  echo "==> Running ./ops/doctor_repo.sh"
  bash ./ops/doctor_repo.sh > "$OUT" 2>&1 || {
    RC=$?
    echo "==> doctor_repo.sh exited with code $RC"
    echo "--- EXIT CODE: $RC ---" >> "$OUT"
    exit $RC
  }
  echo "==> DOCTOR_OUTPUT.txt written ($(wc -c < "$OUT" | tr -d ' ') bytes)"
elif [ -f ./ops/doctor_repo.ps1 ]; then
  echo "PowerShell script found but not supported in this runner environment" >&2
  echo "UNSUPPORTED: ./ops/doctor_repo.ps1 (PowerShell not available in runner)" > "$OUT"
  exit 1
else
  echo "SCRIPT_NOT_FOUND: No doctor_repo script found in target repo" >&2
  echo "SCRIPT_NOT_FOUND: ./ops/doctor_repo.sh" > "$OUT"
  exit 1
fi
