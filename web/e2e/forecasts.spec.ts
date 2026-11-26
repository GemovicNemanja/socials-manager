import { expect, test, type APIRequestContext } from "@playwright/test";

// Model health depends on the fixture's seed and the monitors, so every number checked here is read
// from /api/model first. The fixture has scores and monitor states but no promotion review, goal
// status or follower forecast yet: those panels are checked in their empty state, and their
// populated elements only when the API returns them.
const ACTIONS = "http://127.0.0.1:8797";

const pct = (v: number) => `${Math.round(v * 100)}%`;

async function json(request: APIRequestContext, path: string) {
  const response = await request.get(path);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test.describe("Forecasts screen", () => {
  test("header tiles read the API's hit rates, skill, calibration, drift and goal", async ({ page, request }) => {
    const m = await json(request, "/api/model");
    const goals = await json(request, "/api/goals");
    await page.goto("/#/forecasts");
    await expect(page.getByRole("heading", { name: "Forecasts" })).toBeVisible();
    await expect(page.getByText(`${m.owner_n} matured posts`, { exact: false }).first()).toBeVisible();
    if (m.stage === "prior_only") await expect(page.locator("header").getByText("Prior only")).toBeVisible();

    const tiles = page.getByRole("region", { name: "Model health" });
    for (const [label, t] of [
      ["80% interval hit rate", m.hit_80],
      ["50% interval hit rate", m.hit_50],
    ] as const) {
      const tile = tiles.locator("div", { has: page.getByText(label, { exact: true }) }).last();
      await expect(tile.getByText(pct(t.value), { exact: true })).toBeVisible();
      await expect(tile.getByText(`${t.n} matured posts`)).toBeVisible();
      await expect(tile.getByText(t.in_band ? "in band" : "outside", { exact: true })).toBeVisible();
      await expect(tile.getByText(`target band ${Math.round(t.band_lo * 100)}–${Math.round(t.band_hi * 100)}%`)).toBeVisible();
    }
    const skill = tiles.locator("div", { has: page.getByText("CRPS skill vs baseline", { exact: true }) }).last();
    await expect(skill.getByText(m.skill.value == null ? "computed at the first review" : /^90% CI /)).toBeVisible();
    const cal = tiles.locator("div", { has: page.getByText("Calibration test", { exact: true }) }).last();
    await expect(cal.getByText(m.calibration.passed ? "pass" : "fail", { exact: true })).toBeVisible();
    await expect(cal.getByText("gate p > 0.01")).toBeVisible();
    const drift = tiles.locator("div", { has: page.getByText("Location drift", { exact: true }) }).last();
    await expect(drift.getByText(m.drift.state, { exact: true })).toBeVisible();
    if (m.drift.stat != null) await expect(drift.getByText(m.drift.stat.toFixed(1), { exact: true })).toBeVisible();

    if (goals.current.length === 0) {
      await expect(tiles.getByText("no goal set")).toBeVisible();
      await expect(tiles.getByText("set one on Account")).toBeVisible();
    } else {
      await expect(tiles.getByText(/^Goal: /)).toBeVisible();
    }
  });

  test("PIT histogram, coverage, model card and release gates", async ({ page, request }) => {
    const m = await json(request, "/api/model");
    await page.goto("/#/forecasts");

    const calibration = page.getByRole("region", { name: "Calibration" });
    if (m.pit.n > 0) {
      await expect(calibration.getByRole("img", { name: `PIT histogram, posts per decile: ${m.pit.counts.join(", ")}` })).toBeVisible();
      await expect(calibration.getByText(`last ${m.pit.n} originals`, { exact: false })).toBeVisible();
    }
    for (const c of m.coverage) {
      await expect(calibration.getByRole("meter", { name: `${Math.round(c.level * 100)}% intervals containing outcome` })).toHaveAttribute(
        "aria-valuenow",
        String(Math.round((c.value ?? 0) * 100)),
      );
    }
    if (m.next_check_in_posts != null) {
      await expect(calibration.getByText(`after ${m.next_check_in_posts} more posts`)).toBeVisible();
    }

    const card = page.getByRole("region", { name: "Model card" });
    await expect(card.getByText(m.card.version ?? m.card.champion, { exact: true })).toBeVisible();
    await expect(card.getByText(m.card.outcome_model, { exact: true })).toBeVisible();
    await expect(card.getByText(m.card.challenger, { exact: true })).toBeVisible();
    await expect(card.getByText(m.card.last_promotion ? /^Promoted / : "none yet")).toBeVisible();

    const gates = page.getByRole("region", { name: "Release gates" });
    if (m.last_review == null) {
      await expect(gates.getByText(`first review at ${m.owner_terms_at} matured posts`)).toBeVisible();
      await expect(gates.getByText(/No promotion review yet/)).toBeVisible();
    } else {
      await expect(gates.getByRole("table", { name: "Gates" }).getByRole("rowgroup").getByRole("row")).toHaveCount(m.last_review.gates.length);
    }
  });

  test("changes table expands to every change; drift monitor charts each armed detector", async ({ page, request }) => {
    const m = await json(request, "/api/model");
    await page.goto("/#/forecasts");

    const changes = page.getByRole("region", { name: "Changes that can shift performance" });
    const rows = changes.getByRole("rowgroup").getByRole("row");
    await expect(rows).toHaveCount(Math.min(6, m.changes.length));
    if (m.changes.length > 0) await expect(rows.first()).toContainText(m.changes[0].text);
    if (m.changes.length > 6) {
      await changes.getByRole("link", { name: `All changes (${m.changes.length})` }).click();
      await expect(page).toHaveURL(/#\/forecasts\/changes$/);
      await expect(rows).toHaveCount(m.changes.length);
      await changes.getByRole("link", { name: "Fewer changes" }).click();
      await expect(rows).toHaveCount(6);
    }

    const drift = page.getByRole("region", { name: "Drift monitor" });
    const charted = m.monitors.filter((mo: { series: unknown[] }) => mo.series.length > 0);
    if (charted.length > 1) await expect(drift.getByRole("tab")).toHaveCount(charted.length);
    const location = charted.find((mo: { chart: string }) => mo.chart === "d1_location") ?? charted[0];
    if (location) {
      await expect(drift.getByRole("img", { name: new RegExp(`^${location.label}`) })).toBeVisible();
      await expect(drift.getByText(`h ${location.threshold.toFixed(2)} · ARL₀ 300 posts`, { exact: true })).toBeVisible();
    }
    const scale = charted.find((mo: { chart: string }) => mo.chart === "d1_scale");
    if (scale) {
      await drift.getByRole("tab", { name: "Scale" }).click();
      await expect(drift.getByRole("img", { name: new RegExp(`^${scale.label}`) })).toBeVisible();
    }

    const monitors = page.getByRole("region", { name: "Monitors" });
    // Open alarms repeat their monitor's label, so the monitor row is the first match.
    for (const mo of m.monitors) await expect(monitors.getByText(mo.label, { exact: true }).first()).toBeVisible();
    const open = m.alarms.filter((a: { acknowledged_at: string | null }) => a.acknowledged_at == null);
    const alarms = page.getByRole("region", { name: "Open alarms" });
    await expect(alarms.getByText(open.length ? `Open alarms · ${open.length}` : "Open alarms", { exact: true })).toBeVisible();
    await expect(alarms.getByRole("button", { name: "Acknowledge" })).toHaveCount(Math.min(4, open.length));
    await page.getByRole("navigation", { name: "Main" }).getByText("Forecasts", { exact: true }).click();
    await page.screenshot({ path: "e2e/.results/forecasts.png" });
  });

  test("acknowledging an alarm removes it from the open list (mutable backend)", async ({ page, request }) => {
    const m = await json(request, `${ACTIONS}/api/model`);
    const open = m.alarms.filter((a: { acknowledged_at: string | null }) => a.acknowledged_at == null);
    test.skip(open.length === 0, "the fixture raised no alarm");
    const alarm = open[0];
    await page.goto(`${ACTIONS}/#/forecasts`);
    const row = page.locator(`[data-alarm="${alarm.id}"]`);
    await expect(row).toContainText(alarm.label);
    const response = page.waitForResponse((r) => r.url().endsWith(`/api/alarms/${alarm.id}/ack`));
    await row.getByRole("button", { name: "Acknowledge" }).click();
    expect((await (await response).json()).acknowledged).toBe(true);
    await expect(row).toHaveCount(0);
    const after = await json(request, `${ACTIONS}/api/model`);
    const acked = after.alarms.find((a: { id: number }) => a.id === alarm.id);
    expect(acked.acknowledged_at).not.toBeNull();
  });
});
