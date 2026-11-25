import { defineConfig, devices } from "@playwright/test";

const PORT = 8798;
const DATA_DIR = "e2e/.data";

/**
 * End-to-end smoke tests against the real backend: a fresh fixture database, the built SPA
 * served by `socialmgr serve`. Run `pnpm build` first (CI and `scripts/check.sh` do).
 */
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  outputDir: "e2e/.results",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    cwd: "..",
    command:
      `rm -rf web/${DATA_DIR} && ` +
      `SOCIALMGR_DATA_DIR=web/${DATA_DIR} uv run socialmgr fixture --until 2026-11-25T06:00:00+00:00 && ` +
      `SOCIALMGR_DATA_DIR=web/${DATA_DIR} uv run socialmgr serve --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
