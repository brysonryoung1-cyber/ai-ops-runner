import { mkdirSync, readFileSync, writeFileSync } from "fs";
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
  test("renders Operator Inbox, Pending Approvals, and Run Next", async ({ page, request }) => {
    await setAutonomyMode(request, "ON");
    await page.goto("/inbox");

    await expect(page.getByRole("heading", { name: /Operator Inbox/i })).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByRole("heading", { name: "Pending Approvals" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Run Next" })).toBeVisible();
    await expect(page.getByTestId("operator-inbox-page")).toBeVisible();
  });

  test("root URL redirects to /inbox (landing page)", async ({ page }) => {
    await page.goto("/");
    await page.waitForURL(/\/inbox/, { timeout: 5_000 });
    expect(page.url()).toContain("/inbox");
  });

  test("renders approvals list and approval buttons call APIs with mocks", async ({ page }) => {
    let autonomyMode: "ON" | "OFF" = "OFF";
    let approveCalls = 0;
    let rejectCalls = 0;

    const mockSummary = {
      ok: true,
      autonomy_mode: {
        mode: autonomyMode,
        updated_at: "2026-03-07T10:15:00.000Z",
        updated_by: "integrator",
      },
      canary_core_status: "PASS",
      canary_optional_status: "WARN",
      scheduler_tick: {
        run_id: "tick_20260307T103000Z",
        started_at: "2026-03-07T10:30:00.000Z",
        finished_at: "2026-03-07T10:30:04.000Z",
        observe_only: true,
        decisions_written: 6,
        executed_written: 2,
        mutating_candidates_blocked: 3,
        tick_summary_url: "/artifacts/system/autonomy_scheduler/tick_20260307T103000Z/tick_summary.json",
        proof_url: "/artifacts/system/autonomy_scheduler/tick_20260307T103000Z",
      },
      projects: [
        {
          project_id: "soma_kajabi",
          name: "Soma Kajabi",
          description: "Human-gated funnel operations.",
          autonomy_mode: autonomyMode,
          core_status: "PASS",
          optional_status: "WARN",
          needs_human: true,
          approvals_pending: 2,
          proof_links: [
            { label: "Last run proof", href: "/artifacts/system/playbook_runs/playbook_1" },
          ],
          cards: [
            {
              id: "soma-human-gate",
              type: "HUMAN_ONLY",
              title: "Human gate open",
              summary: "Kajabi session needs operator reauth.",
              project_id: "soma_kajabi",
              proof_links: [],
              tone: "warn",
            },
          ],
          recommended_playbook: {
            id: "soma.review_approvals",
            title: "Review Approvals",
            rationale: "Resolve queued approvals first.",
            expected_outputs: ["approval_resolution.json"],
          },
          human_gate: {
            run_id: "run_1",
            novnc_url: "https://novnc.example.test",
            browser_url: "https://browser.example.test",
            instruction: "Log in and resume the Soma lane.",
          },
          last_run: {
            run_id: "run_1",
            action: "soma_run_to_done",
            status: "WAITING_FOR_HUMAN",
            finished_at: "2026-03-07T10:16:00.000Z",
          },
        },
      ],
    };

    const mockApprovals = {
      ok: true,
      approvals: [
        {
          id: "approval_1",
          project_id: "soma_kajabi",
          playbook_id: "soma.resume_publish",
          playbook_title: "Resume Kajabi publish",
          primary_action: "soma_run_to_done",
          status: "PENDING",
          rationale: "Operator approval required.",
          created_at: "2026-03-07T10:19:01.000Z",
          created_by: "operator:local",
          resolved_at: null,
          resolved_by: null,
          note: null,
          proof_bundle: "artifacts/system/playbook_runs/playbook_20260307T101900Z",
          proof_bundle_url: "/artifacts/system/playbook_runs/playbook_20260307T101900Z",
          request_path: "artifacts/system/approvals/approval_1/request.json",
          request_url: "/artifacts/system/approvals/approval_1/request.json",
          resolution_path: null,
          resolution_url: null,
          policy_decision: "APPROVAL",
          autonomy_mode: "OFF",
          run_id: null,
        },
        {
          id: "approval_2",
          project_id: "soma_kajabi",
          playbook_id: "soma.fix_business_dod",
          playbook_title: "Fix Business DoD",
          primary_action: "soma_business_dod_fixer",
          status: "PENDING",
          rationale: "Business acceptance fix needs an operator signoff.",
          created_at: "2026-03-07T10:22:01.000Z",
          created_by: "operator:local",
          resolved_at: null,
          resolved_by: null,
          note: null,
          proof_bundle: "artifacts/system/playbook_runs/playbook_20260307T102200Z",
          proof_bundle_url: "/artifacts/system/playbook_runs/playbook_20260307T102200Z",
          request_path: "artifacts/system/approvals/approval_2/request.json",
          request_url: "/artifacts/system/approvals/approval_2/request.json",
          resolution_path: null,
          resolution_url: null,
          policy_decision: "APPROVAL",
          autonomy_mode: "OFF",
          run_id: null,
        },
      ],
    };

    await page.route("**/api/ui/inbox_summary", async (route) => {
      await route.fulfill({ json: mockSummary });
    });
    await page.route("**/api/ui/autonomy_mode", async (route) => {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON() as { mode?: "ON" | "OFF" };
        autonomyMode = body.mode === "OFF" ? "OFF" : "ON";
        await route.fulfill({
          json: {
            ok: true,
            mode: autonomyMode,
            updated_at: "2026-03-07T10:15:00.000Z",
            updated_by: "operator:local",
            path: "artifacts/system/autonomy_mode.json",
          },
        });
        return;
      }
      await route.fulfill({
        json: {
          ok: true,
          mode: autonomyMode,
          updated_at: "2026-03-07T10:15:00.000Z",
          updated_by: "integrator",
          path: "artifacts/system/autonomy_mode.json",
        },
      });
    });
    await page.route("**/api/ui/approvals?status=PENDING", async (route) => {
      await route.fulfill({ json: mockApprovals });
    });
    await page.route("**/api/ui/approvals/approval_1/approve", async (route) => {
      approveCalls += 1;
      await route.fulfill({
        json: {
          ok: true,
          approval: { id: "approval_1", status: "APPROVED" },
          run: { ok: true, status: "RUNNING", playbook_run_id: "playbook_20260307T101900Z" },
        },
      });
    });
    await page.route("**/api/ui/approvals/approval_2/reject", async (route) => {
      rejectCalls += 1;
      await route.fulfill({
        json: {
          ok: true,
          approval: { id: "approval_2", status: "REJECTED" },
        },
      });
    });

    await page.goto("/inbox");

    await expect(page.getByRole("heading", { name: "Pending Approvals" })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("approvals-list")).toContainText("Resume Kajabi publish");
    await expect(page.getByTestId("approvals-list")).toContainText("Fix Business DoD");

    await page.getByTestId("approvals-list").getByRole("button", { name: "Approve" }).first().click();
    await page.getByTestId("approvals-list").getByRole("button", { name: "Reject" }).nth(1).click();

    expect(approveCalls).toBe(1);
    expect(rejectCalls).toBe(1);

    const proofDir = join(ARTIFACTS_ROOT, "ui-proof");
    mkdirSync(proofDir, { recursive: true });
    writeFileSync(join(proofDir, "inbox-approvals-mock.html"), await page.content(), "utf-8");
    await page.screenshot({ path: join(proofDir, "inbox-approvals-mock.png"), fullPage: true });
  });
});
