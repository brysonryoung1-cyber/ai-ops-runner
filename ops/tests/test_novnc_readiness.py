"""State-machine tests for convergent noVNC readiness."""
from __future__ import annotations

import json

from ops.lib import novnc_readiness as nr


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.t += float(seconds)


def test_state_machine_converges_after_recovery() -> None:
    """Fail, recover, and eventually PASS with expected exponential sleeps."""
    clock = _FakeClock()
    ready_by_attempt = {0: False, 1: False, 2: True}
    recover_calls: list[int] = []

    def probe_once(attempt: int) -> dict:
        return {"ready": ready_by_attempt.get(attempt, False), "error_class": "NOVNC_NOT_READY"}

    def recover_once(attempt: int, _snapshot: dict) -> dict:
        recover_calls.append(attempt)
        return {"attempt": attempt, "ok": True}

    out = nr.run_convergent_readiness(
        probe_once,
        recover_once,
        backoff_seconds=(2, 4, 8),
        max_wait_seconds=120,
        clock=clock,
    )
    assert out["ok"] is True
    assert out["attempts"] == 3
    assert recover_calls == [0, 1]
    assert clock.sleeps == [2.0, 4.0]


def test_state_machine_respects_time_budget() -> None:
    """Failing probes should stop once max_wait_seconds budget is exhausted."""
    clock = _FakeClock()

    def probe_once(attempt: int) -> dict:
        # Simulate probe cost to burn budget quickly.
        clock.t += 15.0
        return {"ready": False, "error_class": "NOVNC_NOT_READY", "attempt": attempt}

    def recover_once(attempt: int, _snapshot: dict) -> dict:
        return {"attempt": attempt, "ok": True}

    out = nr.run_convergent_readiness(
        probe_once,
        recover_once,
        backoff_seconds=(2, 4, 8, 16),
        max_wait_seconds=40,
        clock=clock,
    )
    assert out["ok"] is False
    assert out["elapsed_sec"] <= 60  # includes simulated probe cost and bounded sleeps
    assert len(out["probes"]) < 5


def test_state_machine_skips_recovery_on_first_pass() -> None:
    """A green initial probe should return immediately without recovery/sleep."""
    clock = _FakeClock()
    recover_calls: list[int] = []

    def probe_once(_attempt: int) -> dict:
        return {"ready": True, "error_class": None}

    def recover_once(attempt: int, _snapshot: dict) -> dict:
        recover_calls.append(attempt)
        return {"attempt": attempt, "ok": True}

    out = nr.run_convergent_readiness(
        probe_once,
        recover_once,
        backoff_seconds=(2, 4, 8),
        max_wait_seconds=120,
        clock=clock,
    )
    assert out["ok"] is True
    assert out["attempts"] == 1
    assert recover_calls == []
    assert clock.sleeps == []


def _snapshot(*, ready: bool, backend_ok: bool, ws_ok: bool, err: str) -> dict:
    return {
        "ready": ready,
        "error_class": None if ready else err,
        "checks": {
            "systemd": {"ok": True},
            "http_novnc": {"required_path_ok": True},
            "tcp_websockify": {"ok": True},
            "tcp_backend_vnc": {"ok": backend_ok},
            "ws_local": {"ok": ws_ok},
            "ws_tailnet": {"performed": False, "ok": True},
        },
        "novnc_url": "https://test.ts.net/novnc/vnc.html?autoconnect=1&path=/websockify",
        "ws_stability_local": "verified" if ws_ok else "failed",
        "ws_stability_tailnet": "verified",
    }


def test_backend_selfheal_triggers_only_on_tcp_backend_failure(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "project_state.json").write_text("{}\n", encoding="utf-8")

    probes = iter(
        [
            _snapshot(ready=False, backend_ok=False, ws_ok=True, err="NOVNC_BACKEND_UNAVAILABLE"),
            _snapshot(ready=True, backend_ok=True, ws_ok=True, err=""),
        ]
    )
    restart_calls: list[int] = []

    monkeypatch.setattr(nr, "_repo_root", lambda: root)
    monkeypatch.setattr(nr, "_is_gate_active", lambda _root: False)
    monkeypatch.setattr(nr, "_capture_runtime_bundle", lambda *args, **kwargs: None)
    monkeypatch.setattr(nr, "_collect_probe_snapshot", lambda **kwargs: next(probes))
    monkeypatch.setattr(
        nr,
        "_run_backend_selfheal_restart",
        lambda _root: restart_calls.append(1) or {"method": "mock", "rc": 0, "ok": True},
    )
    monkeypatch.setattr(nr.time, "sleep", lambda _s: None)

    out = nr.ensure_novnc_ready(run_id="rate_limit_run", emit_artifacts=True)
    assert out.ok is True
    assert out.attempts == 2
    assert len(restart_calls) == 1
    attempt = root / "artifacts" / "novnc_readiness" / "rate_limit_run" / "novnc_backend_selfheal" / "attempt.json"
    assert attempt.exists()
    payload = json.loads(attempt.read_text(encoding="utf-8"))
    assert payload["attempted"] is True


def test_backend_selfheal_not_triggered_for_ws_only_failure(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "project_state.json").write_text("{}\n", encoding="utf-8")
    restart_calls: list[int] = []

    monkeypatch.setattr(nr, "_repo_root", lambda: root)
    monkeypatch.setattr(nr, "_is_gate_active", lambda _root: False)
    monkeypatch.setattr(nr, "_capture_runtime_bundle", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        nr,
        "_collect_probe_snapshot",
        lambda **kwargs: _snapshot(ready=False, backend_ok=True, ws_ok=False, err="NOVNC_WS_LOCAL_FAILED"),
    )
    monkeypatch.setattr(
        nr,
        "_run_backend_selfheal_restart",
        lambda _root: restart_calls.append(1) or {"method": "mock", "rc": 0, "ok": True},
    )

    out = nr.ensure_novnc_ready(run_id="ws_only_fail", emit_artifacts=True)
    assert out.ok is False
    assert out.error_class == "NOVNC_WS_LOCAL_FAILED"
    assert len(restart_calls) == 0
    attempt = root / "artifacts" / "novnc_readiness" / "ws_only_fail" / "novnc_backend_selfheal" / "attempt.json"
    assert not attempt.exists()


def test_backend_selfheal_is_rate_limited_to_one_attempt_per_run(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    (root / "config" / "project_state.json").write_text("{}\n", encoding="utf-8")
    restart_calls: list[int] = []

    monkeypatch.setattr(nr, "_repo_root", lambda: root)
    monkeypatch.setattr(nr, "_is_gate_active", lambda _root: False)
    monkeypatch.setattr(nr, "_capture_runtime_bundle", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        nr,
        "_collect_probe_snapshot",
        lambda **kwargs: _snapshot(ready=False, backend_ok=False, ws_ok=True, err="NOVNC_BACKEND_UNAVAILABLE"),
    )
    monkeypatch.setattr(
        nr,
        "_run_backend_selfheal_restart",
        lambda _root: restart_calls.append(1) or {"method": "mock", "rc": 1, "ok": False},
    )
    monkeypatch.setattr(nr.time, "sleep", lambda _s: None)

    first = nr.ensure_novnc_ready(run_id="same_run_id", emit_artifacts=True)
    second = nr.ensure_novnc_ready(run_id="same_run_id", emit_artifacts=True)
    assert first.ok is False
    assert second.ok is False
    assert len(restart_calls) == 1
