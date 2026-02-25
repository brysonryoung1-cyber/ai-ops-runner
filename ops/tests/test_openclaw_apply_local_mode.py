"""Unit tests for openclaw_apply_remote.sh local-mode behavior.

Verifies that when target IP matches local Tailscale IP, the script uses local
execution paths (no SSH invoked). Structural/static analysis of the script.
"""

import os


def _read_apply_script() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    apply_path = os.path.join(script_dir, "..", "openclaw_apply_remote.sh")
    with open(apply_path, "r", encoding="utf-8") as f:
        return f.read()


def test_has_apply_mode_detection():
    """Script must detect APPLY_MODE (local vs ssh_target)."""
    content = _read_apply_script()
    assert "APPLY_MODE" in content, "Missing APPLY_MODE variable"
    assert "local" in content and "ssh_target" in content, "Must define both modes"


def test_local_mode_has_direct_execution_paths():
    """When APPLY_MODE=local, steps run via (cd ...) not ssh."""
    content = _read_apply_script()
    assert 'if [ "$APPLY_MODE" = "local" ]' in content
    assert '(cd "$VPS_DIR"' in content
    assert "docker compose up" in content


def test_tailscale_ip_used_for_detection():
    """Script must use tailscale ip -4 for local detection."""
    content = _read_apply_script()
    assert "tailscale ip -4" in content
    assert "LOCAL_TAILSCALE_IP" in content
    assert "TARGET_IP" in content


def test_hostname_or_repo_path_for_local_detection():
    """Script must use hostname=aiops-1 or ROOT_DIR=/opt/ai-ops-runner for deterministic local mode."""
    content = _read_apply_script()
    assert "aiops-1" in content
    assert "/opt/ai-ops-runner" in content
    assert "HOSTNAME_SHORT" in content or "hostname" in content
