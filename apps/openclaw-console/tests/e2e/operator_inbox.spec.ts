/**
 * Operator Inbox: API response shape and /inbox page rendering.
 */
import { test, expect } from "@playwright/test";

test.describe("GET /api/operator-inbox", () => {
  test("returns required top-level keys: waiting_for_human, degraded, last_proof, last_deploy, last_canary", async ({
    request,
  }) => {
    const res = await request.get("/api/operator-inbox");
    expect(res.status()).toBe(200);
    const data = await res.json();

    expect(data).toHaveProperty("waiting_for_human");
    expect(Array.isArray(data.waiting_for_human)).toBe(true);

    expect(data).toHaveProperty("degraded");
    expect(Array.isArray(data.degraded)).toBe(true);

    expect(data).toHaveProperty("last_proof");
    expect(data.last_proof).toHaveProperty("tree_sha");
    expect(data.last_proof).toHaveProperty("run_id");
    expect(data.last_proof).toHaveProperty("proof_link");
    expect(data.last_proof).toHaveProperty("timestamp");

    expect(data).toHaveProperty("last_deploy");
    expect(data.last_deploy).toHaveProperty("build_sha");
    expect(data.last_deploy).toHaveProperty("deploy_time");
    expect(data.last_deploy).toHaveProperty("version_link");

    expect(data).toHaveProperty("last_canary");
    expect(data.last_canary).toHaveProperty("status");
    expect(data.last_canary).toHaveProperty("run_id");
    expect(data.last_canary).toHaveProperty("proof_link");
    expect(data.last_canary).toHaveProperty("timestamp");
  });

  test("waiting_for_human items have project_id, run_id, reason, canonical_url, single_instruction, artifacts_link", async ({
    request,
  }) => {
    const res = await request.get("/api/operator-inbox");
    expect(res.status()).toBe(200);
    const data = await res.json();

    for (const item of data.waiting_for_human) {
      expect(item).toHaveProperty("project_id");
      expect(typeof item.project_id).toBe("string");
      expect(item).toHaveProperty("run_id");
      expect(typeof item.run_id).toBe("string");
      expect(item).toHaveProperty("reason");
      expect(typeof item.reason).toBe("string");
      expect(item).toHaveProperty("canonical_url");
      expect(item).toHaveProperty("single_instruction");
      expect(item).toHaveProperty("artifacts_link");
    }
  });

  test("degraded items have subsystem, run_id, failing_checks, proof_link, incident_link", async ({
    request,
  }) => {
    const res = await request.get("/api/operator-inbox");
    expect(res.status()).toBe(200);
    const data = await res.json();

    for (const item of data.degraded) {
      expect(item).toHaveProperty("subsystem");
      expect(typeof item.subsystem).toBe("string");
      expect(item).toHaveProperty("run_id");
      expect(typeof item.run_id).toBe("string");
      expect(item).toHaveProperty("failing_checks");
      expect(Array.isArray(item.failing_checks)).toBe(true);
      expect(item).toHaveProperty("proof_link");
      expect(item).toHaveProperty("incident_link");
    }
  });
});

test.describe("/inbox page", () => {
  test("renders Operator Inbox heading and three sections", async ({ page }) => {
    await page.goto("/inbox");
    await expect(page.getByRole("heading", { name: /Operator Inbox/i })).toBeVisible({
      timeout: 10_000,
    });

    await expect(page.getByRole("heading", { name: "Needs You" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Degraded" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Recent" })).toBeVisible();
  });

  test("page has data-testid operator-inbox-page", async ({ page }) => {
    await page.goto("/inbox");
    await expect(page.getByTestId("operator-inbox-page")).toBeVisible({ timeout: 10_000 });
  });

  test("snapshot: inbox layout structure", async ({ page }) => {
    await page.goto("/inbox");
    await expect(page.getByTestId("operator-inbox-page")).toBeVisible({ timeout: 10_000 });

    const sections = page.locator("section");
    await expect(sections).toHaveCount(3);

    const headings = page.locator("section h3");
    await expect(headings).toHaveCount(3);
    await expect(headings.nth(0)).toContainText("Needs You");
    await expect(headings.nth(1)).toContainText("Degraded");
    await expect(headings.nth(2)).toContainText("Recent");
  });
});
