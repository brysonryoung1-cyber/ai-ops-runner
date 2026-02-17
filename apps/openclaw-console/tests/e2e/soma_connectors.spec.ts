import { test, expect } from "@playwright/test";

/**
 * Hermetic UI smoke tests for Soma Connectors on /projects/soma_kajabi.
 * Requires OPENCLAW_UI_STUB=1 (no real hostd/Kajabi/Gmail credentials).
 * Verifies that connector buttons trigger backend action and show visible success/error.
 */
test.describe("Soma Connectors UI", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/projects/soma_kajabi");
    await expect(page.getByRole("heading", { name: /soma/i })).toBeVisible({ timeout: 10_000 });
  });

  test("Check Connectors button triggers request and shows feedback", async ({ page }) => {
    await page.getByRole("button", { name: /Check Connectors/i }).click();
    await expect(page.getByText(/Done\. Run:|error_class|UI_ACTION_FAILED/).first()).toBeVisible({ timeout: 15_000 });
  });

  test("Kajabi Bootstrap button triggers request and shows feedback", async ({ page }) => {
    await page.getByRole("button", { name: /Kajabi Bootstrap/i }).first().click();
    await expect(page.getByText(/Done\. Run:|error_class|UI_ACTION_FAILED/).first()).toBeVisible({ timeout: 15_000 });
  });

  test("Gmail Connect button triggers request and shows feedback", async ({ page }) => {
    await page.getByRole("button", { name: /Gmail Connect/i }).first().click();
    await expect(page.getByText(/Done\. Run:|error_class|UI_ACTION_FAILED/).first()).toBeVisible({ timeout: 15_000 });
  });

  test("Connectors section shows Kajabi and Gmail status badges", async ({ page }) => {
    const card = page.locator("h3", { hasText: "Connectors" }).locator("..");
    await expect(card.getByText("Kajabi", { exact: true })).toBeVisible();
    await expect(card.getByText("Gmail", { exact: true })).toBeVisible();
    await expect(card.locator("text=/Connected|Not connected|—/").first()).toBeVisible();
  });

  test("Refresh status button triggers request", async ({ page }) => {
    await page.getByRole("button", { name: /Refresh status/i }).click();
    await expect(page.getByText(/Done\. Run:|error_class|UI_ACTION_FAILED/).first()).toBeVisible({ timeout: 15_000 });
  });
});
