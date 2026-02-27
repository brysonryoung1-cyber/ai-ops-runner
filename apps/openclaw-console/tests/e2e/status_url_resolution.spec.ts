import { test, expect } from "@playwright/test";
import { toCanonicalNovncUrl } from "@/lib/novnc-url";

/**
 * Status API URL resolution tests for Guided Human Gate.
 * Ensures framebuffer_url and artifact_dir_url point to direct paths, not /artifacts root.
 */
test.describe("Status API URL resolution", () => {
  test("GET /api/projects/soma_kajabi/status returns URL fields", async ({ request }) => {
    const res = await request.get("/api/projects/soma_kajabi/status");
    const data = await res.json();

    expect(res.ok()).toBeTruthy();
    expect(data.ok).toBe(true);

    // Response must include URL fields (single source of truth)
    expect(data).toHaveProperty("framebuffer_url");
    expect(data).toHaveProperty("artifact_dir_url");
    expect(data).toHaveProperty("doctor_framebuffer_url");
    expect(data).toHaveProperty("doctor_artifact_dir_url");

    // When WAITING_FOR_HUMAN with artifact_dir: framebuffer_url must end in /framebuffer.png (not /artifacts root)
    if (data.current_status === "WAITING_FOR_HUMAN" && data.framebuffer_url) {
      expect(data.framebuffer_url).toMatch(/\/framebuffer\.png$/);
      expect(data.framebuffer_url).not.toBe("/artifacts");
      expect(data.framebuffer_url).toMatch(/^\/artifacts\//);
    }

    // When WAITING_FOR_HUMAN with artifact_dir: artifact_dir_url must point to run dir (not /artifacts root)
    if (data.current_status === "WAITING_FOR_HUMAN" && data.artifact_dir_url) {
      expect(data.artifact_dir_url).not.toBe("/artifacts");
      expect(data.artifact_dir_url).toMatch(/^\/artifacts\//);
    }

    // When NOT waiting: URLs should be null
    if (data.current_status !== "WAITING_FOR_HUMAN") {
      expect(data.framebuffer_url).toBeNull();
      expect(data.artifact_dir_url).toBeNull();
    }

    // Doctor fallback URLs when present must also be valid paths
    if (data.doctor_framebuffer_url) {
      expect(data.doctor_framebuffer_url).toMatch(/\/framebuffer\.png$/);
      expect(data.doctor_framebuffer_url).toMatch(/^\/artifacts\//);
    }
    if (data.doctor_artifact_dir_url) {
      expect(data.doctor_artifact_dir_url).toMatch(/^\/artifacts\//);
    }

    // noVNC URL canonicalization: status returns novnc_url_canonical (primary) and novnc_url_legacy when present
    expect(data).toHaveProperty("novnc_url_canonical");
    if (data.novnc_url) {
      // Canonical must be https, /novnc, path=/websockify
      expect(data.novnc_url).toMatch(/^https:\/\//);
      expect(data.novnc_url).toContain("/novnc/vnc.html");
      expect(data.novnc_url).toContain("path=/websockify");
    }
  });

  test("novnc_url canonicalization: legacy http://:6080 => canonical https with path=/websockify", () => {
    const legacy = "http://aiops-1.tailc75c62.ts.net:6080/vnc.html?autoconnect=1";
    const canonical = toCanonicalNovncUrl(legacy);
    expect(canonical).toBeTruthy();
    expect(canonical).toMatch(/^https:\/\//);
    expect(canonical).toContain("/novnc/vnc.html");
    expect(canonical).toContain("path=/websockify");
    expect(canonical).not.toContain(":6080");
  });
});
