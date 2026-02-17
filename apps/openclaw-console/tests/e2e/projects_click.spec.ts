import { test, expect } from "@playwright/test";

/**
 * Projects click tests for OpenClaw HQ.
 * Verifies project cards have clickable "Open →" links.
 * Runs with OPENCLAW_UI_STUB=1 but relies on config/projects.json for data.
 */
test.describe("Projects Click", () => {
  test("projects page loads and shows heading", async ({ page }) => {
    await page.goto("/projects");
    await expect(page.getByRole("heading", { name: /Projects/i })).toBeVisible({ timeout: 10_000 });
  });

  test("project cards have Open → links that navigate", async ({ page }) => {
    await page.goto("/projects");
    await expect(page.getByRole("heading", { name: /Projects/i })).toBeVisible({ timeout: 10_000 });

    // Wait for project cards to load
    await page.waitForTimeout(3000);
    const openLinks = page.locator('a:has-text("Open →")');
    const count = await openLinks.count();

    if (count === 0) {
      // No projects in registry — skip navigation test but page loaded fine
      return;
    }

    // Each Open → should be a real <a> with an href
    for (let i = 0; i < count; i++) {
      const href = await openLinks.nth(i).getAttribute("href");
      expect(href).toMatch(/^\/projects\//);
    }

    // Click the first Open → link
    const firstHref = await openLinks.first().getAttribute("href");
    await openLinks.first().click();
    await expect(page).toHaveURL(new RegExp(firstHref!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  });

  test("project title is a clickable link", async ({ page }) => {
    await page.goto("/projects");
    await expect(page.getByRole("heading", { name: /Projects/i })).toBeVisible({ timeout: 10_000 });

    // Wait for cards
    await page.waitForTimeout(2000);

    // Project titles should be links
    const titleLinks = page.locator("h3 a");
    const count = await titleLinks.count();

    if (count === 0) {
      // No projects, that's fine
      return;
    }

    for (let i = 0; i < count; i++) {
      const href = await titleLinks.nth(i).getAttribute("href");
      expect(href).toMatch(/^\/projects\//);
    }
  });
});
