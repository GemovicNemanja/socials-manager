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

    // Past results read in words against their expected range (MODEL §4.1: p ≥ 0.8 above, ≤ 0.2 below).
    const outcome = (p: number) => (p >= 0.8 ? "Above expected" : p <= 0.2 ? "Below expected" : "As expected");
    for (const [label, kpi] of [
      ["Impressions, 7d", impressions],
      ["Median 48h impressions", median],
    ] as const) {
      const tile = tiles.locator("div", { has: page.getByText(label, { exact: true }) }).last();
      await expect(tile.getByText(outcome(kpi.expected.percentile), { exact: true })).toBeVisible();
      await expect(tile.getByText(/^expected .+ \(80%\)$/)).toBeVisible();
    }
    // Profile visits and follows per 1k have no forecast; followers get one once a 7-day follower
    // forecast has been scored.
    const followersExpected = (await (await request.get("/api/followers")).json()).tiles.followers.expected;
    await expect(tiles.getByText(/^no forecast yet/)).toHaveCount(followersExpected ? 2 : 3);
  });

  test("status counts, model health and the follower chart", async ({ page, request }) => {
    const ov = await overview(request);
    const model = await (await request.get("/api/model")).json();
    const goals = await (await request.get("/api/goals")).json();
    const followers = await (await request.get("/api/followers")).json();
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
    const drift = calibration.getByRole("meter", { name: "Location drift (CUSUM)" });
    await expect(drift).toBeVisible();
    if (model.drift.stat != null && model.drift.threshold) {
      await expect(drift).toHaveAttribute("aria-valuenow", String(Math.round(Math.min(1, model.drift.stat / model.drift.threshold) * 100)));
    }
    await expect(calibration.getByText(model.stage === "prior_only" ? "Prior only" : /Calibrated|Miscalibrated|Not tested yet/)).toBeVisible();
    if (goals.current.length === 0) await expect(calibration.getByText("no goal set", { exact: true })).toBeVisible();
    else await expect(calibration.getByRole("meter", { name: /^Goal: / })).toBeVisible();

    const chart = page.getByRole("region", { name: "Followers vs forecast" });
    await expect(chart.getByRole("img", { name: /Followers per run/ })).toBeVisible();
    await expect(chart.getByText(`Today ${int(ov.tiles.followers.now)}`)).toBeVisible();
    if (followers.forecast == null) await expect(chart.getByText(/No follower forecast frozen yet/)).toBeVisible();
    else await expect(chart.getByRole("img", { name: /forecast median/ })).toBeVisible();
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

  test("briefing panel shows the latest run's briefing with record ids", async ({ page, request }) => {
    const briefing = await (await request.get("/api/briefing")).json();
    const b = briefing.briefing;
    await page.goto("/#/overview");
    const panel = page.getByRole("region", { name: "Last briefing" });
    await expect(panel.getByText(`run ${briefing.run_id} ·`, { exact: false })).toBeVisible();
    await expect(panel.getByText("Too early", { exact: true })).toBeVisible();
    const scored = b.scored.above + b.scored.as_expected + b.scored.below;
    const happened = panel.getByRole("region", { name: "What happened" });
    await expect(happened.getByText(`${b.new_posts} new posts; ${scored} reached 48h`, { exact: false })).toBeVisible();
    expect(b.notable.length).toBeGreaterThan(0);
    for (const n of b.notable.slice(0, 5)) {
      await expect(happened.getByRole("link", { name: n.post, exact: true })).toBeVisible();
    }
    if (b.near_duplicates.length) {
      await expect(happened.getByText(`${b.near_duplicates.length} new posts look like near-duplicates`)).toBeVisible();
    }
    await expect(panel.getByRole("meter", { name: /X API/ })).toBeVisible();
    await expect(panel.getByText(`This run cost $${b.spend.run_usd.toFixed(3)}.`)).toBeVisible();
    await expect(panel.getByText(/Briefings arrive in/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Start run", exact: true })).toBeEnabled();

    const first = b.notable[0];
    await happened.getByRole("link", { name: first.post, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`#/posts/${first.post.replace("post:", "")}$`));
    await expect(page.getByRole("dialog", { name: "Post detail" })).toBeVisible();
  });

  test("navigation from the recent posts panel", async ({ page }) => {
    await page.goto("/#/overview");
    await page.getByRole("link", { name: "Open posts" }).click();
    await expect(page.getByRole("heading", { name: "Posts" })).toBeVisible();
    await page.getByRole("navigation", { name: "Main" }).getByText("Overview", { exact: true }).click();
    await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  });

  test("full-page screenshot for visual review", async ({ page }) => {
    await page.goto("/#/overview");
    await expect(page.getByRole("region", { name: "Recent posts" }).getByRole("row").nth(5)).toBeVisible();
    // Wait for the model-health and briefing panels, which load from their own routes.
    await expect(page.getByRole("region", { name: "Calibration" }).getByText(/^Calibration p/)).toBeVisible();
    await expect(page.getByRole("region", { name: "Last briefing" }).getByText(/^run \d+ ·/)).toBeVisible();
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
    await expect(checklist.getByRole("button", { name: "Start run" })).toBeEnabled();
    await expect(checklist.getByRole("button", { name: "Set goal" })).toBeEnabled();
    await page.screenshot({ path: "e2e/.results/overview-day-zero.png" });
  });

  test("the checklist's Register post opens the dialog; a draft range waits for the first run", async ({ page }) => {
    // Opens the dialog only: registering or running here would change the empty database.
    await page.goto(`${EMPTY_BACKEND}/#/overview`);
    await page.getByRole("region", { name: "Getting started" }).getByRole("button", { name: "Register post" }).click();
    const dialog = page.getByRole("dialog", { name: "Register a planned post" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/A draft range needs one finished run/)).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toHaveCount(0);
  });
});
