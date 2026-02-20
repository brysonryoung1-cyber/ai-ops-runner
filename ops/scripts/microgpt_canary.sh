#!/usr/bin/env bash
# microgpt_canary.sh — Offline canary using Karpathy microgpt (fetch pinned + SHA256, patch, run).
# Runs inside test_runner worker. Writes to ARTIFACT_DIR/microgpt_canary/.
# No OPENAI_API_KEY / LiteLLM; no binding ports; no reading secrets; no writing outside artifacts/tmp.
set -euo pipefail

# Pinned source (Karpathy gist). Pin by SHA256; do not vendor unlicensed code.
# Optional override for tests: MICROGPT_EXPECTED_SHA256, MICROGPT_RAW_URL (e.g. file://)
MICROGPT_RAW_URL="${MICROGPT_RAW_URL:-https://gist.githubusercontent.com/karpathy/8627fe009c40f57531cb18360106ce95/raw/microgpt.py}"
EXPECTED_SHA256="${MICROGPT_EXPECTED_SHA256:-d47d88c2fd432c8ebdc1048beab7f7eb64ea7e0e664e11b812d72a6d95ebccee}"
CANARY_STEPS=50
CANARY_SAMPLES=3

if [[ -z "${ARTIFACT_DIR:-}" ]]; then
  echo "ARTIFACT_DIR not set"
  exit 1
fi
OUT_DIR="${ARTIFACT_DIR}/microgpt_canary"
mkdir -p "$OUT_DIR"
# Restrict all writes to ARTIFACT_DIR (and temp under it)
WORK="$(mktemp -d "${ARTIFACT_DIR}/microgpt_canary.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

ARTIFACTS_ROOT="$(dirname "$ARTIFACT_DIR")"
CACHE_DIR="${ARTIFACTS_ROOT}/cache/microgpt_canary"
mkdir -p "$CACHE_DIR"
SCRIPT_PY="$WORK/microgpt_canary.py"

# Resolve microgpt source: cache (verified) or fetch + verify
get_microgpt() {
  local src="$1"
  if [[ -f "$CACHE_DIR/microgpt.py" ]]; then
    local h
    h="$(sha256sum < "$CACHE_DIR/microgpt.py" | awk '{print $1}')"
    if [[ "$h" == "$EXPECTED_SHA256" ]]; then
      cp "$CACHE_DIR/microgpt.py" "$src"
      return 0
    fi
  fi
  # Fetch and verify (network only if cache miss or mismatch)
  curl -sSL "$MICROGPT_RAW_URL" -o "$src"
  local h
  h="$(sha256sum < "$src" | awk '{print $1}')"
  if [[ "$h" != "$EXPECTED_SHA256" ]]; then
    echo "microgpt SHA256 mismatch: got $h expected $EXPECTED_SHA256"
    exit 1
  fi
  cp "$src" "$CACHE_DIR/microgpt.py"
}

get_microgpt "$SCRIPT_PY"
# Minimal deterministic patch: reduce steps and sample count (temp copy only)
sed -i.bak -e "s/num_steps = 1000/num_steps = $CANARY_STEPS/" -e "s/for sample_idx in range(20)/for sample_idx in range($CANARY_SAMPLES)/" "$SCRIPT_PY"
rm -f "$SCRIPT_PY.bak"

# Provide input.txt so script does not download (offline-friendly)
printf 'Alice\nBob\nEve\n' > "$WORK/input.txt"

start_ts=$(python3 -c 'import time; print(int(time.time()*1000))')
stdout_log="$OUT_DIR/stdout.log"
stderr_log="$OUT_DIR/stderr.log"
(cd "$WORK" && python3 "./microgpt_canary.py" 2>"$stderr_log") | tee "$stdout_log" || true
end_ts=$(python3 -c 'import time; print(int(time.time()*1000))')
runtime_ms=$(( end_ts - start_ts ))

# Parse last loss line: "step  50 /  50 | loss X.XXXX" (may have \r)
final_loss=""
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line//$'\r'/}"
  if [[ "$line" =~ step[[:space:]]+[0-9]+[[:space:]]+/[[:space:]]+[0-9]+[[:space:]]+\|[[:space:]]+loss[[:space:]]+([0-9.]+) ]]; then
    final_loss="${BASH_REMATCH[1]}"
  fi
done < "$stdout_log"

# Parse inference samples: "sample  1: ..."
samples_file="$OUT_DIR/samples.txt"
samples_preview=()
if [[ -f "$stdout_log" ]]; then
  > "$samples_file"
  while read -r line; do
    if [[ "$line" =~ sample[[:space:]]+[0-9]+:[[:space:]]+(.+) ]]; then
      s="${BASH_REMATCH[1]}"
      echo "$s" >> "$samples_file"
      [[ ${#samples_preview[@]} -lt 5 ]] && samples_preview+=("$s")
    fi
  done < "$stdout_log"
fi

# Build summary.json
summary="$OUT_DIR/summary.json"
ok=0
if [[ -n "$final_loss" ]] && [[ -f "$stdout_log" ]]; then
  ok=1
fi
# JSON-safe array of sample strings (bash 4+)
samples_json="[]"
if [[ ${#samples_preview[@]} -gt 0 ]]; then
  parts=()
  for s in "${samples_preview[@]}"; do
    # Escape for JSON string
    esc="${s//\\/\\\\}"
    esc="${esc//\"/\\\"}"
    esc="${esc//$'\n'/\\n}"
    parts+=("\"$esc\"")
  done
  samples_json="[$(IFS=,; echo "${parts[*]}")]"
fi

python3 - "$summary" "$ok" "$CANARY_STEPS" "$final_loss" "$runtime_ms" "$EXPECTED_SHA256" "$samples_json" << PYEOF
import json, sys
p = sys.argv[1]
ok = bool(int(sys.argv[2]))
steps = int(sys.argv[3])
final_loss = sys.argv[4] if sys.argv[4] else None
runtime_ms = int(sys.argv[5])
sha256 = sys.argv[6]
samples_preview = json.loads(sys.argv[7])
with open(p, "w") as f:
    json.dump({
        "ok": ok,
        "steps": steps,
        "final_loss": float(final_loss) if final_loss else None,
        "runtime_ms": runtime_ms,
        "sha256": sha256,
        "samples_preview": samples_preview,
    }, f, indent=2)
PYEOF

exit $(( 1 - ok ))
