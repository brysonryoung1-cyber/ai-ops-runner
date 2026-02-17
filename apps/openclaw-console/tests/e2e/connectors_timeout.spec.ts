import { test, expect } from "@playwright/test";

/**
 * Soma Connectors page must not remain "Checking…" indefinitely.
 * With OPENCLAW_UI_STUB=1, connector status returns quickly (stub fixture).
 */
test.describe("Connectors status timeout", () => {
  test("connectors card resolves within 20s — shows status or explicit error", async ({ page }) => {
    await page.goto("/projects/soma_kajabi");
    await expect(page.getByRole("heading", { name: /soma|kajabi|phase/i }).first()).toBeVisible({ timeout: 10_000 });
    const card = page.locator("h3", { hasText: "Connectors" }).locator("..");
    await expect(card.getByText("Kajabi", { exact: true })).toBeVisible();
    await expect(card.getByText("Gmail", { exact: true })).toBeVisible();
    // Within 20s, must show resolved state (status badges or error) — not stuck on "Checking…"
    await expect(
      card.locator("text=/Connected|Not connected|—|error_class|Error|Done\.|Run:/").first()
    ).toBeVisible({ timeout: 20_000 });
  });
});
