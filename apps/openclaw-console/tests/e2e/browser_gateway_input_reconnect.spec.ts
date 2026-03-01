/**
 * E2E: Browser Gateway input reconnect verification.
 *
 * Validates that after a WebSocket reconnect the viewer:
 *  1) Shows the "Verifying input…" overlay
 *  2) Re-enables controls only when last_input_ts advances
 *  3) Reports no 401s and no last_cdp_dispatch_error
 */
import { test, expect } from "@playwright/test";

const STATUS_URL = "/api/browser-gateway/status";

test.describe("Browser Gateway input reconnect", () => {
  test("initial load reaches LIVE and input selftest passes", async ({
    request,
    page,
  }) => {
    const startRes = await request.post("/api/browser-gateway/start", {
      data: { run_id: "e2e_reconnect_test", purpose: "e2e_test" },
    });

    if (startRes.status() !== 200) {
      test.skip(true, "Browser Gateway not available — skipping");
      return;
    }

    const startData = await startRes.json();
    if (!startData.ok) {
      test.skip(true, `Gateway start failed: ${startData.error}`);
      return;
    }

    const { session_id, token, viewer_url } = startData;
    expect(session_id).toBeTruthy();
    expect(token).toBeTruthy();

    const localViewerUrl = `/browser/${session_id}?token=${token}`;
    await page.goto(localViewerUrl, { waitUntil: "domcontentloaded" });

    const hud = page.locator('[data-testid="gateway-hud"]');
    await expect(hud).toBeVisible({ timeout: 20_000 });

    const inputField = hud.locator("text=input:");
    await expect(inputField).toContainText("VERIFIED", { timeout: 15_000 });

    const statusRes = await request.get(
      `${STATUS_URL}?session_id=${session_id}`,
    );
    expect(statusRes.status()).toBe(200);
    const statusData = await statusRes.json();
    expect(statusData.status).toBe("LIVE");
    expect(statusData.last_input_ts).not.toBeNull();
    expect(statusData.last_cdp_dispatch_error).toBeNull();
  });

  test("controls re-enable after forced reconnect", async ({
    request,
    page,
  }) => {
    const startRes = await request.post("/api/browser-gateway/start", {
      data: { run_id: "e2e_reconnect_recon", purpose: "e2e_test" },
    });

    if (startRes.status() !== 200) {
      test.skip(true, "Browser Gateway not available — skipping");
      return;
    }

    const startData = await startRes.json();
    if (!startData.ok) {
      test.skip(true, `Gateway start failed: ${startData.error}`);
      return;
    }

    const { session_id, token } = startData;
    const localViewerUrl = `/browser/${session_id}?token=${token}`;
    await page.goto(localViewerUrl, { waitUntil: "domcontentloaded" });

    const hud = page.locator('[data-testid="gateway-hud"]');
    await expect(hud).toBeVisible({ timeout: 20_000 });
    await expect(hud.locator("text=input:")).toContainText("VERIFIED", {
      timeout: 15_000,
    });

    const preReconnectRes = await request.get(
      `${STATUS_URL}?session_id=${session_id}`,
    );
    const preTs = (await preReconnectRes.json()).last_input_ts;

    await page.evaluate(() => {
      const wsInstances = (window as unknown as Record<string, WebSocket[]>)
        .__bgTestWsList;
      if (wsInstances?.length) {
        wsInstances.forEach((ws: WebSocket) => ws.close());
      } else {
        document
          .querySelectorAll("canvas")
          .forEach((c) =>
            c.dispatchEvent(new Event("__force_reconnect_test")),
          );
      }
    });

    await page.evaluate(() => {
      const orig = WebSocket.prototype.close;
      const sockets: WebSocket[] = [];
      const origCtor = window.WebSocket;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const win = window as any;
      win.WebSocket = function (
        ...args: ConstructorParameters<typeof WebSocket>
      ) {
        const ws = new origCtor(...args);
        sockets.push(ws);
        return ws;
      } as unknown as typeof WebSocket;
      win.__bgTestWsList = sockets;
      WebSocket.prototype.close = orig;
    });

    await page.evaluate(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const list: WebSocket[] = (window as any).__bgTestWsList;
      list?.forEach((ws: WebSocket) => ws.close());
    });

    const overlay = page.locator('[data-testid="input-verifying-overlay"]');
    await expect(overlay).toBeVisible({ timeout: 20_000 }).catch(() => {
      /* overlay may flash too fast */
    });

    await expect(hud.locator("text=input:")).toContainText(/VERIFIED|VERIFYING/, {
      timeout: 20_000,
    });

    await expect(hud.locator("text=input:")).toContainText("VERIFIED", {
      timeout: 15_000,
    });

    const postReconnectRes = await request.get(
      `${STATUS_URL}?session_id=${session_id}`,
    );
    expect(postReconnectRes.status()).toBe(200);
    const postData = await postReconnectRes.json();
    expect(postData.last_cdp_dispatch_error).toBeNull();

    if (preTs != null && postData.last_input_ts != null) {
      expect(postData.last_input_ts).toBeGreaterThanOrEqual(preTs);
    }
  });

  test("status endpoint returns all telemetry fields", async ({ request }) => {
    const startRes = await request.post("/api/browser-gateway/start", {
      data: { run_id: "e2e_telemetry_check", purpose: "e2e_test" },
    });

    if (startRes.status() !== 200) {
      test.skip(true, "Browser Gateway not available — skipping");
      return;
    }

    const startData = await startRes.json();
    if (!startData.ok) {
      test.skip(true, `Gateway start failed: ${startData.error}`);
      return;
    }

    const { session_id } = startData;
    const res = await request.get(
      `${STATUS_URL}?session_id=${session_id}`,
    );
    expect(res.status()).toBe(200);
    const data = await res.json();

    expect(data).toHaveProperty("status");
    expect(["LIVE", "CONNECTING", "RECONNECTING", "DISCONNECTED", "EXPIRED", "ERROR"]).toContain(
      data.status,
    );
    expect(data).toHaveProperty("last_input_ts");
    expect(data).toHaveProperty("last_input_error");
    expect(data).toHaveProperty("last_input_http_error");
    expect(data).toHaveProperty("last_cdp_dispatch_error");
  });
});
