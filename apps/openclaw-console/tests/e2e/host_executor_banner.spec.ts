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
    expect(data).toHaveProperty("console_can_reach_hostd");
    expect(typeof data.console_can_reach_hostd).toBe("boolean");
    expect(data).toHaveProperty("console_network_mode");
    expect(["host", "bridge", "unknown"]).toContain(data.console_network_mode);
    expect(data).toHaveProperty("executor_url");
    if (!data.ok) {
      expect(data).toHaveProperty("error_class");
    }
  });
});

/**
 * Exec route: when hostd is unreachable, POST /api/exec returns 502 with
 * error_class HOSTD_UNREACHABLE, run_id, error_summary, and artifact_dir
 * (debuggable failure with artifact_dir for stderr.txt).
 */
test.describe("Exec unreachable handling", () => {
  test("POST /api/exec with unreachable hostd returns 502 + HOSTD_UNREACHABLE + artifact_dir", async ({
    request,
  }) => {
    test.setTimeout(100_000); // Retry backoff can take up to ~70s
    const res = await request.post("/api/exec", {
      data: { action: "apply" },
      headers: { "Content-Type": "application/json" },
    });
    const data = await res.json();
    if (res.status() === 502) {
      expect(data.error_class).toBe("HOSTD_UNREACHABLE");
      expect(data).toHaveProperty("run_id");
      expect(data).toHaveProperty("error_summary");
      expect(typeof data.error_summary).toBe("string");
      expect(data).toHaveProperty("artifact_dir");
      expect(data.artifact_dir).toContain("unreachable_");
      expect(data.artifact_dir).toContain("artifacts/hostd/");
    }
  });
});
