import { test, expect } from "@playwright/test";

/**
 * Runs click tests for OpenClaw HQ.
 * Verifies run rows have anchor links for no-JS fallback.
 * Runs with OPENCLAW_UI_STUB=1.
 */
test.describe("Runs Click", () => {
  test("runs page loads and shows heading", async ({ page }) => {
    await page.goto("/runs");
    await expect(page.getByRole("heading", { name: /Runs/i })).toBeVisible({ timeout: 10_000 });
  });

  test("run rows have Permalink anchor links", async ({ page }) => {
    await page.goto("/runs");
    await expect(page.getByRole("heading", { name: /Runs/i })).toBeVisible({ timeout: 10_000 });

    // Wait for runs to load
    await page.waitForTimeout(3000);

    const permalinks = page.locator('a:has-text("Permalink")');
    const count = await permalinks.count();

    if (count === 0) {
      // No runs recorded — verify empty state is shown
      const emptyOrLoading = page.getByText(/No runs|Loading/i);
      await expect(emptyOrLoading.first()).toBeVisible();
      return;
    }

    // Each permalink should be a real <a> with href
    for (let i = 0; i < Math.min(count, 5); i++) {
      const href = await permalinks.nth(i).getAttribute("href");
      expect(href).toMatch(/^\/runs\?id=/);
    }
  });

  test("clicking a run row expands detail panel", async ({ page }) => {
    await page.goto("/runs");
    await expect(page.getByRole("heading", { name: /Runs/i })).toBeVisible({ timeout: 10_000 });

    await page.waitForTimeout(3000);

    const runButtons = page.locator("ul button");
    const count = await runButtons.count();

    if (count === 0) {
      // No runs, skip
      return;
    }

    // Click the first run row button
    await runButtons.first().click();

    // Detail panel should appear with metadata
    await expect(page.getByText(/Duration|Project|Started|Finished/i).first()).toBeVisible({ timeout: 5_000 });
  });

  test("Clear filter link uses anchor tag", async ({ page }) => {
    await page.goto("/runs?project=test_project");
    await expect(page.getByRole("heading", { name: /Runs/i })).toBeVisible({ timeout: 10_000 });

    const clearLink = page.locator('a[href="/runs"]:has-text("Clear filter")');
    await expect(clearLink).toBeVisible();
    await expect(clearLink).toHaveAttribute("href", "/runs");
  });
});
