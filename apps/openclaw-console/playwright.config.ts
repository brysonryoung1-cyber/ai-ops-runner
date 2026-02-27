import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.OPENCLAW_CONSOLE_PORT || process.env.PORT || "8787";
const baseURL = `http://127.0.0.1:${PORT}`;
const hostdMock = process.env.OPENCLAW_HOSTD_MOCK === "1";
const uiStub = process.env.OPENCLAW_UI_STUB ?? (hostdMock ? "0" : "1");
const serverEnv = hostdMock
  ? `OPENCLAW_HOSTD_MOCK=1 OPENCLAW_UI_STUB=${uiStub}`
  : `OPENCLAW_UI_STUB=${uiStub}`;

/**
 * Playwright e2e for OpenClaw console.
 * Run with OPENCLAW_UI_STUB=1 so /api/projects/[projectId]/run returns fixtures (no hostd/credentials).
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `${serverEnv} npx next dev --hostname 127.0.0.1 --port ${PORT}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
