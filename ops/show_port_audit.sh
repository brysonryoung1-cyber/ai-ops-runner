#!/usr/bin/env bash
# show_port_audit.sh — Read-only TCP port listing for security audit.
# No secrets. Safe to run from hostd.
set -euo pipefail
exec ss -lntp 2>/dev/null || exec ss -lntp
