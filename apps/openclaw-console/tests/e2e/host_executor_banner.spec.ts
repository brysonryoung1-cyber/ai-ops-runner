import { test, expect } from "@playwright/test";

/**
 * Host Executor banner must resolve within timeout (no infinite "Checking…").
 * With OPENCLAW_UI_STUB=1 and no OPENCLAW_HOSTD_URL, banner shows explicit failure.
 * With hostd available, banner shows connected state.
 */
test.describe("Host Executor banner", () => {
  test("banner resolves within 6s — shows either connected or explicit failure", async ({ page }) => {
    await page.goto("/");
    // Wait max 6s for banner to resolve (no longer "Checking…")
    await expect(
      page.locator("text=Checking Host Executor connectivity…")
    ).not.toBeVisible({ timeout: 6000 });
    // Should show either: "Host Executor unreachable" OR the Doctor/Guard/LLM section (connected)
    const unreachable = page.locator("text=Host Executor unreachable");
    const connectedSection = page.locator("text=Doctor").first();
    await expect(unreachable.or(connectedSection).first()).toBeVisible({ timeout: 2000 });
  });

  test("host-executor status endpoint returns structured response", async ({ request }) => {
    const res = await request.get("/api/host-executor/status");
    const data = await res.json();
    expect(data).toHaveProperty("ok");
    expect(data).toHaveProperty("latency_ms");
    expect(typeof data.ok).toBe("boolean");
    expect(typeof data.latency_ms).toBe("number");
    if (!data.ok) {
      expect(data).toHaveProperty("error_class");
    }
  });
});
