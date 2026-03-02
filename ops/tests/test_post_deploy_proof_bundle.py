"""Hermetic tests for post_deploy_proof_bundle.generate_bundle().

All data sources are injected — no network, no subprocess, no filesystem
side-effects outside of tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.scripts.post_deploy_proof_bundle import generate_bundle


# ---------------------------------------------------------------------------
# Fixtures — canonical mock payloads
# ---------------------------------------------------------------------------

@pytest.fixture
def health_public_ok() -> dict:
    return {
        "ok": True,
        "build_sha": "abc1234",
        "deploy_sha": "abc1234",
        "server_time": "2026-03-02T00:00:00Z",
        "canonical_url": "https://aiops-1.tailc75c62.ts.net",
        "routes": ["/api/dod/last", "/api/projects"],
    }


@pytest.fixture
def health_public_fail() -> dict:
    return {"ok": False, "error": "http_502", "raw": "Bad Gateway"}


@pytest.fixture
def soma_status_ok() -> dict:
    return {
        "ok": True,
        "acceptance_path": "artifacts/soma_kajabi/acceptance/20260301/final_library_snapshot.json",
        "mirror_pass": True,
        "exceptions_count": 0,
        "stage": "IDLE",
    }


@pytest.fixture
def soma_status_waiting() -> dict:
    return {
        "ok": True,
        "acceptance_path": "artifacts/soma_kajabi/acceptance/20260301/final_library_snapshot.json",
        "mirror_pass": True,
        "exceptions_count": 0,
        "stage": "WAITING_FOR_HUMAN",
    }


@pytest.fixture
def pointers_ok(tmp_path) -> dict:
    doctor_dir = tmp_path / "doctor" / "20260302"
    doctor_dir.mkdir(parents=True)
    (doctor_dir / "doctor.json").write_text(json.dumps({"overall": "PASS"}))

    canary_dir = tmp_path / "canary" / "canary_20260302"
    canary_dir.mkdir(parents=True)
    (canary_dir / "result.json").write_text(json.dumps({"status": "PASS"}))
    (canary_dir / "PROOF.md").write_text("# Canary PASS")

    deploy_dir = tmp_path / "deploy" / "20260302_120000-abcd"
    deploy_dir.mkdir(parents=True)
    (deploy_dir / "deploy_result.json").write_text(json.dumps({"overall": "PASS", "run_id": "20260302_120000-abcd"}))
    (deploy_dir / "deploy_receipt.json").write_text(json.dumps({"deploy_sha": "abc1234"}))

    return {
        "deploy_result": str(deploy_dir / "deploy_result.json"),
        "deploy_dir": str(deploy_dir),
        "deploy_receipt": str(deploy_dir / "deploy_receipt.json"),
        "doctor": str(doctor_dir / "doctor.json"),
        "doctor_dir": str(doctor_dir),
        "canary_result": str(canary_dir / "result.json"),
        "canary_proof": str(canary_dir / "PROOF.md"),
        "canary_dir": str(canary_dir),
    }


# ---------------------------------------------------------------------------
# Tests — PASS scenario
# ---------------------------------------------------------------------------

class TestBundlePass:
    def test_all_pass(self, tmp_path, health_public_ok, soma_status_ok, pointers_ok):
        out = tmp_path / "proof_test"
        result = generate_bundle(
            out,
            health_public=health_public_ok,
            soma_status=soma_status_ok,
            ssh_verify=(True, "RESULT: PASS"),
            pointers=pointers_ok,
            deploy_run_id="deploy_test_123",
            origin_sha="abc1234full",
        )

        assert result["overall"] == "PASS"
        assert result["build_sha"] == "abc1234"
        assert result["deploy_run_id"] == "deploy_test_123"
        assert result["origin_sha"] == "abc1234full"
        assert result["health_public_ok"] is True
        assert result["ssh_tailscale_only_verify"] == "PASS"
        assert result["doctor"] == "PASS"
        assert result["canary"] == "PASS"
        assert result["failures"] == []
        assert result["soma_kajabi"]["acceptance_path"] is not None
        assert result["soma_kajabi"]["mirror_pass"] is True
        assert result["soma_kajabi"]["exceptions_count"] == 0

    def test_artifacts_written(self, tmp_path, health_public_ok, soma_status_ok, pointers_ok):
        out = tmp_path / "proof_written"
        generate_bundle(
            out,
            health_public=health_public_ok,
            soma_status=soma_status_ok,
            ssh_verify=(True, "RESULT: PASS"),
            pointers=pointers_ok,
            deploy_run_id="d1",
            origin_sha="sha1",
        )

        assert (out / "health_public.json").is_file()
        assert (out / "status_soma_kajabi.json").is_file()
        assert (out / "ssh_tailscale_only_verify.txt").is_file()
        assert (out / "pointers.json").is_file()
        assert (out / "RESULT.json").is_file()
        assert (out / "PROOF_BLOCK.md").is_file()


# ---------------------------------------------------------------------------
# Tests — FAILURE scenarios
# ---------------------------------------------------------------------------

class TestBundleFailure:
    def test_health_fail_marks_failure(self, tmp_path, health_public_fail, soma_status_ok, pointers_ok):
        out = tmp_path / "proof_hfail"
        result = generate_bundle(
            out,
            health_public=health_public_fail,
            soma_status=soma_status_ok,
            ssh_verify=(True, "RESULT: PASS"),
            pointers=pointers_ok,
            deploy_run_id="d1",
            origin_sha="sha1",
        )
        assert result["overall"] == "FAILURE"
        assert any("health_public" in f for f in result["failures"])

    def test_ssh_fail_marks_failure(self, tmp_path, health_public_ok, soma_status_ok, pointers_ok):
        out = tmp_path / "proof_sshfail"
        result = generate_bundle(
            out,
            health_public=health_public_ok,
            soma_status=soma_status_ok,
            ssh_verify=(False, "FAIL: public address"),
            pointers=pointers_ok,
            deploy_run_id="d1",
            origin_sha="sha1",
        )
        assert result["overall"] == "FAILURE"
        assert any("ssh" in f.lower() for f in result["failures"])

    def test_doctor_fail_marks_failure(self, tmp_path, health_public_ok, soma_status_ok):
        doc_dir = tmp_path / "doc"
        doc_dir.mkdir()
        (doc_dir / "doctor.json").write_text(json.dumps({"overall": "FAIL"}))
        pointers = {"doctor": str(doc_dir / "doctor.json")}

        out = tmp_path / "proof_docfail"
        result = generate_bundle(
            out,
            health_public=health_public_ok,
            soma_status=soma_status_ok,
            ssh_verify=(True, "PASS"),
            pointers=pointers,
            deploy_run_id="d1",
            origin_sha="sha1",
        )
        assert result["overall"] == "FAILURE"
        assert any("doctor" in f.lower() for f in result["failures"])

    def test_canary_fail_marks_failure(self, tmp_path, health_public_ok, soma_status_ok):
        canary_dir = tmp_path / "can"
        canary_dir.mkdir()
        (canary_dir / "result.json").write_text(json.dumps({"status": "DEGRADED"}))
        pointers = {"canary_result": str(canary_dir / "result.json")}

        out = tmp_path / "proof_canfail"
        result = generate_bundle(
            out,
            health_public=health_public_ok,
            soma_status=soma_status_ok,
            ssh_verify=(True, "PASS"),
            pointers=pointers,
            deploy_run_id="d1",
            origin_sha="sha1",
        )
        assert result["overall"] == "FAILURE"
        assert any("canary" in f.lower() for f in result["failures"])


# ---------------------------------------------------------------------------
# Tests — Soma WAITING_FOR_HUMAN is not a bundle failure
# ---------------------------------------------------------------------------

class TestSomaWaitingForHuman:
    def test_waiting_recorded_not_failure(self, tmp_path, health_public_ok, soma_status_waiting, pointers_ok):
        out = tmp_path / "proof_soma_wait"
        result = generate_bundle(
            out,
            health_public=health_public_ok,
            soma_status=soma_status_waiting,
            ssh_verify=(True, "PASS"),
            pointers=pointers_ok,
            deploy_run_id="d1",
            origin_sha="sha1",
        )
        assert result["overall"] == "PASS"
        assert result["soma_kajabi"]["waiting_for_human"] is True
        assert result["soma_kajabi"]["stage"] == "WAITING_FOR_HUMAN"


# ---------------------------------------------------------------------------
# Tests — PROOF_BLOCK.md content validation
# ---------------------------------------------------------------------------

class TestProofBlockContent:
    REQUIRED_KEYS = [
        "build_sha",
        "deploy_sha",
        "origin/main sha",
        "deploy_run_id",
        "health_public",
        "ssh_tailscale_only",
        "doctor",
        "canary",
        "acceptance_path",
        "mirror_pass",
        "exceptions_count",
        "Overall",
    ]

    def test_proof_block_contains_required_keys(self, tmp_path, health_public_ok, soma_status_ok, pointers_ok):
        out = tmp_path / "proof_keys"
        generate_bundle(
            out,
            health_public=health_public_ok,
            soma_status=soma_status_ok,
            ssh_verify=(True, "PASS"),
            pointers=pointers_ok,
            deploy_run_id="d1",
            origin_sha="sha1",
        )
        proof_md = (out / "PROOF_BLOCK.md").read_text()
        for key in self.REQUIRED_KEYS:
            assert key in proof_md, f"PROOF_BLOCK.md missing required key: {key}"

    def test_proof_block_shows_failure_section_on_fail(self, tmp_path, health_public_fail, soma_status_ok, pointers_ok):
        out = tmp_path / "proof_fail_section"
        generate_bundle(
            out,
            health_public=health_public_fail,
            soma_status=soma_status_ok,
            ssh_verify=(True, "PASS"),
            pointers=pointers_ok,
            deploy_run_id="d1",
            origin_sha="sha1",
        )
        proof_md = (out / "PROOF_BLOCK.md").read_text()
        assert "## Failures" in proof_md
        assert "FAILURE" in proof_md


# ---------------------------------------------------------------------------
# Tests — Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_rerun_same_dir(self, tmp_path, health_public_ok, soma_status_ok, pointers_ok):
        out = tmp_path / "proof_idem"
        r1 = generate_bundle(out, health_public=health_public_ok, soma_status=soma_status_ok,
                             ssh_verify=(True, "PASS"), pointers=pointers_ok, deploy_run_id="d1", origin_sha="sha1")
        r2 = generate_bundle(out, health_public=health_public_ok, soma_status=soma_status_ok,
                             ssh_verify=(True, "PASS"), pointers=pointers_ok, deploy_run_id="d1", origin_sha="sha1")
        assert r1["overall"] == r2["overall"] == "PASS"
        assert (out / "RESULT.json").is_file()
