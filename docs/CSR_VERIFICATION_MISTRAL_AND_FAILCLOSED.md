# CSR verification: Mistral fallback ON + fail-closed push

## Summary

- **Mistral**: Enabled in `config/llm.json`; key path canonical at `/etc/ai-ops-runner/secrets/mistral_api_key`; container mount `/run/openclaw_secrets`. One-time migration from `/opt`: `./ops/migrate_mistral_key_to_etc.sh` on VPS.
- **Fail-closed push**: No `--no-verify` in ops/.githooks; `./ops/check_fail_closed_push.sh` runs before push in ship_auto; review_finish documents that `git push --no-verify` is forbidden.

## Redacted proof (run on VPS / Tailnet)

### VPS — key and container

```bash
# Host: key at /etc (redacted listing)
ls -la /etc/ai-ops-runner/secrets/mistral_api_key
# Expect: -rw-r----- 1 1000 1000 ... mistral_api_key

# Inside container: key visible at mount
docker compose exec -T test_runner_api sh -lc 'ls -la /run/openclaw_secrets; test -f /run/openclaw_secrets/mistral_api_key && echo OK'
# Expect: mistral_api_key listed and OK
```

### Tailnet — LLM status

```bash
curl -s https://aiops-1.tailc75c62.ts.net/api/llm/status | python3 -m json.tool
```

**Expected snippet:**

- `providers`: entry for Mistral with `"enabled": true`, `"configured": true`, `"status": "active"`
- `router.review_model`: `gpt-4o-mini`
- `router.review_gate`: `fail-closed`
- Mistral entry has `review_fallback_model`: `labs-devstral-small-2512`

### Autopilot — no --no-verify

```bash
./ops/check_fail_closed_push.sh
# Expect: check_fail_closed_push: OK (no --no-verify; verdict check 0)

grep -rn '--no-verify' ops/ .githooks/ 2>/dev/null | grep -v 'check_fail_closed_push\|Fail-closed\|FORBIDDEN' || echo "None (allowed)"
# Expect: None (allowed)
```

## Final execution commands (in order)

1. **VPS (aiops-1) — one-time key migration and restart**

   ```bash
   ssh aiops-1 'cd /opt/ai-ops-runner && git pull --ff-only && ./ops/migrate_mistral_key_to_etc.sh && docker compose up -d'
   ```

2. **Repo — gated push (after APPROVED verdict)**

   ```bash
   cd /path/to/ai-ops-runner
   ./ops/review_auto.sh   # get APPROVED for current range
   ./ops/review_finish.sh # advance baseline + push (pre-push gate validates)
   # Or: ./ops/ship_auto.sh   # full autopilot (runs check_fail_closed_push before push)
   ```

3. **Verification (after deploy)**

   - Host: `ls -la /etc/ai-ops-runner/secrets/mistral_api_key`
   - Container: `docker compose exec -T test_runner_api sh -lc 'ls -la /run/openclaw_secrets; test -f /run/openclaw_secrets/mistral_api_key'`
   - API: `curl -s https://aiops-1.tailc75c62.ts.net/api/llm/status | python3 -m json.tool` → Mistral enabled=true, configured=true, active; review_fallback_model labs-devstral-small-2512
