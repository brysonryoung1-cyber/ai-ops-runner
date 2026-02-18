import { test, expect } from "@playwright/test";

/**
 * Connector Secrets Upload flow: Gmail requirements API and Settings Gmail OAuth section.
 * - GET /api/connectors/gmail/requirements returns redirect URIs and filename.
 * - Settings page shows Connectors → Gmail OAuth section.
 */
test.describe("Connectors Gmail requirements and Settings", () => {
  test("GET /api/connectors/gmail/requirements returns required_redirect_uris and filename", async ({
    request,
  }) => {
    const res = await request.get("/api/connectors/gmail/requirements");
    expect(res.ok()).toBe(true);
    const data = await res.json();

    expect(data.ok).toBe(true);
    expect(Array.isArray(data.required_redirect_uris)).toBe(true);
    expect(data.required_redirect_uris.length).toBeGreaterThan(0);
    expect(data.required_redirect_uris).toContain("https://www.google.com/device");
    expect(data.filename_expected).toBe("gmail_client.json");
    expect(Array.isArray(data.required_scopes)).toBe(true);
    expect(data.required_scopes).toContain(
      "https://www.googleapis.com/auth/gmail.readonly"
    );
  });

  test("Settings page shows Connectors → Gmail OAuth section", async ({
    page,
  }) => {
    await page.goto("/settings");
    await expect(
      page.getByRole("heading", { name: /Settings/i })
    ).toBeVisible({ timeout: 10_000 });

    const section = page.locator('[data-testid="connectors-gmail-oauth"]');
    await expect(section).toBeVisible({ timeout: 10_000 });

    await expect(section.getByText("Gmail OAuth")).toBeVisible();
    await expect(section.locator("code").filter({ hasText: "gmail_client.json" }).first()).toBeVisible();
    await expect(section.getByText("How to get gmail_client.json")).toBeVisible();
  });
});
