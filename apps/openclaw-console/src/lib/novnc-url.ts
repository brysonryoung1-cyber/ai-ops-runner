/**
 * noVNC URL canonicalization — same-origin, path=/websockify.
 *
 * Canonical: https://<host>/novnc/vnc.html?autoconnect=1&reconnect=true&reconnect_delay=2000&path=/websockify
 * Legacy: http://<host>:6080/... or https://<host>/novnc/vnc.html?autoconnect=1 (no path/reconnect params)
 *
 * Ensures WS upgrade completes (Tailscale Serve /websockify -> 6080).
 */

const CANONICAL_PATH =
  "/novnc/vnc.html?autoconnect=1&reconnect=true&reconnect_delay=2000&path=/websockify";

/**
 * Returns canonical noVNC URL for the given host.
 */
export function buildCanonicalNovncUrl(host: string): string {
  const h = host.replace(/^https?:\/\//, "").split("/")[0].split(":")[0];
  return `https://${h}${CANONICAL_PATH}`;
}

/**
 * Normalize any noVNC URL to canonical form.
 * - http://...:6080/... => canonical (https + reconnect + path)
 * - https://<host>/novnc/vnc.html?autoconnect=1 (no path/reconnect) => add missing params
 */
export function toCanonicalNovncUrl(url: string | null | undefined): string | null {
  if (!url || typeof url !== "string") return null;
  const trimmed = url.trim();
  if (!trimmed) return null;

  try {
    const u = new URL(trimmed);
    const host = u.hostname;
    if (!host || host === "localhost" || host === "127.0.0.1") return null;

    // Already canonical (check all required params)
    if (
      u.protocol === "https:" &&
      u.pathname === "/novnc/vnc.html" &&
      u.searchParams.get("path") === "/websockify" &&
      u.searchParams.get("reconnect") === "true"
    ) {
      return trimmed;
    }

    // Legacy http or :6080
    if (u.protocol === "http:" || u.port === "6080" || trimmed.includes(":6080")) {
      return buildCanonicalNovncUrl(host);
    }

    // https /novnc/vnc.html but missing path or reconnect params
    if (u.protocol === "https:" && u.pathname === "/novnc/vnc.html") {
      const pathParam = u.searchParams.get("path");
      const hasReconnect = u.searchParams.get("reconnect") === "true";
      if (pathParam !== "/websockify" || !hasReconnect) {
        const next = new URL(trimmed);
        next.searchParams.set("path", "/websockify");
        next.searchParams.set("reconnect", "true");
        next.searchParams.set("reconnect_delay", "2000");
        return next.toString();
      }
      return trimmed;
    }

    // Generic https host — assume same host
    if (u.protocol === "https:") {
      return buildCanonicalNovncUrl(host);
    }

    return buildCanonicalNovncUrl(host);
  } catch {
    return null;
  }
}
