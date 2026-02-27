#!/usr/bin/env bash
# Hermetic OpenCode dry-run selftest. No provider, no Docker.
# Proves container/script produces correct artifact structure.
set -euo pipefail

ROOT_DIR="${OPENCLAW_REPO_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
RUN_ID="opencode_selftest_$(date +%s)"
ART_DIR="${ROOT_DIR}/artifacts/hostd/${RUN_ID}"
mkdir -p "${ART_DIR}"

echo '{"goal":"Add unit test for parse_config","ref":"HEAD","dry_run":true}' > "${ART_DIR}/params.json"
export OPENCLAW_REPO_ROOT="${ROOT_DIR}"
export OPENCLAW_RUN_ID="${RUN_ID}"

"${ROOT_DIR}/ops/scripts/opencode_run.sh"

# Verify artifact structure
for f in patch.diff patch_summary.json log.txt PROOF.md result.json; do
  [[ -f "${ART_DIR}/${f}" ]] || { echo "Missing ${f}"; exit 1; }
done

# Verify result.json
jq -e '.ok == true and .status == "dry_run"' "${ART_DIR}/result.json" >/dev/null || { echo "result.json invalid"; exit 1; }

echo "PASS: OpenCode dry-run artifact structure verified"
