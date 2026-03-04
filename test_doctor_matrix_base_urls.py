"""Unit tests for doctor matrix base URL resolution."""

from __future__ import annotations

from system.doctor_matrix import _resolve_bases


def test_resolve_bases_defaults(monkeypatch) -> None:
    monkeypatch.delenv("OPENCLAW_FRONTDOOR_BASE_URL", raising=False)
    monkeypatch.delenv("OPENCLAW_HOST", raising=False)
    monkeypatch.delenv("OPENCLAW_LOCALHOST_BASE_URL", raising=False)
    monkeypatch.delenv("OPENCLAW_HQ_BASE_FRONTDOOR", raising=False)
    monkeypatch.delenv("OPENCLAW_HQ_BASE_LOCALHOST", raising=False)
    monkeypatch.delenv("OPENCLAW_HQ_BASE", raising=False)

    frontdoor, host, localhost = _resolve_bases()

    assert frontdoor == "https://aiops-1.tailc75c62.ts.net"
    assert host == "aiops-1"
    assert localhost == "http://127.0.0.1:8787"


def test_resolve_bases_prefers_new_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_FRONTDOOR_BASE_URL", "https://custom.tail.ts.net")
    monkeypatch.setenv("OPENCLAW_HOST", "aiops-custom")
    monkeypatch.setenv("OPENCLAW_LOCALHOST_BASE_URL", "http://127.0.0.1:9001")
    monkeypatch.setenv("OPENCLAW_HQ_BASE_FRONTDOOR", "https://legacy-frontdoor.example")
    monkeypatch.setenv("OPENCLAW_HQ_BASE_LOCALHOST", "http://127.0.0.1:9999")
    monkeypatch.setenv("OPENCLAW_HQ_BASE", "http://127.0.0.1:7777")

    frontdoor, host, localhost = _resolve_bases()

    assert frontdoor == "https://custom.tail.ts.net"
    assert host == "aiops-custom"
    assert localhost == "http://127.0.0.1:9001"
