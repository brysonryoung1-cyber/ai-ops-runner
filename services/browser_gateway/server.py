#!/usr/bin/env python3
"""Browser Gateway Server — CDP-based browser streaming for human gate.

Replaces noVNC as primary human gate transport. Streams the Chromium tab
via CDP Page.startScreencast, accepts mouse/keyboard input forwarding.

Architecture:
  aiohttp server on 127.0.0.1:8890 (localhost only)
  Caddy frontdoor routes /browser-gateway/* here
  Tailscale-only access; per-session tokens; no secrets logged.

Endpoints:
  POST /session/start     Create session (returns session_id + token + viewer_url)
  GET  /session/status    Get session status
  POST /session/input     Send mouse/keyboard input
  GET  /stream            WebSocket: binary JPEG frames + JSON input upstream
  GET  /health            Health check

Artifacts:
  artifacts/browser_gateway/<session_id>/{session.json, logs.txt, PROOF.md, last_frame.jpg}
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from aiohttp import web, ClientSession, WSMsgType
except ImportError:
    print("aiohttp required: pip install aiohttp", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("browser-gateway")

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.environ.get("BROWSER_GATEWAY_PORT", "8890"))
CDP_PORT = int(os.environ.get("BROWSER_GATEWAY_CDP_PORT", "9222"))
CHROME_PROFILE = os.environ.get(
    "BROWSER_GATEWAY_CHROME_PROFILE",
    "/var/lib/openclaw/kajabi_chrome_profile",
)
SESSION_TTL_SEC = int(os.environ.get("BROWSER_GATEWAY_SESSION_TTL", "3600"))
SCREENCAST_QUALITY = int(os.environ.get("BROWSER_GATEWAY_QUALITY", "60"))
SCREENCAST_MAX_WIDTH = 1280
SCREENCAST_MAX_HEIGHT = 720
MAX_RECONNECT_ATTEMPTS = 3
HEARTBEAT_INTERVAL = 10
VERSION = "1.0.0"
_SERVER_START_TIME: float = 0.0

TS_HOSTNAME = os.environ.get("OPENCLAW_TS_HOSTNAME", "aiops-1.tailc75c62.ts.net")


def _repo_root() -> Path:
    env = os.environ.get("OPENCLAW_REPO_ROOT")
    if env and Path(env).exists():
        return Path(env)
    return Path("/opt/ai-ops-runner")


def _artifacts_root() -> Path:
    env = os.environ.get("OPENCLAW_ARTIFACTS_ROOT")
    if env:
        return Path(env)
    return _repo_root() / "artifacts"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BrowserGatewaySession:
    """Manages a single CDP streaming session."""

    def __init__(self, session_id: str, run_id: str, purpose: str, timeout_sec: int):
        self.session_id = session_id
        self.run_id = run_id
        self.purpose = purpose
        self.token = secrets.token_urlsafe(32)
        self.status = "CONNECTING"
        self.created_at = _now_iso()
        self.timeout_sec = timeout_sec
        self.last_frame_ts: Optional[float] = None
        self.last_input_ts: Optional[float] = None
        self.viewers: set[web.WebSocketResponse] = set()
        self.cdp_ws: Optional[object] = None
        self.frame_buffer: Optional[bytes] = None
        self.cdp_msg_id = 10
        self._cdp_task: Optional[asyncio.Task] = None
        self._chromium_proc: Optional[subprocess.Popen] = None
        self._artifact_dir = _artifacts_root() / "browser_gateway" / session_id
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        self._log_lines: list[str] = []

    def _log(self, msg: str) -> None:
        line = f"[{_now_iso()}] {msg}"
        self._log_lines.append(line)
        log.info(f"[{self.session_id[:8]}] {msg}")

    def _write_session_json(self) -> None:
        data = {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "purpose": self.purpose,
            "status": self.status,
            "created_at": self.created_at,
            "cdp_port": CDP_PORT,
            "last_frame_ts": self.last_frame_ts,
            "last_input_ts": self.last_input_ts,
            "viewer_count": len(self.viewers),
        }
        (self._artifact_dir / "session.json").write_text(json.dumps(data, indent=2))

    def _write_logs(self) -> None:
        (self._artifact_dir / "logs.txt").write_text("\n".join(self._log_lines))

    def _write_proof(self) -> None:
        has_frame = self.last_frame_ts is not None
        proof = (
            f"# Browser Gateway Proof\n\n"
            f"**Session ID:** {self.session_id}\n"
            f"**Run ID:** {self.run_id}\n"
            f"**Created:** {self.created_at}\n"
            f"**Status:** {self.status}\n"
            f"**First frame received:** {'YES' if has_frame else 'NO'}\n"
            f"**Last frame timestamp:** {self.last_frame_ts}\n"
            f"**Input received:** {'YES' if self.last_input_ts else 'NO'}\n"
        )
        (self._artifact_dir / "PROOF.md").write_text(proof)

    async def launch_chromium(self) -> bool:
        """Launch Chromium with remote debugging enabled, using the persistent profile."""
        self._log("Launching Chromium with CDP enabled")
        profile = Path(CHROME_PROFILE)
        profile.mkdir(parents=True, exist_ok=True)

        display = os.environ.get("DISPLAY", ":99")
        env = {**os.environ, "DISPLAY": display}

        chromium_paths = [
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/usr/bin/google-chrome",
            "/snap/bin/chromium",
        ]
        chromium_bin = None
        for p in chromium_paths:
            if Path(p).exists():
                chromium_bin = p
                break

        if not chromium_bin:
            try:
                result = subprocess.run(
                    ["which", "chromium-browser"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    chromium_bin = result.stdout.strip()
            except Exception:
                pass

        if not chromium_bin:
            self._log("ERROR: Chromium binary not found")
            self.status = "ERROR"
            return False

        cmd = [
            chromium_bin,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            f"--window-size={SCREENCAST_MAX_WIDTH},{SCREENCAST_MAX_HEIGHT}",
        ]

        try:
            self._chromium_proc = subprocess.Popen(
                cmd, env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            await asyncio.sleep(3)
            if self._chromium_proc.poll() is not None:
                self._log(f"Chromium exited immediately with code {self._chromium_proc.returncode}")
                self.status = "ERROR"
                return False
            self._log(f"Chromium launched (PID {self._chromium_proc.pid})")
            return True
        except Exception as e:
            self._log(f"Failed to launch Chromium: {e}")
            self.status = "ERROR"
            return False

    async def attach_to_existing(self) -> bool:
        """Try to attach to an already-running Chromium with CDP."""
        self._log(f"Attempting to attach to existing Chromium on port {CDP_PORT}")
        try:
            async with ClientSession() as http:
                async with http.get(
                    f"http://127.0.0.1:{CDP_PORT}/json/version",
                    timeout=__import__("aiohttp").ClientTimeout(total=3),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._log(f"Attached to Chromium: {data.get('Browser', 'unknown')}")
                        return True
        except Exception:
            pass
        self._log("No existing Chromium found on CDP port")
        return False

    async def connect_cdp(self) -> bool:
        """Connect to Chrome DevTools Protocol and start screencast."""
        import aiohttp as _aiohttp

        for attempt in range(MAX_RECONNECT_ATTEMPTS):
            try:
                async with ClientSession() as http:
                    async with http.get(
                        f"http://127.0.0.1:{CDP_PORT}/json",
                        timeout=_aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        pages = await resp.json()

                page_ws_url = None
                for page in pages:
                    if page.get("type") == "page":
                        page_ws_url = page.get("webSocketDebuggerUrl")
                        break

                if not page_ws_url:
                    self._log(f"No page found (attempt {attempt + 1})")
                    await asyncio.sleep(2)
                    continue

                session = ClientSession()
                self.cdp_ws = await session.ws_connect(page_ws_url)
                self._cdp_session = session

                self.cdp_msg_id = 10
                await self.cdp_ws.send_json({
                    "id": self.cdp_msg_id,
                    "method": "Page.startScreencast",
                    "params": {
                        "format": "jpeg",
                        "quality": SCREENCAST_QUALITY,
                        "maxWidth": SCREENCAST_MAX_WIDTH,
                        "maxHeight": SCREENCAST_MAX_HEIGHT,
                        "everyNthFrame": 2,
                    },
                })
                self.cdp_msg_id += 1

                self.status = "LIVE"
                self._log("CDP connected, screencast started")
                self._write_session_json()
                return True

            except Exception as e:
                self._log(f"CDP connect attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(2)

        self.status = "ERROR"
        self._log("Failed to connect to CDP after all attempts")
        return False

    async def process_cdp_loop(self) -> None:
        """Main loop: read CDP messages, relay frames to viewers."""
        if not self.cdp_ws:
            return

        try:
            async for msg in self.cdp_ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    method = data.get("method", "")

                    if method == "Page.screencastFrame":
                        params = data["params"]
                        frame_data = base64.b64decode(params["data"])
                        self.frame_buffer = frame_data
                        self.last_frame_ts = time.time()

                        await self.cdp_ws.send_json({
                            "id": self.cdp_msg_id,
                            "method": "Page.screencastFrameAck",
                            "params": {"sessionId": params["sessionId"]},
                        })
                        self.cdp_msg_id += 1

                        if self.frame_buffer:
                            (self._artifact_dir / "last_frame.jpg").write_bytes(
                                self.frame_buffer
                            )

                        dead = set()
                        for ws in self.viewers:
                            try:
                                await ws.send_bytes(frame_data)
                            except Exception:
                                dead.add(ws)
                        self.viewers -= dead

                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    self._log("CDP WebSocket closed/error")
                    break

        except Exception as e:
            self._log(f"CDP loop error: {e}")
        finally:
            self.status = "DISCONNECTED"
            self._write_session_json()

    async def dispatch_input(self, event: dict) -> bool:
        """Forward mouse/keyboard input to Chrome via CDP."""
        if not self.cdp_ws or self.status != "LIVE":
            return False

        evt_type = event.get("type", "")
        try:
            if evt_type in ("mousePressed", "mouseReleased", "mouseMoved"):
                await self.cdp_ws.send_json({
                    "id": self.cdp_msg_id,
                    "method": "Input.dispatchMouseEvent",
                    "params": {
                        "type": evt_type,
                        "x": int(event.get("x", 0)),
                        "y": int(event.get("y", 0)),
                        "button": event.get("button", "left"),
                        "clickCount": int(event.get("clickCount", 1)),
                    },
                })
            elif evt_type in ("keyDown", "keyUp"):
                params: dict = {
                    "type": evt_type,
                }
                if event.get("text"):
                    params["text"] = event["text"]
                if event.get("key"):
                    params["key"] = event["key"]
                if event.get("code"):
                    params["code"] = event["code"]
                if event.get("windowsVirtualKeyCode"):
                    params["windowsVirtualKeyCode"] = int(event["windowsVirtualKeyCode"])
                if event.get("nativeVirtualKeyCode"):
                    params["nativeVirtualKeyCode"] = int(event["nativeVirtualKeyCode"])
                await self.cdp_ws.send_json({
                    "id": self.cdp_msg_id,
                    "method": "Input.dispatchKeyEvent",
                    "params": params,
                })
            elif evt_type == "char":
                await self.cdp_ws.send_json({
                    "id": self.cdp_msg_id,
                    "method": "Input.dispatchKeyEvent",
                    "params": {
                        "type": "char",
                        "text": event.get("text", ""),
                    },
                })
            else:
                return False

            self.cdp_msg_id += 1
            self.last_input_ts = time.time()
            return True

        except Exception as e:
            self._log(f"Input dispatch error: {e}")
            return False

    async def close(self) -> None:
        """Clean up session resources."""
        self._log("Closing session")
        self.status = "EXPIRED"

        if self.cdp_ws:
            try:
                await self.cdp_ws.send_json({
                    "id": self.cdp_msg_id,
                    "method": "Page.stopScreencast",
                    "params": {},
                })
            except Exception:
                pass
            try:
                await self.cdp_ws.close()
            except Exception:
                pass

        if hasattr(self, "_cdp_session"):
            try:
                await self._cdp_session.close()
            except Exception:
                pass

        for ws in list(self.viewers):
            try:
                await ws.close()
            except Exception:
                pass
        self.viewers.clear()

        self._write_session_json()
        self._write_logs()
        self._write_proof()

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "purpose": self.purpose,
            "status": self.status,
            "created_at": self.created_at,
            "last_frame_ts": self.last_frame_ts,
            "last_input_ts": self.last_input_ts,
            "viewer_count": len(self.viewers),
            "has_frame": self.frame_buffer is not None,
        }


class BrowserGatewayServer:
    """Main server managing sessions and HTTP/WS endpoints."""

    def __init__(self):
        self.sessions: dict[str, BrowserGatewaySession] = {}
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_post("/session/start", self.handle_start)
        self.app.router.add_get("/session/status", self.handle_status)
        self.app.router.add_post("/session/input", self.handle_input)
        self.app.router.add_get("/stream", self.handle_stream)
        self.app.router.add_get("/health", self.handle_health)

    def _get_session_by_token(self, req: web.Request) -> Optional[BrowserGatewaySession]:
        token = req.query.get("token") or req.headers.get("X-Gateway-Token")
        if not token:
            return None
        for s in self.sessions.values():
            if s.token == token:
                return s
        return None

    async def handle_health(self, req: web.Request) -> web.Response:
        active = [s.to_dict() for s in self.sessions.values() if s.status == "LIVE"]
        return web.json_response({
            "ok": True,
            "version": VERSION,
            "uptime_sec": round(time.time() - _SERVER_START_TIME, 1),
            "active_sessions": len(active),
            "sessions": active,
            "server_time": _now_iso(),
        })

    async def handle_start(self, req: web.Request) -> web.Response:
        try:
            body = await req.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        run_id = body.get("run_id", "unknown")
        purpose = body.get("purpose", "kajabi_login")
        timeout_sec = min(int(body.get("timeout_sec", SESSION_TTL_SEC)), SESSION_TTL_SEC)

        for sid, existing in list(self.sessions.items()):
            if existing.status in ("EXPIRED", "ERROR", "DISCONNECTED"):
                await existing.close()
                del self.sessions[sid]
            elif existing.run_id == run_id and existing.status == "LIVE":
                viewer_url = f"https://{TS_HOSTNAME}/browser/{existing.session_id}?token={existing.token}"
                return web.json_response({
                    "ok": True,
                    "session_id": existing.session_id,
                    "token": existing.token,
                    "viewer_url": viewer_url,
                    "status": existing.status,
                    "reused": True,
                })

        session_id = f"bg_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{secrets.token_hex(4)}"
        session = BrowserGatewaySession(session_id, run_id, purpose, timeout_sec)
        self.sessions[session_id] = session

        attached = await session.attach_to_existing()
        if not attached:
            launched = await session.launch_chromium()
            if not launched:
                del self.sessions[session_id]
                return web.json_response({
                    "ok": False,
                    "error": "Failed to launch or attach to Chromium",
                    "error_class": "BROWSER_GATEWAY_LAUNCH_FAILED",
                }, status=500)
            await asyncio.sleep(2)

        connected = await session.connect_cdp()
        if not connected:
            del self.sessions[session_id]
            return web.json_response({
                "ok": False,
                "error": "Failed to connect to Chrome DevTools",
                "error_class": "BROWSER_GATEWAY_CDP_FAILED",
            }, status=500)

        session._cdp_task = asyncio.create_task(session.process_cdp_loop())

        asyncio.get_event_loop().call_later(timeout_sec, lambda: asyncio.create_task(self._expire_session(session_id)))

        viewer_url = f"https://{TS_HOSTNAME}/browser/{session_id}?token={session.token}"
        session._log(f"Session started. Viewer URL: {viewer_url}")
        session._write_session_json()

        return web.json_response({
            "ok": True,
            "session_id": session_id,
            "token": session.token,
            "viewer_url": viewer_url,
            "status": session.status,
        })

    async def handle_status(self, req: web.Request) -> web.Response:
        session_id = req.query.get("session_id")
        if not session_id or session_id not in self.sessions:
            return web.json_response({"ok": False, "error": "Session not found"}, status=404)
        return web.json_response({"ok": True, **self.sessions[session_id].to_dict()})

    async def handle_input(self, req: web.Request) -> web.Response:
        session = self._get_session_by_token(req)
        if not session:
            return web.json_response({"ok": False, "error": "Invalid token"}, status=401)

        try:
            event = await req.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400)

        ok = await session.dispatch_input(event)
        return web.json_response({"ok": ok})

    async def handle_stream(self, req: web.Request) -> web.WebSocketResponse:
        session = self._get_session_by_token(req)
        if not session:
            ws = web.WebSocketResponse()
            await ws.prepare(req)
            await ws.send_json({"error": "Invalid token"})
            await ws.close()
            return ws

        ws = web.WebSocketResponse(heartbeat=HEARTBEAT_INTERVAL)
        await ws.prepare(req)
        session.viewers.add(ws)
        session._log(f"Viewer connected (total: {len(session.viewers)})")

        if session.frame_buffer:
            try:
                await ws.send_bytes(session.frame_buffer)
            except Exception:
                pass

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        event = json.loads(msg.data)
                        await session.dispatch_input(event)
                    except Exception:
                        pass
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            session.viewers.discard(ws)
            session._log(f"Viewer disconnected (remaining: {len(session.viewers)})")

        return ws

    async def _expire_session(self, session_id: str) -> None:
        if session_id in self.sessions:
            session = self.sessions[session_id]
            if session.status == "LIVE":
                session._log("Session expired (TTL)")
                await session.close()


async def main():
    global _SERVER_START_TIME
    _SERVER_START_TIME = time.time()
    server = BrowserGatewayServer()
    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, LISTEN_HOST, LISTEN_PORT)
    await site.start()
    log.info(f"Browser Gateway listening on {LISTEN_HOST}:{LISTEN_PORT}")

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    for session in server.sessions.values():
        await session.close()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
