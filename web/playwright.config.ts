import { defineConfig } from "@playwright/test";

const port = Number(process.env.MF_E2E_PORT ?? 8799);

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 15_000 },
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "off", viewport: { width: 1440, height: 900 } },
  webServer: {
    command: "bash ../scripts/e2e-server.sh",
    url: `http://127.0.0.1:${port}/api/v1/health`,
    timeout: 120_000,
    reuseExistingServer: !process.env.CI,
  },
});
