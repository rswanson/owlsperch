import { defineConfig, devices } from "@playwright/test";

const API_PORT = 8000;
const WEB_PORT = 5173;

// Playwright smoke test (batch B7, spec 4.10): flow A -- type a prefix, see
// grouped hits, press Enter, land on the record page. Both servers are
// started fresh for the test run: the FastAPI backend against a freshly
// built fixture database (`e2e/serve-fixture.py`), and the Vite dev server
// (which proxies `/api/*` to it, per `vite.config.ts`).
export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts/,
  timeout: 30_000,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
      testMatch: /(smoke|browse)\.spec\.ts/,
    },
    {
      name: "mobile-400",
      // Acceptance criterion 6 (400px layout, finding 3): a dedicated
      // project running only mobile.spec.ts at a 400x800 viewport.
      use: { ...devices["Desktop Chrome"], viewport: { width: 400, height: 800 } },
      testMatch: /mobile\.spec\.ts/,
    },
  ],
  webServer: [
    {
      command: "uv run python e2e/serve-fixture.py",
      url: `http://127.0.0.1:${API_PORT}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: "npm run dev",
      url: `http://127.0.0.1:${WEB_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
