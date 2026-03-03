"""State-machine tests for convergent noVNC readiness."""
from __future__ import annotations

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
