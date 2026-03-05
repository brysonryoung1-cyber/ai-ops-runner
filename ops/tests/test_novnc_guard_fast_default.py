from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NOVNC_GUARD = REPO_ROOT / "ops" / "guards" / "novnc_guard.sh"
NOVNC_GUARD_UNIT = REPO_ROOT / "ops" / "systemd" / "openclaw-novnc-guard.service"


def test_novnc_guard_defaults_to_fast_mode_with_deep_opt_in() -> None:
    content = NOVNC_GUARD.read_text(encoding="utf-8")
    assert 'MODE="fast"' in content
    assert "--deep" in content
    assert "PASS_FAST" in content
    assert "SKIP_DEEP_XVFB_MISSING" in content


def test_novnc_guard_unit_execstart_uses_fast_mode() -> None:
    content = NOVNC_GUARD_UNIT.read_text(encoding="utf-8")
    assert "novnc_guard.sh --fast" in content
