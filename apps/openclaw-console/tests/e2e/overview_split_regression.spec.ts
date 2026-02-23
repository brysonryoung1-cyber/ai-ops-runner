import { test, expect } from "@playwright/test";

/**
 * Regression: Overview must not crash when exec results have undefined stdout.
 * Root cause: parseDoctorSummary/parsePortSummary/etc called .split() on undefined
 * when hostd returns 502 (HOSTD_UNREACHABLE) with no stdout in the response.
 *
 * Test cases: field undefined, empty string, null.
 */
test.describe("Overview split regression", () => {
  test("Overview renders without crash when exec returns partial response (no stdout)", async ({
    page,
  }) => {
    // Intercept host-executor status to say "connected" so Overview triggers exec calls
    await page.route("**/api/host-executor/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, latency_ms: 1 }),
      });
    });

    // Intercept exec to return 502-like partial response (no stdout/stderr)
    await page.route("**/api/exec", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({
            ok: false,
            error_class: "HOSTD_UNREACHABLE",
            error_summary: "Host Executor unreachable",
            action: "doctor",
            run_id: "test-run",
            artifact_dir: "artifacts/hostd/unreachable_test",
          }),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto("/");
    // Page must render; Control Center heading indicates Overview loaded
    await expect(page.getByRole("heading", { name: /Control Center/i })).toBeVisible({
      timeout: 10_000,
    });
    // No uncaught "split" error — if we get here without crash, we're good
  });

  test("Overview renders when exec returns empty stdout", async ({ page }) => {
    await page.route("**/api/host-executor/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, latency_ms: 1 }),
      });
    });

    await page.route("**/api/exec", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            action: "doctor",
            stdout: "",
            stderr: "",
            exitCode: 0,
            durationMs: 100,
          }),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto("/");
    await expect(page.getByRole("heading", { name: /Control Center/i })).toBeVisible({
      timeout: 10_000,
    });
  });

  test("Overview renders when exec returns null-like stdout (explicit null in JSON)", async ({
    page,
  }) => {
    await page.route("**/api/host-executor/status", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, latency_ms: 1 }),
      });
    });

    await page.route("**/api/exec", async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            action: "doctor",
            stdout: null,
            stderr: null,
            exitCode: 0,
            durationMs: 100,
          }),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto("/");
    await expect(page.getByRole("heading", { name: /Control Center/i })).toBeVisible({
      timeout: 10_000,
    });
  });
});
