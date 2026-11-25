import { expect, test } from "@playwright/test";

// Specs that change data run against their own fixture backend (playwright.config.ts, ACTIONS_PORT),
// in order: register planned posts, then start a run. The backend uses the fake provider with the
// regime check and backups off, so a run is free and offline. Forecast numbers come from the API.
const ACTIONS = "http://127.0.0.1:8797";
const int = (n: number) => Math.round(n).toLocaleString("en-US");

/** Tomorrow at hh:mm UTC as a datetime-local value (the e2e account's zone is UTC). */
function tomorrowAt(hours: number, minutes = 0): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + 1);
  d.setUTCHours(hours, minutes, 0, 0);
  return d.toISOString().slice(0, 16);
}

test.describe.configure({ mode: "serial" });
test.use({ baseURL: ACTIONS });

test.describe("Register a planned post", () => {
  test("shows the draft range live, registers, and lists it as upcoming", async ({ page, request }) => {
    await page.goto("/#/overview");
    const briefing = page.getByRole("region", { name: "Last briefing" });
    await expect(briefing.getByRole("region", { name: "Planned posts" }).getByText(/None upcoming/)).toBeVisible();
    await briefing.getByRole("button", { name: "Register post" }).click();

    const dialog = page.getByRole("dialog", { name: "Register a planned post" });
    await expect(dialog).toBeVisible();
    const text = "How the run engine resumes after an interrupted step";
    await dialog.getByLabel("Planned time").fill(tomorrowAt(13, 30));
    await dialog.getByLabel("Topic").selectOption("software_development");
    const draftResponse = page.waitForResponse(
      (r) => r.url().endsWith("/api/planned/draft") && r.request().postDataJSON()?.text === text,
    );
    await dialog.getByLabel("Draft text (optional)").fill(text);
    const draft = await (await draftResponse).json();
    const q = draft.forecast.quantiles;
    const preview = dialog.getByRole("region", { name: "Draft forecast" });
    await expect(preview.getByText(`80% range ${int(q.q10)} – ${int(q.q90)}`)).toBeVisible();
    await expect(preview.getByText(`median ${int(q.q50)}`)).toBeVisible();
    await expect(preview.getByText("prior only · uncalibrated")).toBeVisible();
    await page.screenshot({ path: "e2e/.results/register-dialog.png" });

    const registered = page.waitForResponse((r) => r.url().endsWith("/api/planned") && r.request().method() === "POST");
    await dialog.getByRole("button", { name: "Register and freeze forecast" }).click();
    const result = await (await registered).json();
    expect(result.registered).toBe(true);
    await expect(dialog).toHaveCount(0);

    const schedule = await (await request.get(`${ACTIONS}/api/planned`)).json();
    const slot = schedule.slots.find((s: { slot: string }) => s.slot === result.slot);
    expect(slot.text).toBe(text);
    expect(slot.forecast_q50).toBe(result.forecast.quantiles.q50);
    const planned = briefing.getByRole("region", { name: "Planned posts" });
    await expect(planned.getByText(text)).toBeVisible();
    await expect(planned.getByText(/Nov|Dec|Jan/).first()).toContainText("13:30");
  });

  test("warns about a second original within 3 hours and asks for an override", async ({ page }) => {
    await page.goto("/#/overview");
    await page.getByRole("region", { name: "Last briefing" }).getByRole("button", { name: "Register post" }).click();
    const dialog = page.getByRole("dialog", { name: "Register a planned post" });
    await dialog.getByLabel("Planned time").fill(tomorrowAt(14, 30));
    await expect(dialog.getByText(/Within 3h of slot:\d+/)).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Register anyway and freeze" })).toBeEnabled();
    await dialog.getByLabel("Planned time").fill("2020-01-01T10:00");
    await expect(dialog.getByText(/Pick a time in the future/)).toBeVisible();
    await expect(dialog.getByRole("button", { name: /Register/ })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
  });
});

test.describe("Start a run from the web", () => {
  test("shows each step while it runs, then the new briefing", async ({ page, request }) => {
    const before = await (await request.get(`${ACTIONS}/api/briefing`)).json();
    await page.goto("/#/overview");
    const briefing = page.getByRole("region", { name: "Last briefing" });
    await expect(briefing.getByText(`run ${before.run_id} ·`, { exact: false })).toBeVisible();

    // A fake-provider run takes about a second. Hold its (real) response back briefly so the panel
    // can be seen with every step's result, as status polling reports them.
    await page.route("**/api/runs", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      const response = await route.fetch({ timeout: 90_000 });
      await new Promise((resolve) => setTimeout(resolve, 2_500));
      await route.fulfill({ response });
    });
    await page.getByRole("button", { name: "Start run", exact: true }).click();
    const progress = page.getByRole("region", { name: "Run in progress" });
    await expect(progress).toBeVisible();
    await expect(page.getByRole("button", { name: "Running…" })).toBeDisabled();
    const steps = progress.getByRole("listitem");
    await expect(steps.first()).toContainText("Refresh token, collect posts");
    const stepCount = (await (await request.get(`${ACTIONS}/api/runs/status`)).json()).latest.steps.length;
    await expect(progress.locator('li[data-state="done"]')).toHaveCount(stepCount, { timeout: 30_000 });
    await expect(steps.first()).toContainText(/new posts · \$/);
    await page.screenshot({ path: "e2e/.results/run-progress.png" });

    await expect(progress).toHaveCount(0, { timeout: 90_000 });
    const after = await (await request.get(`${ACTIONS}/api/briefing`)).json();
    expect(after.run_id).toBe(before.run_id + 1);
    await expect(briefing.getByText(`run ${after.run_id} ·`, { exact: false })).toBeVisible();
    const status = await (await request.get(`${ACTIONS}/api/runs/status`)).json();
    expect(status.latest.status).toBe("finished");
    expect(status.latest.steps.every((s: { status: string }) => s.status === "done")).toBe(true);
    // The planned post registered earlier is still upcoming after the run.
    await expect(briefing.getByRole("region", { name: "Planned posts" }).getByRole("listitem")).toHaveCount(1);
  });

  test("a failed collection shows the error banner with the reconnect command", async ({ page }) => {
    // The fake provider never fails, so this one test rewrites the real status response into a run
    // stopped by an expired X sign-in (the server's classification is covered by the API tests).
    await page.route("**/api/runs/status", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      const run = { ...body.latest, status: "failed", finished_at: null };
      run.steps = run.steps.map((s: { name: string }, i: number) =>
        i === 0 ? { ...s, status: "failed", error: "ReauthRequired: X rejected the refresh token." } : { ...s, status: "pending" },
      );
      await route.fulfill({
        response,
        json: {
          ...body,
          unfinished: run,
          active: false,
          failure: {
            run_id: run.run_id,
            step: "collect",
            kind: "reauth",
            title: "X sign-in expired, so this run collected nothing",
            detail: "X rejected the refresh token. Reconnect, then start the run again.",
            command: "socialmgr connect",
          },
        },
      });
    });
    await page.goto("/#/overview");
    await expect(page.getByRole("region", { name: "Recent posts" }).getByRole("row").nth(5)).toBeVisible();
    const banner = page.getByRole("alert").filter({ hasText: "X sign-in expired" });
    await expect(banner).toBeVisible();
    await expect(banner.getByText("socialmgr connect", { exact: true })).toBeVisible();
    await expect(banner.getByRole("button", { name: "Copy command" })).toBeVisible();
    await page.screenshot({ path: "e2e/.results/error-banner.png" });
    await banner.getByRole("button", { name: "Dismiss" }).click();
    await expect(banner).toHaveCount(0);
  });
});
