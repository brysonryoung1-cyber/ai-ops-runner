"""Tests for soma_business_dod_fixer action (interactive executor lane)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_soma_business_dod_fixer_registered():
    """soma_business_dod_fixer is in action_registry.json."""
    registry = REPO_ROOT / "config" / "action_registry.json"
    data = json.loads(registry.read_text())
    ids = {a["id"] for a in data.get("actions", [])}
    assert "soma_business_dod_fixer" in ids


def test_soma_business_dod_fixer_script_exists():
    """soma_business_dod_fixer.py exists and has main logic."""
    script = REPO_ROOT / "ops" / "scripts" / "soma_business_dod_fixer.py"
    assert script.exists()
    content = script.read_text()
    assert "verify_business_dod" in content
    assert "run_business_dod_ui_fixes" in content
    assert "run_soma_preflight" in content
    assert "RESULT.json" in content


def test_soma_business_dod_fixer_pass_path(tmp_path, monkeypatch):
    """When verify_business_dod returns PASS (hermetic), fixer exits 0."""
    monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_BUSINESS_DOD_SKIP_NETWORK", "1")
    # Create minimal passing state: acceptance snapshot with RAW module
    accept_dir = tmp_path / "soma_kajabi" / "acceptance" / "run_001"
    accept_dir.mkdir(parents=True)
    (accept_dir / "final_library_snapshot.json").write_text(
        json.dumps({"home": {"modules": ["raw", "Other"], "lessons": []}, "practitioner": {"modules": [], "lessons": []}})
    )
    discover_dir = tmp_path / "soma_kajabi" / "discover" / "run_001"
    discover_dir.mkdir(parents=True)
    (discover_dir / "memberships_page.html").write_text(
        "<a href='/offers/q6ntyjef/checkout'>x</a><a href='/offers/MHMmHyVZ/checkout'>y</a>"
    )
    (discover_dir / "community.json").write_text(
        json.dumps({"name": "Soma Community", "groups": [{"name": "Home Users"}, {"name": "Practitioners"}]})
    )
    (tmp_path / "soma_kajabi" / "acceptance" / "run_001" / "video_manifest.csv").write_text(
        "subject,content_sha256,status\nx,abc123,attached\n"
    )

    import subprocess
    import sys
    python = REPO_ROOT / ".venv-hostd" / "bin" / "python"
    if not python.exists():
        python = Path(sys.executable)
    r = subprocess.run(
        [str(python), str(REPO_ROOT / "ops" / "scripts" / "soma_business_dod_fixer.py")],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**dict(__import__("os").environ), "OPENCLAW_ARTIFACTS_ROOT": str(tmp_path), "OPENCLAW_BUSINESS_DOD_SKIP_NETWORK": "1"},
        timeout=60,
    )
    out = (r.stdout or "").strip()
    # Fixer prints pretty-printed JSON; parse full output
    data = json.loads(out)
    assert "run_id" in data
    assert data.get("status") in {"PASS", "FAIL", "HUMAN_ONLY"}
    assert data.get("status") == "PASS"
    assert "artifact_dir" in data
    assert r.returncode == 0
