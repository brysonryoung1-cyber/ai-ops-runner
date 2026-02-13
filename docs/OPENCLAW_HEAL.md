# OpenClaw Heal

One-command "Apply + Verify + Evidence" entrypoint for OpenClaw infrastructure.

## Purpose

`ops/openclaw_heal.sh` is the canonical way to bring an OpenClaw host into compliance and prove it. It is idempotent, fail-closed, and produces a timestamped evidence bundle.

## Usage

```bash
# Full heal cycle on local VPS
sudo ./ops/openclaw_heal.sh

# Heal with notification on completion/failure
sudo ./ops/openclaw_heal.sh --notify

# Dry-run (check only, no fixes applied)
sudo ./ops/openclaw_heal.sh --check-only

# Skip optional fix step (verify + evidence only)
sudo ./ops/openclaw_heal.sh --verify-only
```

## Run Contract

The heal entrypoint executes these steps in order:

### Step 1: Private-Only Posture Pre-Check

- Enumerates all listening TCP ports
- Validates all binds are within allowed CIDRs (127.0.0.0/8, ::1, 100.64.0.0/10)
- **FAIL-CLOSED**: if any public listener is found AND `--check-only` is set, exits immediately
- Records pre-check snapshot for evidence

### Step 2: Apply Hardened Fixes (Optional, Idempotent)

Skipped if `--check-only` or `--verify-only` is set.

- Checks Tailscale connectivity
- If Tailscale is DOWN → skips SSH fix (lockout prevention)
- If Tailscale is UP and sshd is publicly bound → runs `openclaw_fix_ssh_tailscale_only.sh`
- All fixes are idempotent and safe to re-run

### Step 3: Doctor Verification

- Runs `openclaw_doctor.sh`
- **FAIL-CLOSED**: if doctor does not PASS, heal exits non-zero
- Doctor output captured for evidence bundle

### Step 4: Evidence Bundle

Captures a comprehensive evidence snapshot:

```
artifacts/evidence/<timestamp>_<hostname>/
├── listeners.txt          # Current listening ports (ss -tlnp)
├── sshd_config.txt        # Effective sshd config summary (sshd -T, no secrets)
├── guard_status.txt       # Guard timer status + last N log lines
├── docker_ports.txt       # Docker published ports summary
├── doctor_output.txt      # Full doctor output from Step 3
├── summary.json           # Machine-readable summary
└── README.txt             # Human-readable explanation
```

### Step 5: Summary

- Prints one-line result: `HEAL PASS: <artifact_path>` or `HEAL FAIL: <reason>`
- If `--notify` is set, sends Pushover alert with result

## Evidence Bundle Schema

`summary.json`:
```json
{
  "timestamp": "2026-02-13T12:00:00Z",
  "hostname": "aiops-1",
  "result": "PASS",
  "doctor_rc": 0,
  "checks": {
    "private_posture": "PASS",
    "fixes_applied": true,
    "doctor_pass": true
  },
  "artifact_path": "artifacts/evidence/20260213_120000_aiops-1"
}
```

## Safety Guarantees

1. **Idempotent**: Safe to run repeatedly. Re-running a passing heal is a no-op.
2. **Fail-closed**: Any uncertainty → fail. Never silently pass.
3. **Lockout prevention**: If Tailscale is down, SSH config is NEVER modified.
4. **No secrets**: Evidence bundle contains no keys, tokens, or passwords.
5. **Atomic evidence**: Evidence directory is written atomically; partial writes are cleaned up.

## Integration

- Guard can invoke heal as a remediation step
- Console "Apply" action wraps heal
- CI/CD can gate deployments on heal PASS + evidence artifact
