"""Hermetic tests for verify_business_dod module.

All filesystem state is injected via tmp_path — no network, no subprocess.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from services.soma_kajabi.verify_business_dod import (
    check_raw_module_present,
    check_site_hostname,
    check_offer_urls,
    check_no_secrets,
    check_community_groups,
    check_manifest_dedupe,
    verify_business_dod,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def artifacts_root(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    return root


def _write_snapshot(artifacts_root: Path, modules: list, lessons: list | None = None) -> Path:
    accept_dir = artifacts_root / "soma_kajabi" / "acceptance" / "run_001"
    accept_dir.mkdir(parents=True, exist_ok=True)
    snap = {
        "home": {
            "modules": modules,
            "lessons": lessons or [],
        },
        "practitioner": {"modules": [], "lessons": []},
    }
    path = accept_dir / "final_library_snapshot.json"
    path.write_text(json.dumps(snap))
    return path


def _write_memberships_html(artifacts_root: Path, content: str) -> Path:
    discover_dir = artifacts_root / "soma_kajabi" / "discover" / "run_001"
    discover_dir.mkdir(parents=True, exist_ok=True)
    path = discover_dir / "memberships_page.html"
    path.write_text(content)
    return path


def _write_community_json(artifacts_root: Path, name: str, groups: list[str]) -> Path:
    discover_dir = artifacts_root / "soma_kajabi" / "discover" / "run_001"
    discover_dir.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "groups": [{"name": g} for g in groups]}
    path = discover_dir / "community.json"
    path.write_text(json.dumps(data))
    return path


def _write_community_html(artifacts_root: Path, content: str) -> Path:
    discover_dir = artifacts_root / "soma_kajabi" / "discover" / "run_001"
    discover_dir.mkdir(parents=True, exist_ok=True)
    path = discover_dir / "community.html"
    path.write_text(content)
    return path


def _write_manifest(artifacts_root: Path, rows: list[dict], fieldnames: list[str] | None = None) -> Path:
    accept_dir = artifacts_root / "soma_kajabi" / "acceptance" / "run_001"
    accept_dir.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = ["subject", "timestamp", "filename", "mapped_lesson", "status", "sha256"]
    path = accept_dir / "video_manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_artifacts_with_secret(artifacts_root: Path, content: str) -> Path:
    bundle_dir = artifacts_root / "post_deploy" / "run_001"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    path = bundle_dir / "status.json"
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# Check 1: RAW module present
# ---------------------------------------------------------------------------

class TestRawModulePresent:
    def test_raw_module_found_string(self, artifacts_root):
        snap_path = _write_snapshot(artifacts_root, ["RAW", "Module 2", "Module 3"])
        result = check_raw_module_present(artifacts_root, snap_path)
        assert result["pass"] is True
        assert "RAW" in result["details"]

    def test_raw_module_found_case_insensitive(self, artifacts_root):
        snap_path = _write_snapshot(artifacts_root, ["raw", "Module 2"])
        result = check_raw_module_present(artifacts_root, snap_path)
        assert result["pass"] is True

    def test_raw_needs_review_found(self, artifacts_root):
        snap_path = _write_snapshot(artifacts_root, ["RAW – Needs Review", "Module 2"])
        result = check_raw_module_present(artifacts_root, snap_path)
        assert result["pass"] is True

    def test_raw_module_missing(self, artifacts_root):
        snap_path = _write_snapshot(artifacts_root, ["Module 1", "Module 2"])
        result = check_raw_module_present(artifacts_root, snap_path)
        assert result["pass"] is False
        assert result["reason"] == "RAW_MODULE_MISSING"

    def test_snapshot_not_found(self, artifacts_root):
        result = check_raw_module_present(artifacts_root, Path("/nonexistent/path.json"))
        assert result["pass"] is False
        assert result["reason"] == "SNAPSHOT_NOT_FOUND"

    def test_auto_resolve_snapshot(self, artifacts_root):
        _write_snapshot(artifacts_root, ["RAW"])
        result = check_raw_module_present(artifacts_root)
        assert result["pass"] is True

    def test_raw_module_dict_format(self, artifacts_root):
        accept_dir = artifacts_root / "soma_kajabi" / "acceptance" / "run_dict"
        accept_dir.mkdir(parents=True, exist_ok=True)
        snap = {
            "home": {
                "modules": [{"name": "RAW"}, {"name": "Module 2"}],
                "lessons": [],
            },
            "practitioner": {"modules": [], "lessons": []},
        }
        snap_path = accept_dir / "final_library_snapshot.json"
        snap_path.write_text(json.dumps(snap))
        result = check_raw_module_present(artifacts_root, snap_path)
        assert result["pass"] is True


# ---------------------------------------------------------------------------
# Check 2: Site hostname
# ---------------------------------------------------------------------------

class TestSiteHostname:
    def test_hostname_match_from_products(self, artifacts_root):
        discover_dir = artifacts_root / "soma_kajabi" / "discover" / "run_001"
        discover_dir.mkdir(parents=True, exist_ok=True)
        products = [{"url": "https://zane-mccourtney.mykajabi.com/product/123"}]
        (discover_dir / "products.json").write_text(json.dumps(products))
        result = check_site_hostname(artifacts_root)
        assert result["pass"] is True

    def test_hostname_mismatch(self, artifacts_root):
        discover_dir = artifacts_root / "soma_kajabi" / "discover" / "run_001"
        discover_dir.mkdir(parents=True, exist_ok=True)
        products = [{"url": "https://wrong-host.mykajabi.com/product/123"}]
        (discover_dir / "products.json").write_text(json.dumps(products))
        result = check_site_hostname(artifacts_root, expected="zane-mccourtney.mykajabi.com")
        assert result["pass"] is False
        assert result["reason"] == "HOSTNAME_MISMATCH"

    def test_no_discover_uses_configured(self, artifacts_root):
        result = check_site_hostname(artifacts_root)
        assert result["pass"] is True
        assert "configured hostname" in result["details"]


# ---------------------------------------------------------------------------
# Check 3: Landing page reachable (skipped in hermetic; tested via orchestrator)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Check 4: Terms/Privacy URLs (skipped in hermetic; tested via orchestrator)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Check 5: Offer URLs present
# ---------------------------------------------------------------------------

class TestOfferUrls:
    def test_offer_urls_present(self, artifacts_root):
        _write_memberships_html(
            artifacts_root,
            '<a href="/offers/q6ntyjef/checkout">Buy</a>'
            '<a href="/offers/MHMmHyVZ/checkout">Buy</a>',
        )
        result = check_offer_urls(artifacts_root)
        assert result["pass"] is True

    def test_offer_urls_missing(self, artifacts_root):
        _write_memberships_html(artifacts_root, "<html><body>No offers</body></html>")
        result = check_offer_urls(artifacts_root)
        assert result["pass"] is False
        assert result["reason"] == "OFFER_URLS_MISSING"

    def test_no_discover_dir(self, artifacts_root):
        result = check_offer_urls(artifacts_root)
        assert result["pass"] is False
        assert result["reason"] == "DISCOVER_ARTIFACTS_MISSING"

    def test_no_memberships_html(self, artifacts_root):
        discover_dir = artifacts_root / "soma_kajabi" / "discover" / "run_001"
        discover_dir.mkdir(parents=True, exist_ok=True)
        result = check_offer_urls(artifacts_root)
        assert result["pass"] is False
        assert result["reason"] == "MEMBERSHIPS_PAGE_MISSING"


# ---------------------------------------------------------------------------
# Check 6: No secrets in artifacts
# ---------------------------------------------------------------------------

class TestSecretsCheck:
    def test_no_secrets_clean(self, artifacts_root):
        _write_artifacts_with_secret(artifacts_root, '{"status": "ok", "message": "all good"}')
        result = check_no_secrets(artifacts_root)
        assert result["pass"] is True

    def test_secrets_detected_openai(self, artifacts_root):
        _write_artifacts_with_secret(
            artifacts_root,
            '{"key": "sk-abcdefghijklmnopqrstuvwxyz1234567890"}',
        )
        result = check_no_secrets(artifacts_root)
        assert result["pass"] is False
        assert result["reason"] == "SECRETS_DETECTED_IN_ARTIFACTS"

    def test_secrets_detected_github_pat(self, artifacts_root):
        _write_artifacts_with_secret(
            artifacts_root,
            '{"token": "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"}',
        )
        result = check_no_secrets(artifacts_root)
        assert result["pass"] is False

    def test_allowlisted_secret_ignored(self, artifacts_root):
        _write_artifacts_with_secret(
            artifacts_root,
            '{"key": "sk-test-abcdefghijklmnopqrstuvwxyz"}',
        )
        result = check_no_secrets(artifacts_root)
        assert result["pass"] is True

    def test_binary_files_skipped(self, artifacts_root):
        bundle_dir = artifacts_root / "post_deploy" / "run_001"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "image.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        result = check_no_secrets(artifacts_root)
        assert result["pass"] is True

    def test_redaction_no_raw_secrets(self, artifacts_root):
        _write_artifacts_with_secret(
            artifacts_root,
            '{"key": "sk-abcdefghijklmnopqrstuvwxyz1234567890"}',
        )
        result = check_no_secrets(artifacts_root)
        assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in result["details"]


# ---------------------------------------------------------------------------
# Check 7: Community + groups
# ---------------------------------------------------------------------------

class TestCommunityGroups:
    def test_community_match_json(self, artifacts_root):
        _write_community_json(artifacts_root, "Soma Community", ["Home Users", "Practitioners"])
        result = check_community_groups(artifacts_root)
        assert result["pass"] is True

    def test_community_missing_group(self, artifacts_root):
        _write_community_json(artifacts_root, "Soma Community", ["Home Users"])
        result = check_community_groups(artifacts_root)
        assert result["pass"] is False
        assert "Practitioners" in result["details"]

    def test_community_wrong_name(self, artifacts_root):
        _write_community_json(artifacts_root, "Wrong Community", ["Home Users", "Practitioners"])
        result = check_community_groups(artifacts_root)
        assert result["pass"] is False

    def test_community_html_fallback(self, artifacts_root):
        _write_community_html(
            artifacts_root,
            "<html><h1>Soma Community</h1><div>Home Users</div><div>Practitioners</div></html>",
        )
        result = check_community_groups(artifacts_root)
        assert result["pass"] is True

    def test_community_html_missing_group(self, artifacts_root):
        _write_community_html(
            artifacts_root,
            "<html><h1>Soma Community</h1><div>Home Users</div></html>",
        )
        result = check_community_groups(artifacts_root)
        assert result["pass"] is False

    def test_no_community_artifacts(self, artifacts_root):
        result = check_community_groups(artifacts_root)
        assert result["pass"] is False
        assert result["reason"] == "DISCOVER_ARTIFACTS_MISSING"


# ---------------------------------------------------------------------------
# Check 8: Manifest dedupe
# ---------------------------------------------------------------------------

class TestManifestDedupe:
    def test_no_duplicates(self, artifacts_root):
        rows = [
            {"subject": "S1", "filename": "f1.mp4", "sha256": "aaa111", "status": "attached"},
            {"subject": "S2", "filename": "f2.mp4", "sha256": "bbb222", "status": "attached"},
        ]
        path = _write_manifest(artifacts_root, rows)
        result = check_manifest_dedupe(artifacts_root, path)
        assert result["pass"] is True

    def test_duplicates_found(self, artifacts_root):
        rows = [
            {"subject": "S1", "filename": "f1.mp4", "sha256": "aaa111", "status": "attached"},
            {"subject": "S2", "filename": "f2.mp4", "sha256": "aaa111", "status": "attached"},
        ]
        path = _write_manifest(artifacts_root, rows)
        result = check_manifest_dedupe(artifacts_root, path)
        assert result["pass"] is False
        assert result["reason"] == "MANIFEST_DUPLICATES_FOUND"

    def test_duplicates_marked_deduped(self, artifacts_root):
        rows = [
            {"subject": "S1", "filename": "f1.mp4", "sha256": "aaa111", "status": "attached", "deduped": "true"},
            {"subject": "S2", "filename": "f2.mp4", "sha256": "aaa111", "status": "deduped", "deduped": ""},
        ]
        fieldnames = ["subject", "timestamp", "filename", "mapped_lesson", "status", "sha256", "deduped"]
        path = _write_manifest(artifacts_root, rows, fieldnames=fieldnames)
        result = check_manifest_dedupe(artifacts_root, path)
        assert result["pass"] is True

    def test_no_hash_column(self, artifacts_root):
        rows = [
            {"subject": "S1", "filename": "f1.mp4", "status": "attached"},
        ]
        fieldnames = ["subject", "timestamp", "filename", "mapped_lesson", "status"]
        path = _write_manifest(artifacts_root, rows, fieldnames=fieldnames)
        result = check_manifest_dedupe(artifacts_root, path)
        assert result["pass"] is False
        assert result["reason"] == "MANIFEST_NO_HASH_COLUMN"

    def test_manifest_not_found(self, artifacts_root):
        result = check_manifest_dedupe(artifacts_root, Path("/nonexistent/manifest.csv"))
        assert result["pass"] is False
        assert result["reason"] == "MANIFEST_NOT_FOUND"

    def test_auto_resolve_manifest(self, artifacts_root):
        rows = [
            {"subject": "S1", "filename": "f1.mp4", "sha256": "unique1", "status": "attached"},
        ]
        _write_manifest(artifacts_root, rows)
        result = check_manifest_dedupe(artifacts_root)
        assert result["pass"] is True


# ---------------------------------------------------------------------------
# Orchestrator: verify_business_dod
# ---------------------------------------------------------------------------

class TestVerifyBusinessDod:
    def test_all_pass_hermetic(self, artifacts_root):
        snap_path = _write_snapshot(artifacts_root, ["RAW", "Module 2"])
        _write_memberships_html(
            artifacts_root,
            '<a href="/offers/q6ntyjef/checkout">Buy</a>'
            '<a href="/offers/MHMmHyVZ/checkout">Buy</a>',
        )
        _write_community_json(artifacts_root, "Soma Community", ["Home Users", "Practitioners"])
        manifest_path = _write_manifest(artifacts_root, [
            {"subject": "S1", "filename": "f1.mp4", "sha256": "unique1", "status": "attached"},
        ])

        result = verify_business_dod(
            artifacts_root=artifacts_root,
            snapshot_path=snap_path,
            manifest_path=manifest_path,
            skip_network_checks=True,
        )

        assert result["pass"] is True
        assert result["checks_passed"] == result["checks_total"]
        assert result["checks_total"] == 8

        out_dir = Path(result["artifact_dir"])
        assert (out_dir / "business_dod_checks.json").is_file()
        assert (out_dir / "SUMMARY.md").is_file()

    def test_partial_fail(self, artifacts_root):
        snap_path = _write_snapshot(artifacts_root, ["Module 1"])
        _write_memberships_html(
            artifacts_root,
            '<a href="/offers/q6ntyjef/checkout">Buy</a>'
            '<a href="/offers/MHMmHyVZ/checkout">Buy</a>',
        )
        _write_community_json(artifacts_root, "Soma Community", ["Home Users", "Practitioners"])
        manifest_path = _write_manifest(artifacts_root, [
            {"subject": "S1", "filename": "f1.mp4", "sha256": "unique1", "status": "attached"},
        ])

        result = verify_business_dod(
            artifacts_root=artifacts_root,
            snapshot_path=snap_path,
            manifest_path=manifest_path,
            skip_network_checks=True,
        )

        assert result["pass"] is False
        assert result["checks"]["raw_module_present"]["pass"] is False
        assert len(result["warnings"]) >= 1


# ---------------------------------------------------------------------------
# Proof bundle integration
# ---------------------------------------------------------------------------

class TestPostDeployBundleIncludesBusinessDod:
    def test_bundle_includes_business_dod_fields(self, tmp_path):
        from ops.scripts.post_deploy_proof_bundle import generate_bundle

        bdod_dir = tmp_path / "soma_kajabi" / "business_dod" / "bdod_test"
        bdod_dir.mkdir(parents=True)
        (bdod_dir / "business_dod_checks.json").write_text(json.dumps({
            "pass": True,
            "checks": {
                "raw_module_present": {"pass": True},
                "no_secrets_in_artifacts": {"pass": True},
            },
        }))

        out = tmp_path / "proof_bdod"
        result = generate_bundle(
            out,
            health_public={"ok": True, "build_sha": "abc", "server_time": "2026-03-02T00:00:00Z"},
            soma_status={"ok": True, "mirror_pass": True, "exceptions_count": 0, "stage": "IDLE"},
            ssh_verify=(True, "PASS"),
            pointers={
                "business_dod": str(bdod_dir / "business_dod_checks.json"),
                "business_dod_dir": str(bdod_dir),
            },
            deploy_run_id="d1",
            origin_sha="sha1",
        )

        assert result["business_dod_pass"] is True
        assert result["business_dod_path"] == str(bdod_dir)
        assert result["business_dod_failed_checks"] == []

        proof_md = (out / "PROOF_BLOCK.md").read_text()
        assert "Business DoD" in proof_md
        assert "business_dod_pass" in proof_md

    def test_bundle_shows_failed_business_dod_checks(self, tmp_path):
        from ops.scripts.post_deploy_proof_bundle import generate_bundle

        bdod_dir = tmp_path / "soma_kajabi" / "business_dod" / "bdod_fail"
        bdod_dir.mkdir(parents=True)
        (bdod_dir / "business_dod_checks.json").write_text(json.dumps({
            "pass": False,
            "checks": {
                "raw_module_present": {"pass": False},
                "no_secrets_in_artifacts": {"pass": True},
            },
        }))

        out = tmp_path / "proof_bdod_fail"
        result = generate_bundle(
            out,
            health_public={"ok": True, "build_sha": "abc", "server_time": "2026-03-02T00:00:00Z"},
            soma_status={"ok": True, "mirror_pass": True, "exceptions_count": 0, "stage": "IDLE"},
            ssh_verify=(True, "PASS"),
            pointers={
                "business_dod": str(bdod_dir / "business_dod_checks.json"),
                "business_dod_dir": str(bdod_dir),
            },
            deploy_run_id="d1",
            origin_sha="sha1",
        )

        assert result["business_dod_pass"] is False
        assert "raw_module_present" in result["business_dod_failed_checks"]

    def test_bundle_no_business_dod_artifact(self, tmp_path):
        from ops.scripts.post_deploy_proof_bundle import generate_bundle

        out = tmp_path / "proof_no_bdod"
        result = generate_bundle(
            out,
            health_public={"ok": True, "build_sha": "abc", "server_time": "2026-03-02T00:00:00Z"},
            soma_status={"ok": True, "mirror_pass": True, "exceptions_count": 0, "stage": "IDLE"},
            ssh_verify=(True, "PASS"),
            pointers={},
            deploy_run_id="d1",
            origin_sha="sha1",
        )

        assert result["business_dod_pass"] == "UNKNOWN"
        assert result["business_dod_path"] == ""
