#!/usr/bin/env python3
"""Kajabi interactive capture — headed browser over Tailscale-only noVNC.

When Playwright hits Cloudflare block, this action starts a headed Chromium session
in Xvfb, exposes it via noVNC (bound to 127.0.0.1/Tailscale only), and waits for
a human to complete the Cloudflare check/login. Then exports storage_state and
reruns discover.

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

KAJABI_PRODUCTS_URL = "https://app.kajabi.com/admin/products"
STORAGE_STATE_PATH = Path("/etc/ai-ops-runner/secrets/soma_kajabi/kajabi_storage_state.json")
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
        return Path(env)
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


def _get_tailscale_url() -> str:
    """Get Tailscale-only URL for noVNC. Binds to 127.0.0.1; user accesses via tailscale serve."""
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0:
            data = json.loads(out.stdout)
            hostname = data.get("Self", {}).get("HostName") or data.get("Self", {}).get("DNSName")
            if hostname:
                return f"https://{hostname}/vnc (Tailscale only — ensure tailscale serve exposes /vnc)"
    except Exception:
        pass
    ip = ""
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            ip = out.stdout.strip().split()[0]
    except Exception:
        pass
    if ip:
        return f"http://{ip}:6080/vnc.html (Tailscale only)"
    return "http://127.0.0.1:6080/vnc.html (port-forward or tailscale serve)"


def _ensure_xvfb_and_novnc(artifact_dir: Path) -> tuple[subprocess.Popen | None, subprocess.Popen | None, subprocess.Popen | None]:
    """Start Xvfb, x11vnc, and noVNC/websockify. Return (xvfb_proc, x11vnc_proc, novnc_proc)."""
    display = ":99"
    xvfb_proc = None
    x11vnc_proc = None
    novnc_proc = None

    # Start Xvfb
    try:
        xvfb_proc = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", "1280x720x24", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(2)
        if xvfb_proc.poll() is not None:
            err = b""
            if xvfb_proc.stderr:
                try:
                    err = xvfb_proc.stderr.read()
                except Exception:
                    pass
            (artifact_dir / "xvfb_stderr.txt").write_text(err.decode("utf-8", errors="replace"))
            return None, None, None
    except FileNotFoundError:
        (artifact_dir / "instructions.txt").write_text(
            "Xvfb not installed. Run: apt install xvfb (or sudo apt install xvfb)"
        )
        return None, None, None

    # Start x11vnc (VNC server on display)
    try:
        x11vnc_proc = subprocess.Popen(
            ["x11vnc", "-display", display, "-rfbport", "5900", "-localhost", "-nopw", "-forever"],
            env={**os.environ, "DISPLAY": display},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(1)
        if x11vnc_proc.poll() is not None:
            return xvfb_proc, None, None
    except FileNotFoundError:
        (artifact_dir / "instructions.txt").write_text(
            "x11vnc not installed. Run: apt install x11vnc (or sudo apt install x11vnc)"
        )
        return xvfb_proc, None, None

    # Start websockify (VNC -> WebSocket) — try noVNC's websockify or python -m websockify
    for cmd in [
        ["websockify", "127.0.0.1:6080", "127.0.0.1:5900"],
        ["python3", "-m", "websockify", "127.0.0.1:6080", "127.0.0.1:5900"],
    ]:
        try:
            novnc_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            time.sleep(1)
            if novnc_proc.poll() is not None:
                novnc_proc = None
                continue
            break
        except FileNotFoundError:
            novnc_proc = None
            continue

    if novnc_proc is None:
        (artifact_dir / "instructions.txt").write_text(
            "websockify not installed. Run: pip install websockify (or apt install novnc)"
        )
        return xvfb_proc, x11vnc_proc, None

    return xvfb_proc, x11vnc_proc, novnc_proc


def main() -> int:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    out_dir = _artifact_dir()
    screenshots_dir = out_dir / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)

    # Start Xvfb + noVNC stack
    xvfb_proc, x11vnc_proc, novnc_proc = _ensure_xvfb_and_novnc(out_dir)
    if novnc_proc is None:
        url = _get_tailscale_url()
        instructions = f"Open {url} and complete Cloudflare/login, then return here. (noVNC stack failed to start; see instructions.txt)"
        (out_dir / "instructions.txt").write_text(instructions)
        summary = {
            "ok": False,
            "error_class": "KAJABI_INTERACTIVE_CAPTURE_NO_VNC",
            "message": "Xvfb/x11vnc/websockify not available. Install: apt install xvfb x11vnc; pip install websockify",
            "artifact_dir": str(out_dir),
        }
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary))
        return 1

    tailscale_url = _get_tailscale_url()
    instructions = f"Open {tailscale_url} and complete Cloudflare/login, then return here"
    (out_dir / "instructions.txt").write_text(instructions)
    print(instructions, file=sys.stderr)

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
                browser = p.chromium.launch(headless=False, env=env)
                context = browser.new_context()
                page = context.new_page()
                page.goto(KAJABI_PRODUCTS_URL, wait_until="domcontentloaded", timeout=60000)
                start = time.time()
                while time.time() - start < TIMEOUT_SEC:
                    title = page.title() or ""
                    content = page.content()[:8192] if hasattr(page, "content") else ""
                    cloudflare = _is_cloudflare_blocked(title, content)
                    has_both, found = _page_has_both_products(content)
                    try:
                        page.screenshot(path=str(screenshots_dir / f"poll_{int(time.time())}.png"))
                    except Exception:
                        pass
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
                            "final_url": page.url,
                            "title": title,
                            "products_found": found,
                            "cloudflare_detected": False,
                        })
                        browser.close()
                        done.set()
                        return
                    time.sleep(10)
                # Timeout
                try:
                    page.screenshot(path=str(out_dir / "screenshots" / "timeout_final.png"))
                except Exception:
                    pass
                result_holder.append({
                    "ok": False,
                    "error_class": KAJABI_INTERACTIVE_CAPTURE_TIMEOUT,
                    "final_url": page.url,
                    "title": page.title() or "",
                    "products_found": [],
                    "cloudflare_detected": _is_cloudflare_blocked(page.title() or "", page.content()[:8192] if hasattr(page, "content") else ""),
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

    # Cleanup
    for proc in [novnc_proc, x11vnc_proc, xvfb_proc]:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    summary = result_holder[0].copy()
    summary["artifact_dir"] = str(out_dir)
    summary["run_id"] = out_dir.name
    summary["tailscale_url"] = tailscale_url
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
