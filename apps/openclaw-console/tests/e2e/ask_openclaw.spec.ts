/**
 * E2E tests for Ask OpenClaw: /api/ask refuses without citations, returns grounded answers.
 */
import { test, expect } from "@playwright/test";

test.describe("POST /api/ask", () => {
  test("returns 400 when question is missing", async ({ request }) => {
    const res = await request.post("/api/ask", {
      data: {},
    });
    expect(res.status()).toBe(400);
    const data = await res.json();
    expect(data.ok).toBe(false);
    expect(data.error).toContain("question");
  });

  test("returns 422 or 200 with citations (never answers without citations)", async ({ request }) => {
    const res = await request.post("/api/ask", {
      data: { question: "What's broken?" },
    });
    // Either 422 (no citations) or 200 (has citations)
    expect([200, 422, 503]).toContain(res.status());
    const data = await res.json();
    if (res.status() === 200 && data.ok) {
      expect(Array.isArray(data.citations)).toBe(true);
      expect(data.citations.length).toBeGreaterThan(0);
      expect(typeof data.answer).toBe("string");
      expect(data.recommended_next_action).toBeDefined();
      expect(data.recommended_next_action.action).toBeDefined();
    }
    if (res.status() === 422) {
      expect(data.error_class).toBe("NO_CITATIONS");
      expect(data.recommended_next_action?.action).toBeDefined();
    }
  });
});
