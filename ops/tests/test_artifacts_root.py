"""Unit tests for ops.lib.artifacts_root — canonical artifacts root resolver."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.lib.artifacts_root import get_artifacts_root  # noqa: E402


class TestGetArtifactsRoot:
    """get_artifacts_root selection precedence: env > VPS > repo fallback."""

    def test_env_var_takes_highest_priority(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom_artifacts"
        custom.mkdir()
        monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(custom))
        result = get_artifacts_root(repo_root=tmp_path / "repo")
        assert result == custom

    def test_env_var_wins_even_if_vps_exists(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom_artifacts"
        custom.mkdir()
        monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", str(custom))
        vps = Path("/opt/ai-ops-runner/artifacts")
        if vps.exists():
            result = get_artifacts_root(repo_root=tmp_path / "repo")
            assert result == custom

    def test_repo_fallback_when_no_env_and_no_vps(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENCLAW_ARTIFACTS_ROOT", raising=False)
        monkeypatch.setattr(
            "ops.lib.artifacts_root._VPS_ARTIFACTS_ROOT",
            tmp_path / "nonexistent_vps_path",
        )
        repo = tmp_path / "repo"
        repo.mkdir()
        result = get_artifacts_root(repo_root=repo)
        assert result == repo / "artifacts"

    def test_none_repo_root_uses_cwd_fallback(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENCLAW_ARTIFACTS_ROOT", raising=False)
        monkeypatch.setattr(
            "ops.lib.artifacts_root._VPS_ARTIFACTS_ROOT",
            tmp_path / "nonexistent_vps_path",
        )
        monkeypatch.chdir(tmp_path)
        result = get_artifacts_root(repo_root=None)
        assert result == tmp_path / "artifacts"

    def test_empty_env_var_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_ARTIFACTS_ROOT", "   ")
        monkeypatch.setattr(
            "ops.lib.artifacts_root._VPS_ARTIFACTS_ROOT",
            tmp_path / "nonexistent_vps_path",
        )
        repo = tmp_path / "repo"
        result = get_artifacts_root(repo_root=repo)
        assert result == repo / "artifacts"


class TestApplyAndProveNoBootstrap:
    """Regression: aiops_apply_and_prove.sh must not fabricate LATEST_RUN.json."""

    def test_apply_and_prove_has_no_latest_run_bootstrap(self):
        script = REPO_ROOT / "ops" / "remote" / "aiops_apply_and_prove.sh"
        content = script.read_text(encoding="utf-8")
        assert "LATEST_RUN.json" not in content, (
            "apply_and_prove.sh must not reference LATEST_RUN.json — "
            "the pointer is the sole responsibility of soma_run_to_done"
        )
        assert "Bootstrap" not in content, (
            "apply_and_prove.sh must not contain bootstrap logic for project pointers"
        )
