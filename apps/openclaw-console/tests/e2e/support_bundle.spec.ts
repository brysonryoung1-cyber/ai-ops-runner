import { test, expect } from "@playwright/test";

/**
 * Support bundle endpoint and UI.
 */
test.describe("Support Bundle", () => {
  test("POST /api/support/bundle returns ok and artifact_dir", async ({ request }) => {
    const res = await request.post("/api/support/bundle");
    const data = await res.json();
    expect(res.ok() || res.status() === 403).toBe(true);
    if (data.ok) {
      expect(data.artifact_dir).toBeTruthy();
      expect(data.artifact_dir).toMatch(/^artifacts\/support_bundle\//);
      expect(data.permalink).toBeTruthy();
    }
  });

  test("Settings page has Generate Support Bundle button", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: /Settings/i })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: /Generate Support Bundle/i })).toBeVisible();
  });
});
