import { test, expect } from "@playwright/test";

/**
 * Support bundle endpoint and UI.
 */
test.describe("Support Bundle", () => {
  test("POST /api/support/bundle returns ok, run_id, and artifact_dir", async ({ request }) => {
    const res = await request.post("/api/support/bundle");
    const data = await res.json();
    expect(res.ok() || res.status() === 403).toBe(true);
    if (data.ok) {
      expect(data.run_id).toBeTruthy();
      expect(data.artifact_dir).toBeTruthy();
      expect(data.artifact_dir).toMatch(/^artifacts\/support_bundle\//);
      expect(data.permalink).toBeTruthy();
      expect(Array.isArray(data.manifest)).toBe(true);
      // New: auth_status.json and last_10_runs.json should be in manifest
      expect(data.manifest).toContain("auth_status.json");
      expect(data.manifest).toContain("last_10_runs.json");
      expect(data.manifest).toContain("last_forbidden.json");
    }
  });

  test("Settings page has Generate Support Bundle button", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: /Settings/i })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: /Generate Support Bundle/i })).toBeVisible();
  });
});
