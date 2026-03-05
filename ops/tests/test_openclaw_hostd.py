from __future__ import annotations

import subprocess
from pathlib import Path

from ops import openclaw_hostd


def test_run_action_sets_console_run_id_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(openclaw_hostd, "ROOT_DIR", str(tmp_path))
    monkeypatch.setattr(openclaw_hostd, "ARTIFACTS_HOSTD", "artifacts/hostd")
    monkeypatch.setattr(
        openclaw_hostd,
        "ALLOWLIST",
        {"test_action": {"cmd": ["bash", "-lc", "echo ok"], "timeout_sec": 5}},
    )

    captured_env: dict[str, str] = {}

    def _fake_run(cmd, cwd, capture_output, timeout, env):  # noqa: ANN001,ARG001
        captured_env.update({k: str(v) for k, v in env.items() if isinstance(k, str)})
        return subprocess.CompletedProcess(cmd, 0, stdout=b'{"ok": true}\n', stderr=b"")

    monkeypatch.setattr(openclaw_hostd.subprocess, "run", _fake_run)

    exit_code, stdout, stderr, truncated = openclaw_hostd.run_action(
        "test_action",
        "20260305_180001_abcd1234",
        console_run_id="20260305180001-ef01",
    )

    assert exit_code == 0
    assert stdout.strip()
    assert stderr == ""
    assert truncated is False
    assert captured_env["OPENCLAW_RUN_ID"] == "20260305_180001_abcd1234"
    assert captured_env["OPENCLAW_CONSOLE_RUN_ID"] == "20260305180001-ef01"


def test_resolve_admin_token_prefers_env_file_when_secret_file_missing(tmp_path: Path, monkeypatch) -> None:
    hostd_env = tmp_path / "openclaw_hostd.env"
    hostd_env.write_text(
        "OTHER_FLAG=1\nOPENCLAW_ADMIN_TOKEN='env-file-token'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENCLAW_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(openclaw_hostd, "TOKEN_PATH", str(tmp_path / "missing_token"))
    monkeypatch.setattr(openclaw_hostd, "HOSTD_ENV_PATH", str(hostd_env))

    token, source, evidence = openclaw_hostd.resolve_admin_token()

    assert token == "env-file-token"
    assert source == "env_file"
    assert any("openclaw_hostd.env" in item for item in evidence)


def test_require_admin_reports_classified_forbidden_without_secret_leak(monkeypatch) -> None:
    monkeypatch.setattr(
        openclaw_hostd,
        "resolve_admin_token",
        lambda: ("expected-admin-token", "file", openclaw_hostd.ADMIN_TOKEN_EVIDENCE),
    )

    sent: dict[str, object] = {}

    class _Handler:
        headers = {"X-OpenClaw-Admin-Token": "wrong-token"}

        def send_json(self, status: int, body: dict) -> None:
            sent["status"] = status
            sent["body"] = body

    ok = openclaw_hostd.Handler._require_admin(_Handler())  # type: ignore[arg-type]

    assert ok is False
    assert sent["status"] == 403
    body = sent["body"]
    assert isinstance(body, dict)
    assert body["error_class"] == "HOSTD_FORBIDDEN"
    assert body["required_header"] == "X-OpenClaw-Admin-Token"
    assert "expected-admin-token" not in str(body)
