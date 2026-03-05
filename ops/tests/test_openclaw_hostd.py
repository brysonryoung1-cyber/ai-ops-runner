from __future__ import annotations

import subprocess

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
