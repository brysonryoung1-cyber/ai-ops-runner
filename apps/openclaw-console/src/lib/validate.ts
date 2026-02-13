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
 * Returns validated AIOPS_HOST from env, or throws with a clear message.
 */
export function getValidatedHost(): string {
  const host = process.env.AIOPS_HOST;
  if (!host) {
    throw new Error(
      "AIOPS_HOST is not set. Copy .env.example to .env.local and set AIOPS_HOST to the Tailscale IP of aiops-1."
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
 * Returns AIOPS_USER from env, defaulting to "root".
 */
export function getUser(): string {
  return process.env.AIOPS_USER || "root";
}
