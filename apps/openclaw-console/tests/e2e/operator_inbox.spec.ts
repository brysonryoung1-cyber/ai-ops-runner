import { readFileSync } from "fs";
import { join, resolve } from "path";
import { test, expect, type APIRequestContext } from "@playwright/test";

const ARTIFACTS_ROOT = resolve(process.cwd(), ".tmp-playwright-artifacts");

async function setAutonomyMode(request: APIRequestContext, mode: "ON" | "OFF") {
  const res = await request.post("/api/ui/autonomy_mode", {
    data: { mode },
  });
  expect(res.status()).toBe(200);
  return res.json();
}

test.describe("GET /api/ui/inbox_summary", () => {
  test("returns required top-level keys and per-project run-next fields", async ({ request }) => {
    await setAutonomyMode(request, "ON");

    const res = await request.get("/api/ui/inbox_summary");
    expect(res.status()).toBe(200);
    const data = await res.json();

    expect(data.ok).toBe(true);
    expect(data).toHaveProperty("autonomy_mode");
    expect(data.autonomy_mode).toHaveProperty("mode");
    expect(data).toHaveProperty("canary_core_status");
    expect(data).toHaveProperty("canary_optional_status");
    expect(Array.isArray(data.projects)).toBe(true);
    expect(data.projects.length).toBeGreaterThan(0);

    for (const project of data.projects) {
      expect(typeof project.project_id).toBe("string");
      expect(typeof project.name).toBe("string");
      expect(typeof project.description).toBe("string");
      expect(["ON", "OFF"]).toContain(project.autonomy_mode);
      expect(project).toHaveProperty("core_status");
      expect(project).toHaveProperty("optional_status");
      expect(project).toHaveProperty("needs_human");
      expect(project).toHaveProperty("approvals_pending");
      expect(Array.isArray(project.cards)).toBe(true);
      expect(Array.isArray(project.playbooks)).toBe(true);
      expect(Array.isArray(project.proof_links)).toBe(true);
      if (project.recommended_playbook) {
        expect(typeof project.recommended_playbook.id).toBe("string");
        expect(typeof project.recommended_playbook.title).toBe("string");
        expect(typeof project.recommended_playbook.rationale).toBe("string");
        expect(Array.isArray(project.recommended_playbook.expected_outputs)).toBe(true);
      }
    }
  });

  test("autonomy mode persists via GET/POST /api/ui/autonomy_mode", async ({ request }) => {
    const off = await setAutonomyMode(request, "OFF");
    expect(off.mode).toBe("OFF");

    const offReadRes = await request.get("/api/ui/autonomy_mode");
    expect(offReadRes.status()).toBe(200);
    const offRead = await offReadRes.json();
    expect(offRead.mode).toBe("OFF");
    expect(typeof offRead.path).toBe("string");

    const on = await setAutonomyMode(request, "ON");
    expect(on.mode).toBe("ON");

    const onReadRes = await request.get("/api/ui/autonomy_mode");
    expect(onReadRes.status()).toBe(200);
    const onRead = await onReadRes.json();
    expect(onRead.mode).toBe("ON");
  });

  test("approval-gated playbook creates one approval and approval execution writes proof bundle", async ({
    request,
  }) => {
    await setAutonomyMode(request, "ON");

    const runRes = await request.post("/api/ui/playbooks/run", {
      data: {
        project_id: "pred_markets",
        playbook_id: "pred.run_to_done",
        user_role: "admin",
      },
    });
    expect(runRes.status()).toBe(202);
    const runData = await runRes.json();

    expect(runData.status).toBe("APPROVAL_REQUIRED");
    expect(typeof runData.approval_id).toBe("string");
    expect(typeof runData.playbook_run_id).toBe("string");

    const approvalsRes = await request.get("/api/ui/approvals?project_id=pred_markets&status=PENDING");
    expect(approvalsRes.status()).toBe(200);
    const approvalsData = await approvalsRes.json();
    expect(Array.isArray(approvalsData.approvals)).toBe(true);
    expect(
      approvalsData.approvals.some((approval: { id: string }) => approval.id === runData.approval_id)
    ).toBe(true);

    const approveRes = await request.post(`/api/ui/approvals/${runData.approval_id}/approve`, {
      data: { user_role: "admin" },
    });
    expect(approveRes.status()).toBe(200);
    const approveData = await approveRes.json();
    expect(approveData.approval.status).toBe("APPROVED");
    expect(approveData.run.playbook_run_id).toBe(runData.playbook_run_id);

    const resultJson = JSON.parse(
      readFileSync(
        join(ARTIFACTS_ROOT, "system", "playbook_runs", runData.playbook_run_id, "RESULT.json"),
        "utf-8"
      )
    );
    expect(resultJson.playbook_run_id).toBe(runData.playbook_run_id);
    expect(["RUNNING", "SUCCESS", "JOINED_EXISTING_RUN"]).toContain(resultJson.status);

    const resolutionJson = JSON.parse(
      readFileSync(
        join(
          ARTIFACTS_ROOT,
          "system",
          "playbook_runs",
          runData.playbook_run_id,
          "approval_resolution.json"
        ),
        "utf-8"
      )
    );
    expect(resolutionJson.approval_id).toBe(runData.approval_id);
    expect(resolutionJson.status).toBe("APPROVED");
  });
});

test.describe("/inbox page", () => {
  test("renders Operator Inbox, Action Queue, and Run Next", async ({ page, request }) => {
    await setAutonomyMode(request, "ON");
    await page.goto("/inbox");

    await expect(page.getByRole("heading", { name: /Operator Inbox/i })).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByRole("heading", { name: "Action Queue" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Run Next" })).toBeVisible();
    await expect(page.getByTestId("operator-inbox-page")).toBeVisible();
  });

  test("root URL redirects to /inbox (landing page)", async ({ page }) => {
    await page.goto("/");
    await page.waitForURL(/\/inbox/, { timeout: 5_000 });
    expect(page.url()).toContain("/inbox");
  });
});
