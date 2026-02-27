/**
 * E2E tests for /api/ui/version: correct fields, tree-to-tree drift, drift_reason, fail-closed.
 */
import { test, expect } from "@playwright/test";

test.describe("GET /api/ui/version", () => {
  test("returns required fields: build_sha, deployed_*, origin_main_*, drift_status, drift, drift_reason, last_deploy_time", async ({
    request,
  }) => {
    const res = await request.get("/api/ui/version");
    expect(res.status()).toBe(200);
    const data = await res.json();

    expect(data).toHaveProperty("build_sha");
    expect(typeof data.build_sha).toBe("string");

    expect(data).toHaveProperty("deployed_head_sha");
    expect(data.deployed_head_sha === null || typeof data.deployed_head_sha === "string").toBe(true);

    expect(data).toHaveProperty("deployed_tree_sha");
    expect(data.deployed_tree_sha === null || typeof data.deployed_tree_sha === "string").toBe(true);

    expect(data).toHaveProperty("origin_main_head_sha");
    expect(data.origin_main_head_sha === null || typeof data.origin_main_head_sha === "string").toBe(true);

    expect(data).toHaveProperty("origin_main_tree_sha");
    expect(data.origin_main_tree_sha === null || typeof data.origin_main_tree_sha === "string").toBe(true);

    expect(data).toHaveProperty("drift_status");
    expect(["ok", "unknown"]).toContain(data.drift_status);

    expect(data).toHaveProperty("drift");
    expect(data.drift === null || typeof data.drift === "boolean").toBe(true);

    expect(data).toHaveProperty("drift_reason");
    expect(data.drift_reason === null || typeof data.drift_reason === "string").toBe(true);

    expect(data).toHaveProperty("last_deploy_time");
    expect(data.last_deploy_time === null || typeof data.last_deploy_time === "string").toBe(true);
  });

  test("when origin_main_tree_sha missing, drift_status is unknown and drift is null (fail-closed)", async ({
    request,
  }) => {
    const res = await request.get("/api/ui/version");
    expect(res.status()).toBe(200);
    const data = await res.json();

    if (!data.origin_main_tree_sha && !data.origin_main_head_sha) {
      expect(data.drift_status).toBe("unknown");
      expect(data.drift).toBeNull();
      expect(data.drift_reason).toBeTruthy();
    }
  });

  test("when both trees present and drift_status=ok, drift compares deployed_tree_sha vs origin_main_tree_sha", async ({
    request,
  }) => {
    const res = await request.get("/api/ui/version");
    expect(res.status()).toBe(200);
    const data = await res.json();

    if (data.deployed_tree_sha && data.origin_main_tree_sha && data.drift_status === "ok") {
      const expectedDrift = data.deployed_tree_sha !== data.origin_main_tree_sha;
      expect(data.drift).toBe(expectedDrift);
      if (data.drift) {
        expect(data.drift_reason).toContain("tree");
      }
    }
  });

  test("when drift_status=ok and trees match, drift is false", async ({ request }) => {
    const res = await request.get("/api/ui/version");
    expect(res.status()).toBe(200);
    const data = await res.json();

    if (
      data.deployed_tree_sha &&
      data.origin_main_tree_sha &&
      data.deployed_tree_sha === data.origin_main_tree_sha &&
      data.drift_status === "ok"
    ) {
      expect(data.drift).toBe(false);
    }
  });
});
