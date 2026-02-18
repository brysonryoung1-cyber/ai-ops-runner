import { test, expect } from "@playwright/test";

/**
 * Auth diagnostics: /api/auth/status endpoint and Settings UI.
 */
test.describe("Auth Diagnostics", () => {
  test("GET /api/auth/status returns diagnostic fields without secrets", async ({
    request,
  }) => {
    const res = await request.get("/api/auth/status");
    expect(res.ok()).toBe(true);
    const data = await res.json();

    expect(data.ok).toBe(true);
    expect(typeof data.hq_token_required).toBe("boolean");
    expect(typeof data.admin_token_loaded).toBe("boolean");
    expect(typeof data.host_executor_reachable).toBe("boolean");
    expect(typeof data.build_sha).toBe("string");
    expect(data.build_sha.length).toBeGreaterThan(0);
    expect(Array.isArray(data.ui_routes)).toBe(true);
    expect(data.ui_routes.length).toBeGreaterThan(0);
    expect(typeof data.trust_tailscale).toBe("boolean");
    expect(Array.isArray(data.notes)).toBe(true);

    // Must NOT contain raw tokens
    const raw = JSON.stringify(data);
    expect(raw).not.toMatch(/sk-[a-zA-Z0-9_-]{20,}/);
    expect(raw).not.toMatch(/ghp_/);
  });

  test("GET /api/ui/health_public returns non-sensitive health data", async ({
    request,
  }) => {
    const res = await request.get("/api/ui/health_public");
    expect(res.ok()).toBe(true);
    const data = await res.json();

    expect(data.ok).toBe(true);
    expect(typeof data.build_sha).toBe("string");
    expect(Array.isArray(data.routes)).toBe(true);
    expect(typeof data.artifacts).toBe("object");
    expect(typeof data.artifacts.readable).toBe("boolean");
    expect(typeof data.server_time).toBe("string");
  });

  test("Settings page shows build SHA and auth status panel", async ({
    page,
  }) => {
    await page.goto("/settings");
    await expect(
      page.getByRole("heading", { name: /Settings/i })
    ).toBeVisible({ timeout: 10_000 });

    // Auth status panel should be visible
    const panel = page.locator('[data-testid="auth-status-panel"]');
    await expect(panel).toBeVisible({ timeout: 10_000 });

    // Build SHA should be shown
    const buildSha = page.locator('[data-testid="build-sha"]');
    await expect(buildSha).toBeVisible({ timeout: 10_000 });
    const shaText = await buildSha.textContent();
    expect(shaText).toMatch(/Build:\s*\S+/);

    // Admin Token Loaded status badge
    await expect(panel.getByText("Admin Token Loaded")).toBeVisible();

    // Host Executor Reachable status badge
    await expect(panel.getByText("Host Executor Reachable")).toBeVisible();
  });

  test("Sidebar shows build SHA", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle(/OpenClaw HQ/);

    const buildSha = page.locator('[data-testid="sidebar-build-sha"]');
    await expect(buildSha).toBeVisible({ timeout: 10_000 });
    const text = await buildSha.textContent();
    expect(text?.length).toBeGreaterThan(3);
  });
});
