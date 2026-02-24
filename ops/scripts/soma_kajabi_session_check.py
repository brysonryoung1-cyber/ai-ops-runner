#!/usr/bin/env python3
"""Soma Kajabi Session Check — validate session can reach admin/products and see both libraries.

Uses exit node wrapper when /etc/ai-ops-runner/config/soma_kajabi_exit_node.txt exists.
PASS only if Products page contains BOTH "Home User Library" and "Practitioner Library".
If Cloudflare/login encountered: output WAITING_FOR_HUMAN with noVNC URL and pause.

Artifacts: artifacts/soma_kajabi/session_check/<run_id>/{SUMMARY.md, summary.json, screenshot.png, page_title.txt}
No secrets.
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

KAJABI_ADMIN = "https://app.kajabi.com/admin"
KAJABI_SITES = "https://app.kajabi.com/admin/sites"
KAJABI_PRODUCTS_URL = "https://app.kajabi.com/admin/products"
TARGET_PRODUCTS = ["Home User Library", "Practitioner Library"]
KAJABI_CHROME_PROFILE_DIR = Path("/var/lib/openclaw/kajabi_chrome_profile")
EXIT_NODE_CONFIG = Path("/etc/ai-ops-runner/config/soma_kajabi_exit_node.txt")
TIMEOUT_SEC = 5 * 60  # 5 min for session check
NOVNC_PORT = 6080


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
    run_id = f"session_check_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    out = root / "artifacts" / "soma_kajabi" / "session_check" / run_id
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


def _get_tailscale_ip() -> str:
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
    hostname = _get_tailscale_hostname()
    ip = _get_tailscale_ip()
    port = NOVNC_PORT
    path = "/vnc.html?autoconnect=1"
    if hostname:
        return f"http://{hostname}:{port}{path}"
    if ip:
        return f"http://{ip}:{port}{path}"
    return f"http://<TAILSCALE_IP>:{port}{path}"


def _start_novnc_systemd(artifact_dir: Path, run_id: str) -> bool:
    env_dir = Path("/run/openclaw-novnc")
    try:
        env_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    env_file = env_dir / "next.env"
    env_file.write_text(
        f"OPENCLAW_NOVNC_RUN_ID={run_id}\n"
        f"OPENCLAW_NOVNC_ARTIFACT_DIR={artifact_dir}\n"
        f"OPENCLAW_NOVNC_PORT={NOVNC_PORT}\n"
        f"OPENCLAW_NOVNC_DISPLAY=:99\n"
    )
    try:
        subprocess.run(["systemctl", "stop", "openclaw-novnc"], capture_output=True, timeout=10)
        subprocess.run(["systemctl", "start", "openclaw-novnc"], check=True, capture_output=True, timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    for _ in range(30):
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(("127.0.0.1", NOVNC_PORT))
            s.close()
            return True
        except OSError:
            time.sleep(1)
    return False


def _stop_novnc_systemd() -> None:
    try:
        subprocess.run(["systemctl", "stop", "openclaw-novnc"], capture_output=True, timeout=15)
    except Exception:
        pass


def _ensure_xvfb_and_novnc(artifact_dir: Path) -> tuple[subprocess.Popen | None, subprocess.Popen | None, subprocess.Popen | None]:
    display = ":99"
    xvfb_proc = x11vnc_proc = novnc_proc = None
    try:
        xvfb_proc = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", "1280x720x24", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(2)
        if xvfb_proc.poll() is not None:
            return None, None, None
    except FileNotFoundError:
        return None, None, None

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
        return xvfb_proc, None, None

    bind_addr = _get_tailscale_ip() or "0.0.0.0"
    bind_spec = f"{bind_addr}:{NOVNC_PORT}"
    web_dir = "/usr/share/novnc" if (Path("/usr/share/novnc/vnc.html").exists()) else None
    base_cmd = ["websockify", bind_spec, "127.0.0.1:5900"]
    if web_dir:
        base_cmd = ["websockify", "--web", web_dir, bind_spec, "127.0.0.1:5900"]
    fallback_cmd = ["python3", "-m", "websockify", bind_spec, "127.0.0.1:5900"]
    if web_dir:
        fallback_cmd = ["python3", "-m", "websockify", "--web", web_dir, bind_spec, "127.0.0.1:5900"]
    for cmd in [base_cmd, fallback_cmd]:
        try:
            novnc_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            time.sleep(1)
            if novnc_proc.poll() is not None:
                novnc_proc = None
                continue
            break
        except FileNotFoundError:
            novnc_proc = None
            continue

    return xvfb_proc, x11vnc_proc, novnc_proc


def main() -> int:
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    out_dir = _artifact_dir()
    run_id = out_dir.name
    profile_dir = str(KAJABI_CHROME_PROFILE_DIR)

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
                context = p.chromium.launch_persistent_context(
                    profile_dir,
                    headless=False,
                    env=env,
                )
                page = context.new_page()
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
                while time.time() - start < TIMEOUT_SEC:
                    title = page.title() or ""
                    content = page.content()[:8192] if hasattr(page, "content") else ""
                    cloudflare = _is_cloudflare_blocked(title, content)
                    has_both, found = _page_has_both_products(content)
                    try:
                        page.screenshot(path=str(out_dir / "screenshot.png"))
                    except Exception:
                        pass
                    (out_dir / "page_title.txt").write_text(title)
                    if cloudflare:
                        tailscale_url = _get_tailscale_url()
                        (out_dir / "instructions.txt").write_text(
                            f"{tailscale_url}\nOpen in browser (Tailscale). Complete Cloudflare/login."
                        )
                        print("\n--- WAITING_FOR_HUMAN ---", file=sys.stderr)
                        print(tailscale_url, file=sys.stderr)
                        sys.stderr.flush()
                        time.sleep(15)
                        continue
                    if has_both:
                        result_holder.append({
                            "ok": True,
                            "final_url": page.url,
                            "title": title,
                            "products_found": found,
                        })
                        context.close()
                        done.set()
                        return
                    time.sleep(5)
                result_holder.append({
                    "ok": False,
                    "error_class": "SESSION_CHECK_TIMEOUT",
                    "final_url": page.url,
                    "title": title,
                })
                context.close()
        except Exception as e:
            result_holder.append({
                "ok": False,
                "error_class": "SESSION_CHECK_ERROR",
                "message": str(e)[:500],
            })
        finally:
            done.set()

    # Start noVNC first (needed if Cloudflare triggers — human completes via noVNC)
    use_systemd_novnc = False
    xvfb_proc = x11vnc_proc = novnc_proc = None
    if _start_novnc_systemd(out_dir, run_id):
        use_systemd_novnc = True
    else:
        xvfb_proc, x11vnc_proc, novnc_proc = _ensure_xvfb_and_novnc(out_dir)

    t = threading.Thread(target=run_playwright)
    t.start()
    done.wait(timeout=TIMEOUT_SEC + 30)

    if not result_holder:
        result_holder.append({
            "ok": False,
            "error_class": "SESSION_CHECK_TIMEOUT",
            "message": "Check thread did not complete",
        })

    # Cleanup noVNC
    if use_systemd_novnc:
        _stop_novnc_systemd()
    else:
        for proc in [novnc_proc, x11vnc_proc, xvfb_proc]:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    pass

    res = result_holder[0]
    summary = res.copy()
    summary["artifact_dir"] = str(out_dir)
    summary["run_id"] = run_id
    summary["profile_dir_used"] = profile_dir
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    if summary.get("ok"):
        summary_md = f"""# Session Check — PASS

**Run ID**: {run_id}
**Products found**: {summary.get("products_found", [])}
"""
        (out_dir / "SUMMARY.md").write_text(summary_md)
        print(json.dumps({
            "ok": True,
            "artifact_dir": str(out_dir),
            "run_id": run_id,
            "products_found": summary.get("products_found", []),
        }))
        return 0

    err = summary.get("error_class", "SESSION_CHECK_FAILED")
    msg = summary.get("message", "Session check failed")
    (out_dir / "SUMMARY.md").write_text(f"# Session Check — FAIL\n\n**{err}**: {msg}\n")
    print(json.dumps(summary))
    return 1


if __name__ == "__main__":
    sys.exit(main())
