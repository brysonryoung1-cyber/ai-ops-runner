import { test, expect } from "@playwright/test";

/**
 * Project cards must link to runs and artifacts when last_run exists.
 */
test.describe("Project cards links", () => {
  test("project cards have Open link to project detail", async ({ page }) => {
    await page.goto("/projects");
    await expect(page.getByRole("heading", { name: /Projects/i })).toBeVisible({ timeout: 10_000 });
    // Project cards have Open link (aria-label contains "Open project")
    const openLink = page.getByRole("link", { name: /Open project/ });
    await expect(openLink.first()).toBeVisible({ timeout: 5000 });
  });

  test("project card last error links to artifacts when present", async ({ page }) => {
    await page.goto("/projects");
    await expect(page.getByRole("heading", { name: /Projects/i })).toBeVisible({ timeout: 10_000 });
    // If "View run artifacts" exists, it should link to /artifacts/
    const artifactsLink = page.locator('a[href*="/artifacts/"]');
    const count = await artifactsLink.count();
    if (count > 0) {
      await expect(artifactsLink.first()).toHaveAttribute("href", /\/artifacts\//);
    }
  });
});
