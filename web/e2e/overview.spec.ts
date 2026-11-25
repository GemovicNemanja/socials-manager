import { expect, test, type APIRequestContext } from "@playwright/test";

// Forecast-derived numbers change with the champion model, so expected values are read from
// /api/overview and the page is checked against them. Provider-only counts are stable.
const TOTAL = 129;
const EMPTY_BACKEND = "http://127.0.0.1:8799";

const int = (n: number) => Math.round(n).toLocaleString("en-US");
const percentile = (p: number) => `p${Math.min(99, Math.max(1, Math.round(p * 100)))}`;

async function overview(request: APIRequestContext, base = "") {
  const response = await request.get(`${base}/api/overview`);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test.describe("Overview screen against the real backend", () => {
  test("top bar and KPI tiles show the API's numbers", async ({ page, request }) => {
    const ov = await overview(request);
    await page.goto("/#/overview");
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
    await expect(page.getByText(`Day ${ov.days_tracked} since launch · ${TOTAL} posts tracked`)).toBeVisible();

    const tiles = page.getByRole("region", { name: "Expectations" });
    const followers = ov.tiles.followers;
    await expect(tiles.getByText("Followers", { exact: true })).toBeVisible();
    await expect(tiles.getByText(int(followers.now), { exact: true })).toBeVisible();
    await expect(tiles.getByText(`+${int(followers.change_7d)} in 7d`)).toBeVisible();

    const impressions = ov.tiles.impressions_7d;
    await expect(tiles.getByText("Impressions, 7d")).toBeVisible();
    await expect(tiles.getByText(percentile(impressions.expected.percentile), { exact: true }).first()).toBeVisible();

    const median = ov.tiles.median_i48;
    await expect(tiles.getByText(int(median.value), { exact: true })).toBeVisible();
    await expect(tiles.getByText(`last ${median.n_posts} matured`)).toBeVisible();

    await expect(tiles.getByText("Profile visits, 7d")).toBeVisible();
    await expect(tiles.getByText(int(ov.tiles.profile_visits_7d.value), { exact: true })).toBeVisible();
    await expect(tiles.getByText(ov.tiles.follows_per_1k_7d.value.toFixed(2), { exact: true })).toBeVisible();
    await expect(tiles.getByText("Posts, 7d")).toBeVisible();
  });

  test("status counts, calibration and the follower chart placeholder", async ({ page, request }) => {
    const ov = await overview(request);
    await page.goto("/#/overview");
    const status = page.getByRole("region", { name: "Posts vs forecast" });
    const c = ov.status_28d;
    for (const [name, value] of [
      ["Above expected", c.above],
      ["As expected", c.as_expected],
      ["Below expected", c.below],
    ] as const) {
      const row = status.locator("div", { hasText: name }).last();
      await expect(row).toContainText(String(value));
    }
    await expect(status.getByText(`${c.above + c.as_expected + c.below} matured`, { exact: false })).toBeVisible();

    const calibration = page.getByRole("region", { name: "Calibration" });
    await expect(calibration.getByRole("meter", { name: "80% range hit rate" })).toHaveAttribute(
      "aria-valuenow",
      String(Math.round(ov.calibration.in_80_rate * 100)),
    );
    await expect(calibration.getByText(/Drift monitors .* arrive in/)).toBeVisible();

    const chart = page.getByRole("region", { name: "Followers" });
    await expect(chart.getByRole("img", { name: /Followers per run/ })).toBeVisible();
    await expect(chart.getByText(`Today ${int(ov.tiles.followers.now)}`)).toBeVisible();
    await expect(chart.getByText(/Follower forecast band arrives in/)).toBeVisible();
  });

  test("recent posts list the newest posts and open the drawer", async ({ page, request }) => {
    const ov = await overview(request);
    await page.goto("/#/overview");
    const recent = page.getByRole("region", { name: "Recent posts" });
    const rows = recent.getByRole("rowgroup").getByRole("row");
    await expect(rows).toHaveCount(ov.recent_posts.length);
    await expect(rows.first()).toContainText(ov.recent_posts[0].text);

    await rows.nth(1).click();
    await expect(page).toHaveURL(new RegExp(`#/posts/${ov.recent_posts[1].id.replace("post:", "")}$`));
    await expect(page.getByRole("dialog", { name: "Post detail" })).toContainText(ov.recent_posts[1].text);
  });

  test("last run panel and navigation", async ({ page, request }) => {
    const ov = await overview(request);
    await page.goto("/#/overview");
    const run = page.getByRole("region", { name: "Last run" });
    await expect(run.getByText(`Run ${ov.last_run.id.replace("run:", "")} finished`, { exact: false })).toBeVisible();
    await expect(run.getByRole("meter", { name: /X API/ })).toBeVisible();
    await expect(run.getByText(/Briefings arrive in/)).toBeVisible();

    await page.getByRole("link", { name: "Open posts" }).click();
    await expect(page.getByRole("heading", { name: "Posts" })).toBeVisible();
    await page.getByRole("navigation", { name: "Main" }).getByText("Overview", { exact: true }).click();
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  });

  test("full-page screenshot for visual review", async ({ page }) => {
    await page.goto("/#/overview");
    await expect(page.getByRole("region", { name: "Recent posts" }).getByRole("row").nth(5)).toBeVisible();
    await page.screenshot({ path: "e2e/.results/overview.png" });
  });
});

test.describe("Overview day zero (empty database)", () => {
  test("shows the getting-started checklist and empty tiles", async ({ page, request }) => {
    const ov = await overview(request, EMPTY_BACKEND);
    expect(ov.total_posts).toBe(0);
    await page.goto(`${EMPTY_BACKEND}/#/overview`);
    await expect(page.getByText("Day 0 · no posts yet")).toBeVisible();
    const checklist = page.getByRole("region", { name: "Getting started" });
    await expect(checklist.getByText("0 of 4 done")).toBeVisible();
    await expect(checklist.getByText("Run for the first time")).toBeVisible();
    await expect(page.getByRole("region", { name: "What to expect" }).getByText("Prior only")).toBeVisible();
    const tiles = page.getByRole("region", { name: "Expectations" });
    await expect(tiles.getByText("first run sets the baseline")).toBeVisible();
    await expect(page.getByRole("region", { name: "Recent posts" })).toHaveCount(0);
    await page.screenshot({ path: "e2e/.results/overview-day-zero.png" });
  });
});
