/**
 * E2E: Browser Gateway health endpoint via tailnet.
 * Verifies /api/browser-gateway/status proxy and /api/agent/status integration.
 */
import { test, expect } from "@playwright/test";

test.describe("Browser Gateway health", () => {
  test("GET /api/agent/status includes browser_gateway check", async ({
    request,
  }) => {
    const res = await request.get("/api/agent/status");
    expect(res.status()).toBe(200);
    const data = await res.json();

    expect(data).toHaveProperty("ok");
    expect(typeof data.ok).toBe("boolean");
    expect(data).toHaveProperty("overall");
    expect(["ok", "blocked"]).toContain(data.overall);
    expect(data).toHaveProperty("checks");
    expect(data.checks).toHaveProperty("browser_gateway");
    expect(["ok", "warn", "blocked", "unknown"]).toContain(
      data.checks.browser_gateway.status,
    );
    expect(typeof data.checks.browser_gateway.detail).toBe("string");
    expect(data).toHaveProperty("human_gate_active");
    expect(typeof data.human_gate_active).toBe("boolean");
  });

  test("fail-closed: if human_gate_active and browser_gateway not ok, overall is blocked", async ({
    request,
  }) => {
    const res = await request.get("/api/agent/status");
    expect(res.status()).toBe(200);
    const data = await res.json();

    if (data.human_gate_active && data.checks.browser_gateway.status !== "ok") {
      expect(data.checks.browser_gateway.status).toBe("blocked");
      expect(data.overall).toBe("blocked");
      expect(data.ok).toBe(false);
      expect(data.remediation.some((r: string) => r.includes("browser-gateway"))).toBe(true);
    }
  });

  test("when browser_gateway is ok, detail contains version and uptime_sec", async ({
    request,
  }) => {
    const res = await request.get("/api/agent/status");
    expect(res.status()).toBe(200);
    const data = await res.json();

    if (data.checks.browser_gateway.status === "ok") {
      expect(data.checks.browser_gateway.detail).toContain("version=");
      expect(data.checks.browser_gateway.detail).toContain("uptime_sec=");
    }
  });
});
