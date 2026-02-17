import { test, expect } from "@playwright/test";

/**
 * Navigation smoke tests for OpenClaw HQ.
 * Verifies sidebar navigation works via real <a> tags.
 * Runs with OPENCLAW_UI_STUB=1 (no hostd/credentials).
 */
test.describe("Navigation Smoke", () => {
  test("sidebar links navigate to correct pages", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/OpenClaw HQ/);

    // Click Projects
    const projectsLink = page.locator('nav[aria-label="Main navigation"] a[href="/projects"]');
    await expect(projectsLink).toBeVisible({ timeout: 10_000 });
    await projectsLink.click();
    await expect(page).toHaveURL(/\/projects/);
    await expect(page.getByRole("heading", { name: /Projects/i })).toBeVisible();

    // Click Runs
    const runsLink = page.locator('nav[aria-label="Main navigation"] a[href="/runs"]');
    await runsLink.click();
    await expect(page).toHaveURL(/\/runs/);
    await expect(page.getByRole("heading", { name: /Runs/i })).toBeVisible();

    // Click Artifacts
    const artifactsLink = page.locator('nav[aria-label="Main navigation"] a[href="/artifacts"]');
    await artifactsLink.click();
    await expect(page).toHaveURL(/\/artifacts/);
    await expect(page.getByRole("heading", { name: /Artifacts/i })).toBeVisible();

    // Click Settings
    const settingsLink = page.locator('nav[aria-label="Main navigation"] a[href="/settings"]');
    await settingsLink.click();
    await expect(page).toHaveURL(/\/settings/);
    await expect(page.getByRole("heading", { name: /Settings/i })).toBeVisible();

    // Click Overview (back to root)
    const overviewLink = page.locator('nav[aria-label="Main navigation"] a[href="/"]');
    await overviewLink.click();
    await expect(page).toHaveURL(/\/$/);
  });

  test("sidebar links are real anchor tags with href", async ({ page }) => {
    await page.goto("/");
    await page.waitForSelector('nav[aria-label="Main navigation"]', { timeout: 10_000 });

    const links = page.locator('nav[aria-label="Main navigation"] a');
    const count = await links.count();
    expect(count).toBeGreaterThanOrEqual(5);

    for (let i = 0; i < count; i++) {
      const href = await links.nth(i).getAttribute("href");
      expect(href).toBeTruthy();
      expect(href).toMatch(/^\//);
    }
  });

  test("active sidebar link has aria-current=page", async ({ page }) => {
    await page.goto("/projects");
    await page.waitForSelector('nav[aria-label="Main navigation"]', { timeout: 10_000 });
    const activeLink = page.locator('nav[aria-label="Main navigation"] a[aria-current="page"]');
    await expect(activeLink).toHaveAttribute("href", "/projects");
  });

  test("hydration badge shows Client: Active", async ({ page }) => {
    await page.goto("/");
    const badge = page.locator('[data-testid="hydration-badge"]');
    await expect(badge).toContainText("Client: Active", { timeout: 5_000 });
  });
});
