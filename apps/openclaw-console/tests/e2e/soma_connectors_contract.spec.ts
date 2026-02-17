import { test, expect } from "@playwright/test";
import { existsSync, readFileSync } from "fs";
import { join } from "path";

const usesMockHostd = process.env.OPENCLAW_HOSTD_MOCK === "1";

function resolveRunPath(runId: string): string {
  const candidates = [
    join(process.cwd(), "artifacts", "runs", runId, "run.json"),
    join(process.cwd(), "..", "..", "artifacts", "runs", runId, "run.json"),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  return candidates[0];
}

test.describe("Soma connectors contract", () => {
  test.skip(!usesMockHostd, "Requires OPENCLAW_HOSTD_MOCK=1");

  test("POST /api/projects/soma_kajabi/run writes run record", async ({ request }) => {
    const res = await request.post("/api/projects/soma_kajabi/run", {
      data: { action: "soma_kajabi_bootstrap_start" },
    });
    const data = await res.json();

    expect([200, 502].includes(res.status())).toBeTruthy();
    expect(typeof data.ok === "boolean").toBeTruthy();
    expect(data.run_id).toBeTruthy();
    expect(String(data.message ?? "")).not.toContain("not available via Host Executor");

    const runPath = resolveRunPath(String(data.run_id));
    expect(existsSync(runPath)).toBeTruthy();
    const run = JSON.parse(readFileSync(runPath, "utf-8"));
    expect(run.project_id).toBe("soma_kajabi");
  });

  test("Disallowed action is rejected", async ({ request }) => {
    const res = await request.post("/api/projects/soma_kajabi/run", {
      data: { action: "doctor" },
    });
    const data = await res.json();
    expect(res.status()).toBe(403);
    expect(data.error_class).toBe("ACTION_NOT_ALLOWED");
  });
});
