import { defineConfig, devices } from "@playwright/test";

const PORT = 8798;
const DATA_DIR = "e2e/.data";
/** A second backend on an empty database, for day-zero screens. */
const EMPTY_PORT = 8799;
const EMPTY_DATA_DIR = "e2e/.data-empty";
/** A third fixture backend for specs that change data (start a run, register planned posts), so the
 * read-only specs on PORT keep their stable counts. */
const ACTIONS_PORT = 8797;
const ACTIONS_DATA_DIR = "e2e/.data-actions";
/**
 * Pinned settings so a member's local .env (handle, time zone, backup folder, the real X provider)
 * never leaks in. Runs started from the browser use the fake provider, skip the GitHub regime check
 * and skip backups: nothing here touches the network.
 */
const ENV = (dir: string) =>
  `SOCIALMGR_DATA_DIR=web/${dir} SOCIALMGR_BACKUP_DIR=web/${dir}/backups ` +
  `SOCIALMGR_ACCOUNT__HANDLE=@your_account SOCIALMGR_ACCOUNT__TZ=UTC SOCIALMGR_PLUGINS__LABELER=none ` +
  `SOCIALMGR_PLUGINS__PROVIDERS='["fake_owner"]' SOCIALMGR_RUN__REGIME_CHECK=false SOCIALMGR_RUN__BACKUP=false`;

const fixtureServer = (dir: string, port: number) => ({
  cwd: "..",
  command:
    `rm -rf web/${dir} && ` +
    `${ENV(dir)} uv run socialmgr fixture --until 2026-11-25T06:00:00+00:00 && ` +
    `${ENV(dir)} uv run socialmgr serve --port ${port}`,
  url: `http://127.0.0.1:${port}/api/health`,
  reuseExistingServer: false,
  timeout: 120_000,
});

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
  webServer: [
    fixtureServer(DATA_DIR, PORT),
    fixtureServer(ACTIONS_DATA_DIR, ACTIONS_PORT),
    {
      cwd: "..",
      command:
        `rm -rf web/${EMPTY_DATA_DIR} && ` +
        `${ENV(EMPTY_DATA_DIR)} uv run socialmgr serve --port ${EMPTY_PORT}`,
      url: `http://127.0.0.1:${EMPTY_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
