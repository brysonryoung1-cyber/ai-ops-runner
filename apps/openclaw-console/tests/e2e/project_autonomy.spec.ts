import { expect, test, type APIRequestContext } from "@playwright/test";
import { mkdirSync, writeFileSync } from "fs";
import { dirname, join, resolve } from "path";

const ARTIFACTS_ROOT = resolve(process.cwd(), ".tmp-playwright-artifacts");

function writeJson(path: string, payload: unknown) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(payload, null, 2)}\n`, "utf-8");
}

async function setAutonomyMode(request: APIRequestContext, mode: "ON" | "OFF") {
  const res = await request.post("/api/ui/autonomy_mode", {
    data: { mode },
  });
  expect(res.status()).toBe(200);
}

test.describe("Project autonomy surface", () => {
  test("project page keeps the default visible button count at or below five", async ({ page, request }) => {
    await setAutonomyMode(request, "ON");
    await page.goto("/projects/soma_kajabi");

    await expect(page.getByRole("heading", { name: /Soma Kajabi/i })).toBeVisible({
      timeout: 10_000,
    });

    const totalButtons = await page.locator(
      '[data-testid="project-primary-actions"] button:visible, [data-testid="project-playbook-buttons"] button:visible'
    ).count();
    expect(totalButtons).toBeLessThanOrEqual(5);
  });

  test("optional canary warnings stay amber and do not raise the red degraded banner", async ({
    page,
    request,
  }) => {
    writeJson(join(ARTIFACTS_ROOT, "system", "canary", "99991231T235959Z_optional_warn_ui", "result.json"), {
      status: "PASS",
      core_status: "PASS",
      optional_status: "WARN",
      core_failed_checks: [],
      optional_failed_checks: ["ask_unreachable"],
      proof: "artifacts/system/canary/99991231T235959Z_optional_warn_ui",
    });

    await setAutonomyMode(request, "ON");
    await page.goto("/projects/soma_kajabi");

    await expect(page.getByTestId("project-optional-warning")).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByTestId("project-core-degraded-banner")).toHaveCount(0);
  });
});
