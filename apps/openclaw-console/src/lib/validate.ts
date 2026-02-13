import { readFileSync } from "fs";
import { join } from "path";
import { homedir } from "os";

/**
 * Validates that a host IP is within the Tailscale CGNAT range (100.64.0.0/10).
 * Fail-closed: returns false for anything outside this range.
 */
export function isTailscaleIP(ip: string): boolean {
  const parts = ip.split(".");
  if (parts.length !== 4) return false;

  const octets = parts.map(Number);
  if (octets.some((o) => isNaN(o) || o < 0 || o > 255)) return false;

  // 100.64.0.0/10 → first octet == 100, second octet 64–127
  return octets[0] === 100 && octets[1] >= 64 && octets[1] <= 127;
}

/**
 * Read the active target from ~/.config/openclaw/targets.json.
 * Returns { host, user } or null if not configured / file missing.
 */
function getActiveTargetFromFile(): { host: string; user: string } | null {
  try {
    const targetsPath = join(homedir(), ".config", "openclaw", "targets.json");
    const raw = readFileSync(targetsPath, "utf-8");
    const data = JSON.parse(raw);
    const active = data?.active;
    if (active && data?.targets?.[active]) {
      const t = data.targets[active];
      if (t.host) {
        return { host: t.host, user: t.user || "root" };
      }
    }
  } catch {
    // File doesn't exist or invalid JSON — fall through to env
  }
  return null;
}

/**
 * Returns validated host from targets.json or AIOPS_HOST env var.
 * Prefers targets.json active target, falls back to env var.
 * Throws with a clear message if not configured or not a Tailscale IP.
 */
export function getValidatedHost(): string {
  // 1. Try targets file
  const target = getActiveTargetFromFile();
  if (target?.host) {
    if (!isTailscaleIP(target.host)) {
      throw new Error(
        `Active target host ${target.host} is not in the Tailscale CGNAT range (100.64.0.0/10). Refusing to connect.`
      );
    }
    return target.host;
  }

  // 2. Fall back to env var
  const host = process.env.AIOPS_HOST;
  if (!host) {
    throw new Error(
      "No target configured. Set up targets with: python3 ops/openclaw_targets.py init\n" +
        "Or copy .env.example to .env.local and set AIOPS_HOST."
    );
  }
  if (!isTailscaleIP(host)) {
    throw new Error(
      `AIOPS_HOST=${host} is not in the Tailscale CGNAT range (100.64.0.0/10). Refusing to connect.`
    );
  }
  return host;
}

/**
 * Returns SSH user from targets.json or AIOPS_USER env, defaulting to "root".
 */
export function getUser(): string {
  const target = getActiveTargetFromFile();
  if (target?.user) return target.user;
  return process.env.AIOPS_USER || "root";
}
