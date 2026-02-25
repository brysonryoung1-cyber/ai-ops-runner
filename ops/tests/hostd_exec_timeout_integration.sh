#!/usr/bin/env bash
# hostd_exec_timeout_integration — Integration test: HQ API exec route completes after >310s.
#
# Run with HOSTD_TIMEOUT_FULL_TEST=1 (takes ~6 min). Skips by default.
# Proves undici headersTimeout/bodyTimeout: 0 allows long hostd exec responses.
#
# Flow: stub hostd (delay 315s) → HQ POST /api/exec → assert completes without fetch failed.
set -euo pipefail

[ "${HOSTD_TIMEOUT_FULL_TEST:-0}" = "1" ] || { echo "SKIP: set HOSTD_TIMEOUT_FULL_TEST=1 to run"; exit 0; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

STUB_PORT="${HOSTD_STUB_PORT:-18877}"
DELAY_SEC=315
CONSOLE_PORT=18787

echo "==> hostd_exec_timeout_integration (delay ${DELAY_SEC}s)"

# 1. Start stub hostd: GET /health returns {ok:true}; POST /exec returns 200 after DELAY_SEC
node -e "
const http = require('http');
const s = http.createServer((req, res) => {
  if (req.method === 'GET' && req.url === '/health') {
    res.writeHead(200, {'Content-Type':'application/json'});
    res.end(JSON.stringify({ok:true}));
  } else if (req.method === 'POST' && req.url === '/exec') {
    setTimeout(() => {
      res.writeHead(200, {'Content-Type':'application/json'});
      res.end(JSON.stringify({ok:true,stdout:'',stderr:'',exitCode:0}));
    }, ${DELAY_SEC} * 1000);
  } else {
    res.writeHead(404); res.end();
  }
});
s.listen(${STUB_PORT}, '127.0.0.1', () => console.log('STUB_READY'));
" &
STUB_PID=$!
trap "kill $STUB_PID 2>/dev/null || true" EXIT

# Wait for stub
for i in $(seq 1 10); do
  curl -sS -o /dev/null "http://127.0.0.1:${STUB_PORT}/" 2>/dev/null && break
  sleep 1
done

# 2. Start Next.js with stub as hostd
export OPENCLAW_HOSTD_URL="http://127.0.0.1:${STUB_PORT}"
export OPENCLAW_CONSOLE_PORT="$CONSOLE_PORT"
export OPENCLAW_ADMIN_TOKEN="test-token"
cd apps/openclaw-console
npm run build 2>/dev/null || true
timeout 20 npm run start -- -p "$CONSOLE_PORT" 2>/dev/null &
CONSOLE_PID=$!
trap "kill $STUB_PID $CONSOLE_PID 2>/dev/null || true" EXIT
cd "$ROOT_DIR"

# Wait for console
for i in $(seq 1 30); do
  curl -sS -o /dev/null "http://127.0.0.1:${CONSOLE_PORT}/api/ui/health_public" 2>/dev/null && break
  sleep 1
done

# 3. POST /api/exec action=doctor (hits stub, waits 315s)
echo "  Calling POST /api/exec (will take ~${DELAY_SEC}s)..."
START=$(date +%s)
RESP=$(curl -sS -w "\n%{http_code}" -X POST "http://127.0.0.1:${CONSOLE_PORT}/api/exec" \
  -H "Content-Type: application/json" \
  -H "Origin: http://127.0.0.1:${CONSOLE_PORT}" \
  -H "X-OpenClaw-Token: test-token" \
  -d '{"action":"doctor"}' \
  --max-time 400 2>&1) || true
END=$(date +%s)
DURATION=$((END - START))

BODY=$(echo "$RESP" | head -n -1)
CODE=$(echo "$RESP" | tail -1)

if [ "$CODE" = "200" ] && echo "$BODY" | grep -q '"ok":true'; then
  echo "  PASS: Request completed after ${DURATION}s (no 300s timeout)"
else
  echo "  FAIL: code=$CODE duration=${DURATION}s body=$BODY"
  exit 1
fi

echo "==> hostd_exec_timeout_integration PASS"
