import { defineConfig } from "@playwright/test";

/**
 * Browser tests. They need the whole stack running: PostgreSQL with the demo data, the API, the worker and the web app.
 * `scripts/e2e-local.sh` starts the first three; start the web app with API_URL pointing at the API (see web/README.md).
 * The tests change the demo data, so run them against a fresh database.
 */
export default defineConfig({
  testDir: "e2e",
  testMatch: "**/*.e2e.ts",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["github"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
