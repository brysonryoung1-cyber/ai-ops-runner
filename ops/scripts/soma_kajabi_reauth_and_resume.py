#!/usr/bin/env python3
"""Soma Kajabi Reauth and Resume — Cloudflare/login is the only human step.

Flow:
1. ensure_novnc_ready() (hard fail-closed with journal link)
2. Start persistent Chromium profile, open Kajabi admin/products
3. If Cloudflare/login detected:
   - emit WAITING_FOR_HUMAN + noVNC URL
   - poll every N seconds for: "Home User Library" AND "Practitioner Library", url matches /admin/products
   - when criteria met: export storage_state, write artifacts, run soma_kajabi_auto_finish
4. If criteria never met within 25 minutes: fail-closed with KAJABI_REAUTH_TIMEOUT

Artifacts: artifacts/soma_kajabi/reauth_and_resume/<run_id>/
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

KAJABI_ADMIN = "https://app.kajabi.com/admin"
KAJABI_SITES = "https://app.kajabi.com/admin/sites"
KAJABI_PRODUCTS_URL = "https://app.kajabi.com/admin/products"
STORAGE_STATE_PATH = Path("/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json")
KAJABI_CHROME_PROFILE_DIR = Path("/var/lib/openclaw/kajabi_chrome_profile")
TARGET_PRODUCTS = ["Home User Library", "Practitioner Library"]
TIMEOUT_SEC = 25 * 60  # 25 minutes
POLL_INTERVAL = 10
KAJABI_REAUTH_TIMEOUT = "KAJABI_REAUTH_TIMEOUT"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo_root() -> Path:
    env = os.environ.get("OPENCLAW_REPO_ROOT")
    if env and Path(env).exists():
        return Path(env)
    cwd = Path.cwd()
    for _ in range(10):
        if (cwd / "config" / "project_state.json").exists():
            return cwd
        if cwd == cwd.parent:
            break
        cwd = cwd.parent
    return Path(env or "/opt/ai-ops-runner")


def _artifact_dir() -> Path:
    env = os.environ.get("ARTIFACT_DIR")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    root = _repo_root()
    run_id = f"reauth_and_resume_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    out = root / "artifacts" / "soma_kajabi" / "reauth_and_resume" / run_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def _is_cloudflare_blocked(title: str, content: str) -> bool:
    combined = ((title or "") + " " + (content or ""))[:8192].lower()
    if "cloudflare" not in combined:
        return False
    if "attention required" in combined or "blocked" in combined or "sorry, you have been blocked" in combined:
        return True
    return False


def _page_has_both_products(content: str) -> tuple[bool, list[str]]:
    content_lower = (content or "").lower()
    found = [t for t in TARGET_PRODUCTS if t.lower() in content_lower]
    return len(found) == len(TARGET_PRODUCTS), found


def _url_matches_products(url: str) -> bool:
    return "/admin/products" in (url or "")


def main() -> int:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    out_dir = _artifact_dir()
    run_id = out_dir.name
    screenshots_dir = out_dir / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)

    script_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / "ops" / "scripts"))
    from novnc_ready import ensure_novnc_ready
    from src.playwright_safe import safe_content_excerpt, safe_screenshot, safe_title, safe_url

    # 1) ensure_novnc_ready — hard fail-closed with journal link
    ready, tailscale_url, err_class, journal_artifact = ensure_novnc_ready(out_dir, run_id)
    if not ready and err_class:
        summary = {
            "ok": False,
            "error_class": err_class,
            "message": f"noVNC backend unavailable. Journal: {journal_artifact or 'N/A'}",
            "artifact_dir": str(out_dir),
            "run_id": run_id,
            "journal_artifact": journal_artifact,
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        (out_dir / "instructions.txt").write_text(
            f"noVNC failed. See journal: {journal_artifact or 'openclaw_novnc_journal.txt'}"
        )
        print(json.dumps(summary))
        return 1

    (out_dir / "instructions.txt").write_text(
        f"{tailscale_url}\nIf you get 'Failed to connect', refresh the page; the viewer auto-restarts."
    )
    print("noVNC READY", file=sys.stderr)
    print(tailscale_url, file=sys.stderr)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        summary = {
            "ok": False,
            "error_class": "PLAYWRIGHT_NOT_INSTALLED",
            "message": "pip install playwright && playwright install chromium",
            "artifact_dir": str(out_dir),
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary))
        return 1

    env = os.environ.copy()
    env["DISPLAY"] = ":99"

    done = threading.Event()
    result_holder: list[dict] = []

    def run_playwright():
        try:
            with sync_playwright() as p:
                profile_dir = str(KAJABI_CHROME_PROFILE_DIR)
                try:
                    KAJABI_CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                    KAJABI_CHROME_PROFILE_DIR.chmod(0o700)
                except OSError:
                    pass
                browser = p.chromium.launch(
                    headless=False,
                    env=env,
                    args=[f"--user-data-dir={profile_dir}"],
                )
                context = browser.new_context()
                page = context.new_page()
                # Bootstrap: admin → sites → click Soma → products
                page.goto(KAJABI_ADMIN, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                page.goto(KAJABI_SITES, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                try:
                    link = page.get_by_role("link", name="Soma")
                    if link.count() > 0:
                        link.first.click()
                        page.wait_for_load_state("load", timeout=15000)
                except Exception:
                    for sel in ["text=Soma", "text=zane-mccourtney", '[href*="zane-mccourtney"]']:
                        try:
                            el = page.query_selector(sel)
                            if el and el.is_visible():
                                el.click()
                                page.wait_for_load_state("load", timeout=15000)
                                break
                        except Exception:
                            pass
                page.goto(KAJABI_PRODUCTS_URL, wait_until="domcontentloaded", timeout=60000)

                start = time.time()
                emitted_waiting = False
                while time.time() - start < TIMEOUT_SEC:
                    title = safe_title(page)
                    content = safe_content_excerpt(page, 8192)
                    current_url = safe_url(page)
                    safe_screenshot(page, str(screenshots_dir / f"poll_{int(time.time())}.png"))
                    cloudflare = _is_cloudflare_blocked(title, content)
                    has_both, found = _page_has_both_products(content)
                    url_ok = _url_matches_products(current_url)

                    if cloudflare:
                        if not emitted_waiting:
                            print("\n--- WAITING_FOR_HUMAN ---", file=sys.stderr)
                            print("noVNC READY", file=sys.stderr)
                            print(tailscale_url, file=sys.stderr)
                            print("1. Open the URL in your browser (Tailscale network).", file=sys.stderr)
                            print("2. Complete the Cloudflare challenge and log in.", file=sys.stderr)
                            print("The run will auto-resume after you see both products.", file=sys.stderr)
                            sys.stderr.flush()
                            emitted_waiting = True
                        time.sleep(POLL_INTERVAL)
                        continue
                    if has_both and url_ok:
                        STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                        context.storage_state(path=str(STORAGE_STATE_PATH))
                        try:
                            STORAGE_STATE_PATH.chmod(0o600)
                        except OSError:
                            pass
                        result_holder.append({
                            "ok": True,
                            "final_url": current_url,
                            "title": title,
                            "products_found": found,
                            "cloudflare_detected": False,
                        })
                        browser.close()
                        done.set()
                        return
                    time.sleep(POLL_INTERVAL)

                # Timeout
                safe_screenshot(page, str(out_dir / "screenshots" / "timeout_final.png"))
                result_holder.append({
                    "ok": False,
                    "error_class": KAJABI_REAUTH_TIMEOUT,
                    "final_url": safe_url(page),
                    "title": safe_title(page),
                    "products_found": [],
                    "cloudflare_detected": _is_cloudflare_blocked(safe_title(page), safe_content_excerpt(page, 8192)),
                })
                browser.close()
        except Exception as e:
            result_holder.append({
                "ok": False,
                "error_class": "KAJABI_REAUTH_ERROR",
                "message": str(e)[:500],
            })
        finally:
            done.set()

    t = threading.Thread(target=run_playwright)
    t.start()
    done.wait(timeout=TIMEOUT_SEC + 30)
    if not result_holder:
        result_holder.append({
            "ok": False,
            "error_class": KAJABI_REAUTH_TIMEOUT,
            "message": "Reauth thread did not complete",
        })

    summary = result_holder[0].copy()
    summary["artifact_dir"] = str(out_dir)
    summary["run_id"] = run_id
    summary["tailscale_url"] = tailscale_url
    summary["profile_dir_used"] = str(KAJABI_CHROME_PROFILE_DIR)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    if not summary.get("ok"):
        print(json.dumps(summary))
        return 1

    # Write artifacts: screenshot + title + final_url + timestamp
    final_screenshot = screenshots_dir / "final.png"
    # Copy most recent poll screenshot to final.png
    screenshots = sorted(screenshots_dir.glob("poll_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    if screenshots:
        shutil.copy(screenshots[0], final_screenshot)
    artifacts_meta = {
        "screenshot": str(final_screenshot) if final_screenshot.exists() else None,
        "title": summary.get("title"),
        "final_url": summary.get("final_url"),
        "timestamp_utc": _now_iso(),
    }
    (out_dir / "artifacts_meta.json").write_text(json.dumps(artifacts_meta, indent=2))

    # Immediately run soma_kajabi_auto_finish
    venv_python = root / ".venv-hostd" / "bin" / "python"
    if not venv_python.exists():
        venv_python = Path(sys.executable)
    auto_finish_script = root / "ops" / "scripts" / "soma_kajabi_auto_finish.py"
    print("\n--- Running Auto-Finish ---", file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()
    rc = subprocess.call(
        [str(venv_python), str(auto_finish_script)],
        cwd=str(root),
        timeout=2000,
    )
    if rc != 0:
        summary["auto_finish_exit_code"] = rc
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps({
            "ok": False,
            "reauth_ok": True,
            "auto_finish_exit_code": rc,
            "run_id": run_id,
            "artifact_dir": str(out_dir),
        }))
        return rc

    print(json.dumps({
        "ok": True,
        "artifact_dir": str(out_dir),
        "run_id": run_id,
        "products_found": summary.get("products_found", []),
        "tailscale_url": tailscale_url,
        "storage_state_refreshed": _now_iso(),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
