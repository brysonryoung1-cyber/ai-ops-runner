"""Hermetic tests for Kajabi discover page-capture helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "kajabi_discover",
        REPO_ROOT / "ops" / "scripts" / "kajabi_discover.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class _FakeResponse:
    def __init__(self, status: int):
        self._status = status

    def status(self) -> int:
        return self._status


class _FakePage:
    def __init__(self, routes: dict[str, dict[str, str | int]]):
        self.routes = routes
        self.url = ""
        self._content = ""

    def goto(self, url: str, wait_until: str = "load", timeout: int = 30000):
        route = self.routes[url]
        self.url = str(route.get("final_url") or url)
        self._content = str(route.get("content") or "")
        return _FakeResponse(int(route.get("status") or 0))

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def content(self) -> str:
        return self._content


def test_capture_required_pages_writes_memberships_community_terms_privacy(tmp_path):
    mod = _load_module()
    out_dir = tmp_path / "discover_run"
    out_dir.mkdir(parents=True, exist_ok=True)

    site = "https://example.test"
    page = _FakePage(
        {
            f"{site}/memberships": {"status": 404, "content": "<html>missing</html>"},
            f"{site}/memberships-soma": {
                "status": 200,
                "content": (
                    '<a href="/offers/q6ntyjef/checkout">Home</a>'
                    '<a href="/offers/MHMmHyVZ/checkout">Practitioner</a>'
                ),
            },
            f"{site}/community": {
                "status": 200,
                "content": "<html><h1>Soma Community</h1><div>Home Users</div><div>Practitioners</div></html>",
            },
            f"{site}/privacy-policy": {"status": 200, "content": "<html>privacy</html>"},
            f"{site}/terms": {"status": 200, "content": "<html>terms</html>"},
        }
    )

    result = mod._capture_required_pages(
        page,
        out_dir=out_dir,
        site_origin=site,
        safe_screenshot=lambda _p, path: Path(path).write_text("ok", encoding="utf-8"),
    )

    assert result["memberships_page_captured"] is True
    assert result["community_page_captured"] is True
    assert result["privacy_page_captured"] is True
    assert result["terms_page_captured"] is True
    assert result["offer_urls_found"] == ["/offers/q6ntyjef/checkout", "/offers/MHMmHyVZ/checkout"]

    assert (out_dir / "memberships_page.html").is_file()
    assert (out_dir / "community.html").is_file()
    assert (out_dir / "community.json").is_file()
    assert (out_dir / "privacy.html").is_file()
    assert (out_dir / "terms.html").is_file()
    assert (out_dir / "statuses.json").is_file()

    statuses_doc = json.loads((out_dir / "statuses.json").read_text())
    memberships_status = statuses_doc["statuses"]["memberships"]
    assert memberships_status["status"] == 200
    assert memberships_status["path"] == "/memberships-soma"

    community_doc = json.loads((out_dir / "community.json").read_text())
    assert community_doc["name"] == "Soma Community"
    group_names = [g["name"] for g in community_doc["groups"]]
    assert "Home Users" in group_names
    assert "Practitioners" in group_names

