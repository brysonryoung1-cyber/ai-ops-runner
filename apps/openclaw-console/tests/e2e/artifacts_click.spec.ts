import { test, expect } from "@playwright/test";

/**
 * Artifacts click tests for OpenClaw HQ.
 * Verifies artifact directory rows are clickable real links.
 * Runs with OPENCLAW_UI_STUB=1 so API returns fixture data.
 */
test.describe("Artifacts Click", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/artifacts");
    await expect(page.getByRole("heading", { name: /Artifacts/i })).toBeVisible({ timeout: 10_000 });
  });

  test("artifact directory rows are rendered as links", async ({ page }) => {
    // Wait for the directory list to load (stub returns ui_telemetry, runs, hostd, pred_markets)
    const listItems = page.locator('main ul a[href^="/artifacts/"]');
    await expect(listItems.first()).toBeVisible({ timeout: 10_000 });

    const count = await listItems.count();
    expect(count).toBeGreaterThanOrEqual(1);

    // Each row should have an href pointing to /artifacts/<name>
    for (let i = 0; i < count; i++) {
      const href = await listItems.nth(i).getAttribute("href");
      expect(href).toMatch(/^\/artifacts\//);
    }
  });

  test("clicking a directory row navigates to artifact browser", async ({ page }) => {
    // Wait for rows to appear
    const firstLink = page.locator('ul a[href*="/artifacts/"]').first();
    await expect(firstLink).toBeVisible({ timeout: 10_000 });

    // Click the first directory link
    await firstLink.click();

    // Should navigate to /artifacts/<name>
    await expect(page).toHaveURL(/\/artifacts\/[^/]+/);

    // Breadcrumbs should show "Artifacts" as a link
    const breadcrumbLink = page.locator('nav[aria-label="Breadcrumb"] a[href="/artifacts"]');
    await expect(breadcrumbLink).toBeVisible({ timeout: 5_000 });
  });

  test("artifact browser shows entries or empty state", async ({ page }) => {
    // Navigate to a known stub directory
    await page.goto("/artifacts/runs");
    await page.waitForTimeout(2000);

    // Page must render meaningful content: either directory entries, file content, or an informational message
    const heading = page.getByRole("heading");
    await expect(heading.first()).toBeVisible();

    const hasEntries = await page.locator("ul a").count();
    const hasMessage = await page.getByText(/empty|not found|error|loading|runs/i).count();
    expect(hasEntries + hasMessage).toBeGreaterThan(0);
  });

  test("each directory row has Open → text", async ({ page }) => {
    const openLabels = page.locator('ul a span:text("Open →")');
    await expect(openLabels.first()).toBeVisible({ timeout: 10_000 });
  });
});
