/**
 * E2E: Run clock + single-flight fix verification.
 *
 * Tests:
 *  1. /api/runs returns valid timestamps (no NaN, no INVALID DATE)
 *  2. Runs page renders without INVALID DATE or NaN
 *  3. system.repair_run_state action works
 *  4. Single-flight: concurrent start attempts → 409 with active_run_id
 */
import { test, expect } from "@playwright/test";

test.describe("Run Clock + Single-flight", () => {
  test("GET /api/runs returns valid timestamps — no NaN or undefined", async ({
    request,
  }) => {
    const res = await request.get("/api/runs?limit=50");
    expect(res.status()).toBe(200);
    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(Array.isArray(data.runs)).toBe(true);

    for (const run of data.runs) {
      expect(run).toHaveProperty("run_id");
      expect(run).toHaveProperty("started_at");
      expect(typeof run.started_at).toBe("string");

      const startedMs = new Date(run.started_at).getTime();
      expect(isNaN(startedMs)).toBe(false);

      if (run.status !== "running" && run.status !== "queued") {
        if (run.finished_at !== undefined) {
          expect(typeof run.finished_at).toBe("string");
          const finishedMs = new Date(run.finished_at).getTime();
          expect(isNaN(finishedMs)).toBe(false);
        }
        expect(run.duration_ms).toBeGreaterThanOrEqual(0);
        expect(isFinite(run.duration_ms)).toBe(true);
      }
    }
  });

  test("Runs page does not show INVALID DATE or NaN", async ({ page }) => {
    await page.goto("/runs");
    await expect(
      page.getByRole("heading", { name: /Runs/i }),
    ).toBeVisible({ timeout: 10_000 });

    await page.waitForTimeout(3000);

    const body = await page.locator("body").textContent();
    expect(body).not.toContain("INVALID DATE");
    expect(body).not.toContain("Invalid Date");
    expect(body).not.toMatch(/NaN\s*(ago|ms|s|m|h|d)/);
  });

  test("system.repair_run_state returns repaired_count", async ({
    request,
  }) => {
    const res = await request.post("/api/exec", {
      data: { action: "system.repair_run_state" },
    });
    expect(res.status()).toBe(200);
    const data = await res.json();
    expect(data.ok).toBe(true);
    expect(data.action).toBe("system.repair_run_state");
    expect(typeof data.repaired_count).toBe("number");
    expect(data.repaired_count).toBeGreaterThanOrEqual(0);
  });

  test("single-flight: second exec start returns 409 with active_run_id (soma_kajabi_auto_finish)", async ({
    request,
  }) => {
    const action = "soma_kajabi_auto_finish";

    const first = await request.post("/api/exec", {
      data: { action },
    });

    if (first.status() === 409) {
      const body = await first.json();
      expect(body.active_run_id).toBeTruthy();
      expect(body.error_class).toBe("ALREADY_RUNNING");

      const second = await request.post("/api/exec", {
        data: { action },
      });
      expect(second.status()).toBe(409);
      const body2 = await second.json();
      expect(body2.active_run_id).toBeTruthy();
      expect(body2.error_class).toBe("ALREADY_RUNNING");
    } else if (first.status() === 202) {
      const body = await first.json();
      expect(body.run_id).toBeTruthy();

      const second = await request.post("/api/exec", {
        data: { action },
      });
      expect(second.status()).toBe(409);
      const body2 = await second.json();
      expect(body2.active_run_id).toBeTruthy();
      expect(body2.error_class).toBe("ALREADY_RUNNING");
    } else if (first.status() === 502) {
      test.skip(true, "Hostd unreachable — skip single-flight test");
    }
  });

  test("detail panel shows Started/Duration without NaN for running runs", async ({
    page,
  }) => {
    await page.goto("/runs");
    await expect(
      page.getByRole("heading", { name: /Runs/i }),
    ).toBeVisible({ timeout: 10_000 });
    await page.waitForTimeout(3000);

    const runningSection = page.locator('text="Currently Running"');
    const hasRunning = (await runningSection.count()) > 0;
    if (!hasRunning) return;

    const firstRunButton = page
      .locator("ul button")
      .first();
    if ((await firstRunButton.count()) === 0) return;

    await firstRunButton.click();
    await page.waitForTimeout(500);

    const panel = page.locator("body");
    const text = (await panel.textContent()) ?? "";
    expect(text).not.toContain("NaN");
    expect(text).not.toContain("Invalid Date");
  });
});
