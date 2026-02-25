#!/usr/bin/env python3
"""Kajabi interactive capture — headed browser over Tailscale-only noVNC.

When Playwright hits Cloudflare block, this action starts a headed Chromium session
in Xvfb, exposes it via noVNC (bound to 0.0.0.0 or Tailscale IP; firewall restricts
to 100.64.0.0/10), and waits for a human to complete the Cloudflare check/login.
Then exports storage_state and reruns discover.

Usage: Run on aiops-1. Requires: Xvfb, x11vnc, noVNC (or websockify).
  python3 ops/scripts/kajabi_capture_interactive.py

Artifacts: artifacts/soma_kajabi/capture_interactive/<run_id>/
  - summary.json (final_url, title, products_found, cloudflare_detected)
  - screenshots/*.png
  - instructions.txt (single-line Tailscale URL)

Fail-closed: KAJABI_INTERACTIVE_CAPTURE_TIMEOUT after 20 minutes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Use bootstrap flow: admin → sites → click Soma → products (avoids 404 on direct /admin/products)
KAJABI_ADMIN = "https://app.kajabi.com/admin"
KAJABI_SITES = "https://app.kajabi.com/admin/sites"
KAJABI_PRODUCTS_URL = "https://app.kajabi.com/admin/products"
STORAGE_STATE_PATH = Path("/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json")
KAJABI_CHROME_PROFILE_DIR = Path("/var/lib/openclaw/kajabi_chrome_profile")
TARGET_PRODUCTS = ["Home User Library", "Practitioner Library"]
TIMEOUT_SEC = 20 * 60  # 20 minutes
KAJABI_INTERACTIVE_CAPTURE_TIMEOUT = "KAJABI_INTERACTIVE_CAPTURE_TIMEOUT"


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
    run_id = f"capture_interactive_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    out = root / "artifacts" / "soma_kajabi" / "capture_interactive" / run_id
    out.mkdir(parents=True, exist_ok=True)
    return out


def _is_cloudflare_blocked(title: str, content: str) -> bool:
    """Detect Cloudflare block by title/content."""
    combined = ((title or "") + " " + (content or ""))[:8192].lower()
    if "cloudflare" not in combined:
        return False
    if "attention required" in combined or "blocked" in combined or "sorry, you have been blocked" in combined:
        return True
    return False


def _page_has_both_products(content: str) -> tuple[bool, list[str]]:
    """Check if page contains both target products."""
    content_lower = (content or "").lower()
    found = [t for t in TARGET_PRODUCTS if t.lower() in content_lower]
    return len(found) == len(TARGET_PRODUCTS), found


NOVNC_PORT = 6080


def _get_tailscale_ip() -> str:
    """Get Tailscale IPv4 via tailscale ip -4. Prefer for binding."""
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().split()[0]
    except Exception:
        pass
    return ""


def _get_tailscale_hostname() -> str:
    """Get Tailscale hostname (e.g. aiops-1.tailc75c62.ts.net) from tailscale status --json."""
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            data = json.loads(out.stdout)
            self_data = data.get("Self", {})
            dns_name = (self_data.get("DNSName") or "").rstrip(".")
            host_name = self_data.get("HostName") or ""
            if dns_name and ".ts.net" in dns_name:
                return dns_name
            if host_name:
                return host_name
    except Exception:
        pass
    return ""


def _get_tailscale_url() -> str:
    """Get Tailscale-only noVNC URL. Never emits 127.0.0.1."""
    hostname = _get_tailscale_hostname()
    ip = _get_tailscale_ip()
    port = NOVNC_PORT
    path = "/vnc.html?autoconnect=1"
    if hostname:
        return f"http://{hostname}:{port}{path}"
    if ip:
        return f"http://{ip}:{port}{path}"
    return f"http://<TAILSCALE_IP>:{port}{path} (run on aiops-1 with Tailscale)"


def _write_instructions(artifact_dir: Path, url: str) -> None:
    """Write instructions.txt with single clickable URL and reconnection guidance."""
    text = f"{url}\nIf you get 'Failed to connect', refresh the page; the viewer auto-restarts."
    (artifact_dir / "instructions.txt").write_text(text)


def _stop_novnc_systemd() -> None:
    """Stop openclaw-novnc service."""
    try:
        subprocess.run(
            ["systemctl", "stop", "openclaw-novnc"],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


def main() -> int:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    out_dir = _artifact_dir()
    run_id = out_dir.name
    screenshots_dir = out_dir / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)

    # Opt-in: interactive/noVNC tests disabled by default (ship runs on Mac without noVNC backend).
    if os.environ.get("OPENCLAW_RUN_INTERACTIVE_TESTS") != "1":
        skip_msg = "SKIP: interactive/noVNC tests disabled (set OPENCLAW_RUN_INTERACTIVE_TESTS=1 to run)"
        (out_dir / "instructions.txt").write_text(skip_msg)
        print(skip_msg, file=sys.stderr)
        return 0

    # Restart noVNC + poll probe. Fail-closed if unavailable.
    script_dir = Path(__file__).resolve().parent
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from novnc_ready import ensure_novnc_ready_with_recovery
    from src.playwright_safe import safe_content_excerpt, safe_screenshot, safe_title, safe_url

    ready, tailscale_url, err_class, journal_artifact = ensure_novnc_ready_with_recovery(out_dir, run_id)
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

    _write_instructions(out_dir, tailscale_url)
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
    from novnc_ready import novnc_display
    env["DISPLAY"] = novnc_display()

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
                # Bootstrap: admin → sites → click Soma → products (avoids 404)
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
                # Click Soma site (matches kajabi_admin_context._try_click_soma_site)
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
                while time.time() - start < TIMEOUT_SEC:
                    title = safe_title(page)
                    content = safe_content_excerpt(page, 8192)
                    safe_screenshot(page, str(screenshots_dir / f"poll_{int(time.time())}.png"))
                    cloudflare = _is_cloudflare_blocked(title, content)
                    has_both, found = _page_has_both_products(content)
                    if cloudflare:
                        time.sleep(10)
                        continue
                    if has_both:
                        STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                        context.storage_state(path=str(STORAGE_STATE_PATH))
                        try:
                            STORAGE_STATE_PATH.chmod(0o600)
                        except OSError:
                            pass
                        result_holder.append({
                            "ok": True,
                            "final_url": safe_url(page),
                            "title": title,
                            "products_found": found,
                            "cloudflare_detected": False,
                        })
                        browser.close()
                        done.set()
                        return
                    time.sleep(10)
                # Timeout
                safe_screenshot(page, str(out_dir / "screenshots" / "timeout_final.png"))
                result_holder.append({
                    "ok": False,
                    "error_class": KAJABI_INTERACTIVE_CAPTURE_TIMEOUT,
                    "final_url": safe_url(page),
                    "title": safe_title(page),
                    "products_found": [],
                    "cloudflare_detected": _is_cloudflare_blocked(safe_title(page), safe_content_excerpt(page, 8192)),
                })
                browser.close()
        except Exception as e:
            result_holder.append({
                "ok": False,
                "error_class": "KAJABI_INTERACTIVE_CAPTURE_ERROR",
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
            "error_class": KAJABI_INTERACTIVE_CAPTURE_TIMEOUT,
            "message": "Capture thread did not complete",
        })

    # Cleanup: stop systemd unit (AFTER storage_state export in run_playwright)
    _stop_novnc_systemd()

    summary = result_holder[0].copy()
    summary["artifact_dir"] = str(out_dir)
    summary["run_id"] = out_dir.name
    summary["tailscale_url"] = tailscale_url
    summary["profile_dir_used"] = str(KAJABI_CHROME_PROFILE_DIR)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    if summary.get("ok"):
        print(json.dumps({
            "ok": True,
            "artifact_dir": str(out_dir),
            "run_id": out_dir.name,
            "products_found": summary.get("products_found", []),
            "tailscale_url": tailscale_url,
        }))
        return 0
    print(json.dumps(summary))
    return 1


if __name__ == "__main__":
    sys.exit(main())
