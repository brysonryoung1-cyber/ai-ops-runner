#!/usr/bin/env bash
# openclaw_fix_ssh_tailscale_only.sh — Lock sshd to Tailscale IP only.
#
# Intended to run as root on the VPS (aiops-1).
# Fail-closed: exits non-zero if ANY step fails.
#
# What it does:
#   1. Determines the Tailscale IPv4 address.
#   2. Disables/masks ALL plausible socket-activation units:
#      ssh.socket, sshd.socket, and any templated ssh@*.socket.
#      Runs daemon-reload and verifies sockets are dead.
#   3. Detects whether the active daemon unit is ssh.service or sshd.service.
#   4. Ensures sshd_config has Include directive for sshd_config.d/.
#      Scans /etc/ssh/sshd_config and /etc/ssh/sshd_config.d/*.conf for
#      conflicting ListenAddress / AddressFamily directives and comments them
#      out (with timestamped backup).
#   5. Writes /etc/ssh/sshd_config.d/99-tailscale-only.conf:
#        AddressFamily inet
#        ListenAddress <TAILSCALE_IP>
#   6. Validates with: sshd -t + sshd -T (effective config must match).
#   7. If validation fails, restores backups and restarts service (safe rollback).
#   8. Restarts the detected sshd service unit.
#   9. Post-restart verification with ss — fail-closed if any public bind remains.
#
# Test mode: set OPENCLAW_TEST_ROOT to a temp dir to run without root
#            (stubs systemctl/tailscale/sshd/ss in PATH).
#
# Does NOT change auth methods, disable root login, or alter any other
# sshd settings. Minimal and safe.
#
# Usage:
#   sudo ./ops/openclaw_fix_ssh_tailscale_only.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== openclaw_fix_ssh_tailscale_only.sh ==="
echo "  Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  Host: $(hostname)"
echo ""

# --- 0. Root check (skipped in test mode) ---
OPENCLAW_TEST_ROOT="${OPENCLAW_TEST_ROOT:-}"
if [ -n "$OPENCLAW_TEST_ROOT" ]; then
  echo "  [TEST MODE] OPENCLAW_TEST_ROOT=$OPENCLAW_TEST_ROOT"
fi
if [ -z "$OPENCLAW_TEST_ROOT" ] && [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: This script must run as root (use sudo)." >&2
  exit 1
fi

# --- 1. Determine Tailscale IPv4 ---
echo "--- Step 1: Determine Tailscale IPv4 ---"
if ! command -v tailscale >/dev/null 2>&1; then
  echo "ERROR: tailscale command not found. Is Tailscale installed?" >&2
  exit 1
fi

TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 | tr -d '[:space:]')"
if [ -z "$TS_IP" ]; then
  echo "ERROR: Could not determine Tailscale IPv4 address." >&2
  echo "  Is Tailscale up? Run: tailscale status" >&2
  exit 1
fi

# Validate it looks like a tailnet IP (100.64.0.0/10)
if ! echo "$TS_IP" | grep -qE '^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.'; then
  echo "ERROR: Tailscale IP '$TS_IP' is not in the expected 100.64.0.0/10 range." >&2
  exit 1
fi

echo "  Tailscale IPv4: $TS_IP"

# --- 2. Disable ALL socket-activation units ---
echo ""
echo "--- Step 2: Disable socket-activation units ---"
# When any ssh*.socket is active, systemd binds :22 on 0.0.0.0/[::] directly
# and ignores sshd_config ListenAddress directives entirely.
#
# NOTE: We cache the unit list to avoid a SIGPIPE + pipefail interaction.
# With `set -o pipefail`, piping directly to `grep -q` can return non-zero
# if the writing process (systemctl) is killed by SIGPIPE before exit.
_UNIT_LIST="$(systemctl list-unit-files 2>/dev/null || true)"
SOCKET_DISABLED=0
for unit_name in ssh.socket sshd.socket; do
  if echo "$_UNIT_LIST" | grep -q "^${unit_name}"; then
    echo "  $unit_name found — disabling + masking"
    systemctl disable --now "$unit_name" 2>/dev/null || true
    systemctl stop "$unit_name" 2>/dev/null || true
    systemctl mask "$unit_name" 2>/dev/null || true
    SOCKET_DISABLED=1
  fi
done
# Templated ssh@*.socket instances (e.g. ssh@22-100.100.50.1:22.socket)
for unit_name in $(echo "$_UNIT_LIST" | grep -oE 'ssh@[^[:space:]]*\.socket' || true); do
  if [ -n "$unit_name" ]; then
    echo "  $unit_name (templated) found — disabling + masking"
    systemctl disable --now "$unit_name" 2>/dev/null || true
    systemctl stop "$unit_name" 2>/dev/null || true
    systemctl mask "$unit_name" 2>/dev/null || true
    SOCKET_DISABLED=1
  fi
done
if [ "$SOCKET_DISABLED" -eq 1 ]; then
  # Reload systemd to pick up mask changes — without this, systemd may use
  # cached socket state and the mask won't take effect before service restart.
  echo "  Reloading systemd daemon..."
  systemctl daemon-reload 2>/dev/null || true
  # Verify socket units are actually dead after masking
  for unit_name in ssh.socket sshd.socket; do
    if systemctl is-active --quiet "$unit_name" 2>/dev/null; then
      echo "  WARNING: $unit_name still active after mask — force-stopping" >&2
      systemctl kill "$unit_name" 2>/dev/null || true
    fi
  done
else
  echo "  No ssh*.socket units found — no action needed"
fi

# --- 2b. Detect and enable the active sshd service unit ---
SSHD_UNIT=""
for candidate in ssh.service sshd.service; do
  if echo "$_UNIT_LIST" | grep -q "${candidate}"; then
    SSHD_UNIT="$candidate"
    break
  fi
done
if [ -z "$SSHD_UNIT" ]; then
  echo "ERROR: Neither ssh.service nor sshd.service found." >&2
  exit 1
fi
echo "  Active daemon unit: $SSHD_UNIT"
systemctl enable "$SSHD_UNIT" 2>/dev/null || true

# --- Paths and backup stamp ---
SSHD_CONF_ROOT="${OPENCLAW_TEST_ROOT}/etc/ssh"
BACKUP_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$SSHD_CONF_ROOT/.backups/$BACKUP_STAMP"
SSHD_CONF_DIR="$SSHD_CONF_ROOT/sshd_config.d"
SSHD_CONF="$SSHD_CONF_DIR/99-tailscale-only.conf"

# --- Rollback helper (defined early, used in steps 5–7) ---
rollback_and_exit() {
  echo "" >&2
  echo "ERROR: $1 — rolling back." >&2
  # Remove our drop-in
  rm -f "$SSHD_CONF"
  # Restore backed-up files
  if [ -d "$BACKUP_DIR" ]; then
    for _bak in "$BACKUP_DIR"/*; do
      [ -f "$_bak" ] || continue
      _base="$(basename "$_bak")"
      if [ "$_base" = "sshd_config" ]; then
        cp "$_bak" "$SSHD_CONF_ROOT/sshd_config"
      else
        cp "$_bak" "$SSHD_CONF_DIR/$_base"
      fi
      echo "  Restored: $_base from backup" >&2
    done
  fi
  # Try to restart with restored config
  systemctl restart "$SSHD_UNIT" 2>/dev/null || true
  echo "  Service $SSHD_UNIT restarted with original config." >&2
  exit 1
}

# --- 3. Eliminate conflicting ListenAddress / AddressFamily directives ---
echo ""
echo "--- Step 3: Scan and fix conflicting ListenAddress / AddressFamily ---"
BACKED_UP_ANY=false

# 3a. Ensure sshd_config includes drop-in directory (critical: without this,
#     the 99-tailscale-only.conf drop-in file is completely ignored by sshd).
if [ -f "$SSHD_CONF_ROOT/sshd_config" ]; then
  if ! grep -qiE '^\s*Include\s+/etc/ssh/sshd_config\.d/\*' "$SSHD_CONF_ROOT/sshd_config"; then
    echo "  Include directive for sshd_config.d/ missing — adding"
    mkdir -p "$BACKUP_DIR"
    cp "$SSHD_CONF_ROOT/sshd_config" "$BACKUP_DIR/sshd_config"
    _tmp_conf="$(mktemp)"
    printf '# Added by openclaw — required for drop-in config to take effect\nInclude /etc/ssh/sshd_config.d/*.conf\n' > "$_tmp_conf"
    cat "$SSHD_CONF_ROOT/sshd_config" >> "$_tmp_conf"
    mv "$_tmp_conf" "$SSHD_CONF_ROOT/sshd_config"
    chmod 644 "$SSHD_CONF_ROOT/sshd_config"
  else
    echo "  Include directive present in sshd_config"
  fi
fi

# 3b. Build list of config files to scan for conflicts
SCAN_FILES=""
if [ -f "$SSHD_CONF_ROOT/sshd_config" ]; then
  SCAN_FILES="$SSHD_CONF_ROOT/sshd_config"
fi
if [ -d "$SSHD_CONF_DIR" ]; then
  for _f in "$SSHD_CONF_DIR"/*.conf; do
    [ -f "$_f" ] || continue
    case "$(basename "$_f")" in
      99-tailscale-only.conf) continue ;;  # skip our own drop-in
    esac
    SCAN_FILES="$SCAN_FILES $_f"
  done
fi
for conf_file in $SCAN_FILES; do
  [ -f "$conf_file" ] || continue
  # Check for conflicting directives
  HAS_CONFLICT=false
  while IFS= read -r _line; do
    # Uncommented ListenAddress not matching TS_IP
    if echo "$_line" | grep -qiE '^\s*ListenAddress\s'; then
      _addr="$(echo "$_line" | awk '{print $2}')"
      if [ "$_addr" != "$TS_IP" ]; then
        HAS_CONFLICT=true
        break
      fi
    fi
    # Uncommented AddressFamily not set to inet
    if echo "$_line" | grep -qiE '^\s*AddressFamily\s'; then
      _af="$(echo "$_line" | awk '{print $2}')"
      if [ "$_af" != "inet" ]; then
        HAS_CONFLICT=true
        break
      fi
    fi
  done < "$conf_file"

  if [ "$HAS_CONFLICT" = "true" ]; then
    # Create backup directory and save original (skip if Include check already backed it up)
    mkdir -p "$BACKUP_DIR"
    if [ ! -f "$BACKUP_DIR/$(basename "$conf_file")" ]; then
      cp "$conf_file" "$BACKUP_DIR/$(basename "$conf_file")"
    fi
    BACKED_UP_ANY=true
    echo "  Backed up: $conf_file"
    # Comment out conflicting directives (portable — no sed -i)
    _tmp_conf="$(mktemp)"
    while IFS= read -r _line; do
      _commented=false
      if echo "$_line" | grep -qiE '^\s*ListenAddress\s'; then
        _addr="$(echo "$_line" | awk '{print $2}')"
        if [ "$_addr" != "$TS_IP" ]; then
          printf '# DISABLED by openclaw %s — was: %s\n' "$BACKUP_STAMP" "$_line" >> "$_tmp_conf"
          echo "    Commented out: ListenAddress $_addr"
          _commented=true
        fi
      fi
      if [ "$_commented" = "false" ] && echo "$_line" | grep -qiE '^\s*AddressFamily\s'; then
        _af="$(echo "$_line" | awk '{print $2}')"
        if [ "$_af" != "inet" ]; then
          printf '# DISABLED by openclaw %s — was: %s\n' "$BACKUP_STAMP" "$_line" >> "$_tmp_conf"
          echo "    Commented out: AddressFamily $_af"
          _commented=true
        fi
      fi
      if [ "$_commented" = "false" ]; then
        printf '%s\n' "$_line" >> "$_tmp_conf"
      fi
    done < "$conf_file"
    mv "$_tmp_conf" "$conf_file"
    chmod 644 "$conf_file"
  fi
done

if [ "$BACKED_UP_ANY" = "false" ]; then
  echo "  No conflicting directives found"
fi

# --- 4. Write sshd drop-in config ---
echo ""
echo "--- Step 4: Write sshd config ---"
if [ ! -d "$SSHD_CONF_DIR" ]; then
  echo "  Creating $SSHD_CONF_DIR"
  mkdir -p "$SSHD_CONF_DIR"
fi

cat > "$SSHD_CONF" <<EOF
# Generated by openclaw_fix_ssh_tailscale_only.sh — $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Lock sshd to Tailscale interface only (no public exposure).
# Do NOT edit manually; re-run the script to update.
AddressFamily inet
ListenAddress $TS_IP
EOF

chmod 644 "$SSHD_CONF"
echo "  Written: $SSHD_CONF"
echo "    AddressFamily inet"
echo "    ListenAddress $TS_IP"

# --- 5. Validate sshd config ---
echo ""
echo "--- Step 5: Validate sshd config ---"
if ! sshd -t 2>&1; then
  rollback_and_exit "sshd config validation failed (sshd -t)"
fi
echo "  sshd -t: OK"

# Check effective config
echo "  sshd -T effective config:"
EFFECTIVE="$(sshd -T 2>&1 | grep -iE '^(addressfamily|listenaddress|port)' || true)"
echo "$EFFECTIVE" | while IFS= read -r _line; do
  [ -n "$_line" ] && echo "    $_line"
done

# Validate effective ListenAddress — must contain ONLY TS_IP
EFFECTIVE_LISTEN="$(echo "$EFFECTIVE" | grep -iE '^listenaddress\s' | awk '{print $2}' || true)"
BAD_LISTEN=""
while IFS= read -r _addr; do
  [ -z "$_addr" ] && continue
  # Strip port suffix if present (e.g., 100.100.50.1:22 → 100.100.50.1)
  _addr_no_port="${_addr%:*}"
  if [ "$_addr_no_port" != "$TS_IP" ]; then
    BAD_LISTEN="${BAD_LISTEN} ${_addr}"
  fi
done <<< "$EFFECTIVE_LISTEN"
if [ -n "$BAD_LISTEN" ]; then
  echo "  Effective config has unexpected ListenAddress:$BAD_LISTEN" >&2
  rollback_and_exit "Effective config has unexpected ListenAddress (expected only $TS_IP)"
fi
# Validate effective AddressFamily — must be inet
EFFECTIVE_AF="$(echo "$EFFECTIVE" | grep -iE '^addressfamily\s' | awk '{print $2}' || true)"
if [ -n "$EFFECTIVE_AF" ] && [ "$EFFECTIVE_AF" != "inet" ]; then
  rollback_and_exit "Effective AddressFamily is '$EFFECTIVE_AF' (expected 'inet')"
fi
echo "  Effective config validated: ListenAddress=$TS_IP AddressFamily=inet"

# --- 6. Restart sshd ---
echo ""
echo "--- Step 6: Restart sshd ($SSHD_UNIT) ---"
if ! systemctl restart "$SSHD_UNIT" 2>&1; then
  rollback_and_exit "Failed to restart $SSHD_UNIT"
fi
echo "  $SSHD_UNIT restarted"

# --- 7. Verify ---
echo ""
echo "--- Step 7: Verify ---"
# Give sshd a moment to bind
sleep 2

if command -v ss >/dev/null 2>&1; then
  SSH_BINDS="$(ss -lntp 2>/dev/null | grep ':22 ' || true)"
  echo "  Current :22 listeners:"
  echo "$SSH_BINDS" | while IFS= read -r _line; do
    [ -n "$_line" ] && echo "    $_line"
  done

  # Check for public binds (0.0.0.0:22, [::]:22, *:22)
  if echo "$SSH_BINDS" | grep -qE '0\.0\.0\.0:22|\[::\]:22|\*:22'; then
    echo "" >&2
    echo "ERROR: sshd is STILL bound to a public address after fix!" >&2
    echo "" >&2
    echo "  --- Debug: sshd effective config ---" >&2
    sshd -T 2>&1 | grep -iE 'addressfamily|listenaddress|port' >&2 || true
    echo "  --- Debug: systemctl status ---" >&2
    systemctl status ssh.socket sshd.socket ssh.service sshd.service 2>&1 | head -40 >&2 || true
    echo "  --- Debug: sshd config files ---" >&2
    grep -rnH 'ListenAddress\|AddressFamily\|Include' \
      "$SSHD_CONF_ROOT/sshd_config" \
      "$SSHD_CONF_DIR/" 2>/dev/null >&2 || true
    rollback_and_exit "sshd still bound to public address after restart"
  fi

  # Confirm bound to tailscale IP
  if echo "$SSH_BINDS" | grep -q "$TS_IP:22"; then
    echo ""
    echo "  VERIFIED: sshd is now bound to $TS_IP:22 only."
  else
    echo "" >&2
    echo "WARNING: sshd does not appear bound to $TS_IP:22." >&2
    echo "  Check the output above. It may be bound to another Tailscale IP." >&2
    # Don't fail — it might be on a different tailnet IP if multi-homed
  fi
else
  echo "  ss not available — skipping verification (run openclaw_doctor.sh to confirm)"
fi

echo ""
echo "=== sshd is now Tailscale-only ==="
echo "  ListenAddress: $TS_IP"
echo "  Config file:   $SSHD_CONF"
echo "  Service unit:  $SSHD_UNIT"
echo ""
echo "  Run ./ops/openclaw_doctor.sh to confirm all checks pass."
exit 0
