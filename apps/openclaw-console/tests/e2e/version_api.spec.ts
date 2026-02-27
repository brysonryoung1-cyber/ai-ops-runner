/**
 * E2E tests for /api/ui/version: correct fields, tree-to-tree drift, drift_reason.
 */
import { test, expect } from "@playwright/test";

test.describe("GET /api/ui/version", () => {
  test("returns required fields: build_sha, deployed_head_sha, deployed_tree_sha, origin_main_*, drift, drift_reason, last_deploy_time", async ({
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

    expect(data).toHaveProperty("drift");
    expect(typeof data.drift).toBe("boolean");

    expect(data).toHaveProperty("drift_reason");
    expect(data.drift_reason === null || typeof data.drift_reason === "string").toBe(true);

    expect(data).toHaveProperty("last_deploy_time");
    expect(data.last_deploy_time === null || typeof data.last_deploy_time === "string").toBe(true);
  });

  test("drift is tree-true: when both trees present, drift compares deployed_tree_sha vs origin_main_tree_sha", async ({
    request,
  }) => {
    const res = await request.get("/api/ui/version");
    expect(res.status()).toBe(200);
    const data = await res.json();

    if (data.deployed_tree_sha && data.origin_main_tree_sha) {
      const expectedDrift = data.deployed_tree_sha !== data.origin_main_tree_sha;
      expect(data.drift).toBe(expectedDrift);
      if (data.drift) {
        expect(data.drift_reason).toContain("tree");
      }
    }
  });
});
