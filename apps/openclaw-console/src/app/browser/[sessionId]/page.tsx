"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useParams, useSearchParams } from "next/navigation";

type ConnectionState = "CONNECTING" | "LIVE" | "RECONNECTING" | "EXPIRED" | "ERROR";
type InputVerification = "IDLE" | "VERIFYING" | "VERIFIED" | "FAILED";

interface GatewayStatus {
  status?: string;
  last_input_ts?: number | null;
  last_input_error?: string | null;
  last_cdp_dispatch_error?: string | null;
}

export default function BrowserViewerPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const sessionId = params.sessionId as string;
  const token = searchParams.get("token") || "";

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [state, setState] = useState<ConnectionState>("CONNECTING");
  const [fps, setFps] = useState(0);
  const [lastError, setLastError] = useState<string | null>(null);
  const frameCountRef = useRef(0);
  const reconnectAttemptRef = useRef(0);
  const maxReconnects = 5;

  const [inputVerification, setInputVerification] = useState<InputVerification>("IDLE");
  const [inputFailReason, setInputFailReason] = useState<string | null>(null);
  const [hudStatus, setHudStatus] = useState<GatewayStatus>({});
  const controlsEnabled = state === "LIVE" && inputVerification === "VERIFIED";

  const fetchStatus = useCallback(async (): Promise<GatewayStatus> => {
    try {
      const resp = await fetch(
        `/api/browser-gateway/status?session_id=${encodeURIComponent(sessionId)}`,
        { signal: AbortSignal.timeout(3000) },
      );
      if (!resp.ok) return {};
      return await resp.json();
    } catch {
      return {};
    }
  }, [sessionId]);

  const runInputSelftest = useCallback(async () => {
    setInputVerification("VERIFYING");
    setInputFailReason(null);

    const baselineStatus = await fetchStatus();
    const baselineTs = baselineStatus.last_input_ts ?? 0;

    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "mouseMoved", x: 1, y: 1 }));
    } else {
      setInputVerification("FAILED");
      setInputFailReason("WebSocket not open for selftest");
      return;
    }

    const deadline = Date.now() + 2000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 250));
      const status = await fetchStatus();
      if (status.last_input_ts && status.last_input_ts > baselineTs) {
        setInputVerification("VERIFIED");
        return;
      }
    }

    const finalStatus = await fetchStatus();
    const reason =
      finalStatus.last_input_error ||
      finalStatus.last_cdp_dispatch_error ||
      "last_input_ts did not advance within 2s";
    setInputVerification("FAILED");
    setInputFailReason(reason);
  }, [fetchStatus]);

  const connectWs = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/browser-gateway/stream?token=${encodeURIComponent(token)}`;

    setState("CONNECTING");
    setInputVerification("IDLE");
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    let receivedFrame = false;
    let gotError = false;
    let isReconnect = reconnectAttemptRef.current > 0;

    const connectTimeout = setTimeout(() => {
      if (!receivedFrame && ws.readyState !== WebSocket.CLOSED) {
        setLastError("Connection timed out — gateway may be unreachable or session expired");
        setState("ERROR");
        ws.close();
      }
    }, 15000);

    ws.onopen = () => {
      reconnectAttemptRef.current = 0;
      setLastError(null);
    };

    ws.onmessage = (evt) => {
      if (evt.data instanceof ArrayBuffer) {
        if (!receivedFrame) {
          receivedFrame = true;
          clearTimeout(connectTimeout);
          setState("LIVE");
          if (isReconnect || inputVerification !== "VERIFIED") {
            runInputSelftest();
          } else {
            setInputVerification("VERIFIED");
          }
        }
        const blob = new Blob([evt.data], { type: "image/jpeg" });
        const url = URL.createObjectURL(blob);
        const img = new Image();
        img.onload = () => {
          const canvas = canvasRef.current;
          if (canvas) {
            canvas.width = img.width;
            canvas.height = img.height;
            const ctx = canvas.getContext("2d");
            if (ctx) {
              ctx.drawImage(img, 0, 0);
            }
          }
          URL.revokeObjectURL(url);
          frameCountRef.current++;
        };
        img.src = url;
      } else if (typeof evt.data === "string") {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.error) {
            gotError = true;
            clearTimeout(connectTimeout);
            setLastError(msg.error);
            setState("ERROR");
          }
        } catch { /* ignore non-JSON text */ }
      }
    };

    ws.onclose = () => {
      clearTimeout(connectTimeout);
      if (gotError) return;
      if (reconnectAttemptRef.current < maxReconnects) {
        setState("RECONNECTING");
        setInputVerification("IDLE");
        reconnectAttemptRef.current++;
        const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 10000);
        setTimeout(connectWs, delay);
      } else {
        setState("EXPIRED");
      }
    };

    ws.onerror = () => {
      clearTimeout(connectTimeout);
      setLastError("WebSocket connection error — check that Browser Gateway is running");
    };
  }, [token, runInputSelftest, inputVerification]);

  useEffect(() => {
    connectWs();
    return () => {
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const interval = setInterval(() => {
      setFps(frameCountRef.current);
      frameCountRef.current = 0;
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (state !== "LIVE" && state !== "RECONNECTING") return;
    let cancelled = false;
    const poll = async () => {
      while (!cancelled) {
        const s = await fetchStatus();
        if (cancelled) break;
        setHudStatus(s);
        await new Promise((r) => setTimeout(r, 2000));
      }
    };
    poll();
    return () => { cancelled = true; };
  }, [state, fetchStatus]);

  const sendInput = useCallback((event: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(event));
    }
  }, []);

  const getCanvasCoords = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;
    return {
      x: Math.round((e.clientX - rect.left) * scaleX),
      y: Math.round((e.clientY - rect.top) * scaleY),
    };
  }, []);

  const handleMouseDown = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!controlsEnabled) return;
    e.preventDefault();
    const coords = getCanvasCoords(e);
    sendInput({ type: "mousePressed", ...coords, button: "left", clickCount: 1 });
  }, [getCanvasCoords, sendInput, controlsEnabled]);

  const handleMouseUp = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!controlsEnabled) return;
    e.preventDefault();
    const coords = getCanvasCoords(e);
    sendInput({ type: "mouseReleased", ...coords, button: "left", clickCount: 1 });
  }, [getCanvasCoords, sendInput, controlsEnabled]);

  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!controlsEnabled) return;
    if (e.buttons === 0) return;
    const coords = getCanvasCoords(e);
    sendInput({ type: "mouseMoved", ...coords });
  }, [getCanvasCoords, sendInput, controlsEnabled]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!controlsEnabled) return;
      e.preventDefault();
      sendInput({
        type: "keyDown",
        key: e.key,
        code: e.code,
        text: e.key.length === 1 ? e.key : undefined,
        windowsVirtualKeyCode: e.keyCode,
      });
      if (e.key.length === 1) {
        sendInput({ type: "char", text: e.key });
      }
    };

    const handleKeyUp = (e: KeyboardEvent) => {
      if (!controlsEnabled) return;
      e.preventDefault();
      sendInput({
        type: "keyUp",
        key: e.key,
        code: e.code,
        windowsVirtualKeyCode: e.keyCode,
      });
    };

    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("keyup", handleKeyUp);
    };
  }, [controlsEnabled, sendInput]);

  const handleRestart = async () => {
    setState("CONNECTING");
    try {
      const resp = await fetch("/api/browser-gateway/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: sessionId, purpose: "kajabi_login" }),
      });
      const data = await resp.json();
      if (data.ok && data.viewer_url) {
        window.location.href = data.viewer_url;
      } else {
        setLastError(data.error || "Failed to restart session");
        setState("ERROR");
      }
    } catch {
      setLastError("Failed to restart session");
      setState("ERROR");
    }
  };

  const stateColors: Record<ConnectionState, string> = {
    CONNECTING: "bg-amber-500",
    LIVE: "bg-green-500",
    RECONNECTING: "bg-amber-500",
    EXPIRED: "bg-red-500",
    ERROR: "bg-red-500",
  };

  const wsReadyState = wsRef.current?.readyState ?? -1;
  const wsStateLabel = ["CONNECTING", "OPEN", "CLOSING", "CLOSED"][wsReadyState] ?? "NONE";

  const truncate = (s: string | null | undefined, n: number) =>
    s && s.length > n ? s.slice(0, n) + "…" : s ?? "—";

  return (
    <div className="min-h-screen bg-black flex flex-col">
      <header className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-800">
        <div className="flex items-center gap-3">
          <div className={`w-2.5 h-2.5 rounded-full ${stateColors[state]} ${state === "CONNECTING" || state === "RECONNECTING" ? "animate-pulse" : ""}`} />
          <span className="text-sm font-medium text-white/90">Browser Gateway</span>
          <span className="text-xs text-white/50 font-mono">{sessionId.slice(0, 16)}</span>
        </div>
        <div className="flex items-center gap-4">
          {state === "LIVE" && (
            <span className="text-xs text-white/50">{fps} fps</span>
          )}
          <a
            href="/inbox"
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            Back to Inbox
          </a>
        </div>
      </header>

      {(state === "LIVE" || state === "RECONNECTING") && (
        <div
          data-testid="gateway-hud"
          className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5 bg-gray-950 border-b border-gray-800 text-[11px] font-mono text-white/60"
        >
          <span>
            status:{" "}
            <span className={hudStatus.status === "LIVE" ? "text-green-400" : "text-amber-400"}>
              {hudStatus.status ?? state}
            </span>
          </span>
          <span>last_input_ts: {hudStatus.last_input_ts ? new Date(hudStatus.last_input_ts * 1000).toLocaleTimeString() : "—"}</span>
          <span>input_err: {truncate(hudStatus.last_input_error, 60)}</span>
          <span>cdp_err: {truncate(hudStatus.last_cdp_dispatch_error, 60)}</span>
          <span>ws: {wsStateLabel}</span>
          <span>
            input:{" "}
            <span className={
              inputVerification === "VERIFIED" ? "text-green-400" :
              inputVerification === "FAILED" ? "text-red-400" :
              inputVerification === "VERIFYING" ? "text-amber-400" :
              "text-white/40"
            }>
              {inputVerification}
            </span>
          </span>
        </div>
      )}

      <main className="flex-1 flex items-center justify-center p-2 relative">
        {state === "LIVE" || state === "RECONNECTING" ? (
          <canvas
            ref={canvasRef}
            className={`max-w-full max-h-[calc(100vh-80px)] ${controlsEnabled ? "cursor-crosshair" : "cursor-not-allowed opacity-80"}`}
            style={{ imageRendering: "auto", pointerEvents: controlsEnabled ? "auto" : "none" }}
            onMouseDown={handleMouseDown}
            onMouseUp={handleMouseUp}
            onMouseMove={handleMouseMove}
            onContextMenu={(e) => e.preventDefault()}
            tabIndex={0}
          />
        ) : null}

        {inputVerification === "VERIFYING" && (
          <div
            data-testid="input-verifying-overlay"
            className="absolute inset-0 flex items-center justify-center bg-black/60 z-10"
          >
            <div className="flex items-center gap-3 px-5 py-3 rounded-xl bg-gray-900 border border-gray-700">
              <div className="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
              <span className="text-sm text-white/80">Verifying input…</span>
            </div>
          </div>
        )}

        {inputVerification === "FAILED" && (
          <div
            data-testid="input-failed-banner"
            className="absolute top-4 left-1/2 -translate-x-1/2 z-10 flex items-center gap-3 px-5 py-3 rounded-xl bg-red-900/80 border border-red-700"
          >
            <span className="text-sm text-red-200">
              Input not verified: {inputFailReason ?? "unknown"}
            </span>
            <button
              onClick={() => runInputSelftest()}
              className="px-3 py-1 text-xs font-medium bg-red-700 hover:bg-red-600 text-white rounded-lg transition-colors"
            >
              Retry verification
            </button>
          </div>
        )}

        {state === "CONNECTING" && (
          <div className="text-center">
            <div className="inline-block w-8 h-8 border-2 border-blue-400 border-t-transparent rounded-full animate-spin mb-4" />
            <p className="text-sm text-white/70">Connecting to browser session...</p>
          </div>
        )}

        {state === "EXPIRED" && (
          <div className="text-center space-y-4">
            <div className="w-12 h-12 mx-auto rounded-full bg-red-500/20 flex items-center justify-center">
              <svg className="w-6 h-6 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </div>
            <p className="text-sm text-white/70">Session expired or disconnected</p>
            <button
              onClick={handleRestart}
              className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
            >
              Restart session
            </button>
          </div>
        )}

        {state === "ERROR" && (
          <div className="text-center space-y-4">
            <p className="text-sm text-red-400">{lastError || "Connection error"}</p>
            <button
              onClick={handleRestart}
              className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
            >
              Retry
            </button>
          </div>
        )}

        {state === "RECONNECTING" && (
          <div className="absolute top-4 right-4 flex items-center gap-2 px-3 py-1.5 bg-amber-500/20 rounded-lg">
            <div className="w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
            <span className="text-xs text-amber-300">Reconnecting...</span>
          </div>
        )}
      </main>

      <footer className="px-4 py-2 bg-gray-900 border-t border-gray-800">
        <p className="text-[10px] text-white/30 text-center">
          Tailnet-only. Credentials typed here are sent directly to Chromium via CDP.
          Not captured or logged by Browser Gateway.
        </p>
      </footer>
    </div>
  );
}
