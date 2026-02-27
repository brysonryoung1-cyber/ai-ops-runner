#!/usr/bin/env bash
# OpenCode runner — sandboxed container job for code.opencode.propose_patch.
# Produces patch.diff, patch_summary.json, log.txt (redacted), PROOF.md.
# Fail-closed: no provider → clear error. Never merges/deploys.
set -euo pipefail

ROOT_DIR="${OPENCLAW_REPO_ROOT:-/opt/ai-ops-runner}"
RUN_ID="${OPENCLAW_RUN_ID:?OPENCLAW_RUN_ID required}"
ART_DIR="${ROOT_DIR}/artifacts/hostd/${RUN_ID}"
PARAMS_JSON="${ART_DIR}/params.json"
PROVIDER_ENV_PATH="/etc/ai-ops-runner/secrets/opencode/provider_env"
OPENCODE_IMAGE="${OPENCODE_IMAGE:-ghcr.io/anomalyco/opencode:latest}"
MAX_RUNTIME="${OPENCODE_MAX_RUNTIME:-600}"

# Fail-closed: ensure params exist
if [[ ! -f "${PARAMS_JSON}" ]]; then
  echo '{"error_class":"OPENCODE_PARAMS_MISSING","reason":"params.json not found"}' > "${ART_DIR}/result.json"
  echo '{"ok":false,"error_class":"OPENCODE_PARAMS_MISSING","reason":"params.json not found"}' > "${ART_DIR}/patch_summary.json"
  exit 1
fi

GOAL=$(jq -r '.goal // empty' "${PARAMS_JSON}")
REF=$(jq -r '.ref // "origin/main"' "${PARAMS_JSON}")
TEST_CMD=$(jq -r '.test_command // empty' "${PARAMS_JSON}")
DRY_RUN=$(jq -r '.dry_run // false' "${PARAMS_JSON}")

if [[ -z "${GOAL}" ]]; then
  echo '{"error_class":"OPENCODE_GOAL_REQUIRED","reason":"goal is required"}' > "${ART_DIR}/result.json"
  echo '{"ok":false,"error_class":"OPENCODE_GOAL_REQUIRED","reason":"goal is required"}' > "${ART_DIR}/patch_summary.json"
  exit 1
fi

# Dry-run: produce artifact structure without calling OpenCode (no provider needed)
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "# Dry-run: no OpenCode invocation" > "${ART_DIR}/patch.diff"
  echo '{"files_changed":[],"tests_run":false,"tests_pass":null,"dry_run":true}' > "${ART_DIR}/patch_summary.json"
  echo "[DRY-RUN] Goal: ${GOAL}" > "${ART_DIR}/log.txt"
  echo "[DRY-RUN] Ref: ${REF}" >> "${ART_DIR}/log.txt"
  echo "[DRY-RUN] No provider called; artifact structure verified." >> "${ART_DIR}/log.txt"
  cat > "${ART_DIR}/PROOF.md" << EOF
# OpenCode Dry-Run PROOF

**Run ID:** ${RUN_ID}
**Mode:** dry_run (no provider)
**Goal:** ${GOAL}
**Ref:** ${REF}

## Artifacts

- patch.diff: empty (dry-run)
- patch_summary.json: structure verified
- log.txt: redacted
- PROOF.md: this file

## Verification

Dry-run proves the artifact structure is correct without invoking OpenCode.
EOF
  echo '{"ok":true,"status":"dry_run","artifact_dir":"artifacts/hostd/'${RUN_ID}'","citations":["artifacts/hostd/'${RUN_ID}'/patch.diff","artifacts/hostd/'${RUN_ID}'/PROOF.md"]}' > "${ART_DIR}/result.json"
  exit 0
fi

# Real run: check provider availability (fail-closed if required)
if [[ ! -f "${PROVIDER_ENV_PATH}" ]]; then
  echo '{"error_class":"OPENCODE_PROVIDER_REQUIRED","reason":"Provider env not configured. Create /etc/ai-ops-runner/secrets/opencode/provider_env (key=value, chmod 600) or use dry_run=true"}' > "${ART_DIR}/result.json"
  echo '{"ok":false,"error_class":"OPENCODE_PROVIDER_REQUIRED","reason":"Provider env not configured"}' > "${ART_DIR}/patch_summary.json"
  exit 1
fi

# Check Docker availability
if ! command -v docker &>/dev/null; then
  echo '{"error_class":"OPENCODE_DOCKER_MISSING","reason":"Docker not available"}' > "${ART_DIR}/result.json"
  echo '{"ok":false,"error_class":"OPENCODE_DOCKER_MISSING","reason":"Docker not available"}' > "${ART_DIR}/patch_summary.json"
  exit 1
fi

# Create temp workdir for checkout (container will mount)
WORK_DIR="${ART_DIR}/work"
mkdir -p "${WORK_DIR}"
cp -a "${ROOT_DIR}/." "${WORK_DIR}/"
cd "${WORK_DIR}"
git fetch origin 2>/dev/null || true
git checkout "${REF}" 2>/dev/null || git checkout HEAD 2>/dev/null || true

# Run OpenCode in container (sandboxed; provider_env mounted read-only)
set +e
docker run --rm \
  -v "${WORK_DIR}:/workspace:rw" \
  -w /workspace \
  --env-file "${PROVIDER_ENV_PATH}" \
  "${OPENCODE_IMAGE}" \
  run "${GOAL}" 2>&1 | tee "${ART_DIR}/opencode.log" | sed 's/\(api_key\|token\|password\|secret\)[=:][^[:space:]]*/\1=***REDACTED***/gi' > "${ART_DIR}/log.txt"
OC_EXIT=$?
set -e

# Extract patch (git diff)
cd "${WORK_DIR}"
git diff --no-color > "${ART_DIR}/patch.diff" 2>/dev/null || true
git diff --cached --no-color >> "${ART_DIR}/patch.diff" 2>/dev/null || true

# Run test command if provided
TESTS_RAN=false
TESTS_PASS=null
if [[ -n "${TEST_CMD}" ]]; then
  TESTS_RAN=true
  echo "Running tests: ${TEST_CMD}" >> "${ART_DIR}/log.txt"
  if eval "${TEST_CMD}" >> "${ART_DIR}/log.txt" 2>&1; then
    TESTS_PASS=true
  else
    TESTS_PASS=false
  fi
fi

# Build patch_summary
FILES_CHANGED=$(git diff --name-only 2>/dev/null | jq -R -s 'split("\n") | map(select(length>0))' 2>/dev/null || echo '[]')
echo "{\"files_changed\":${FILES_CHANGED},\"tests_run\":${TESTS_RAN},\"tests_pass\":${TESTS_PASS},\"opencode_exit\":${OC_EXIT}}" > "${ART_DIR}/patch_summary.json"

# PROOF.md
cat > "${ART_DIR}/PROOF.md" << EOF
# OpenCode propose_patch PROOF

**Run ID:** ${RUN_ID}
**Goal:** ${GOAL}
**Ref:** ${REF}
**OpenCode exit:** ${OC_EXIT}

## Artifacts

- patch.diff: git diff
- patch_summary.json: files changed, tests
- log.txt: redacted
- PROOF.md: this file

## Verification

Patch only; does not merge or deploy. Use ship_deploy_verify after approval.
EOF

# result.json
echo "{\"ok\":${OC_EXIT:-1}==0,\"status\":\"complete\",\"artifact_dir\":\"artifacts/hostd/${RUN_ID}\",\"citations\":[\"artifacts/hostd/${RUN_ID}/patch.diff\",\"artifacts/hostd/${RUN_ID}/PROOF.md\",\"artifacts/hostd/${RUN_ID}/result.json\"]}" > "${ART_DIR}/result.json"

exit "${OC_EXIT:-1}"
