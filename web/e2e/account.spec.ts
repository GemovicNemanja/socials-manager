import { expect, test, type APIRequestContext } from "@playwright/test";

// The read-only fixture backend has follower counts and gains but no follower forecast, goal or
// experiment yet: the chart shows actuals only, the goal panel reads "No goal set", past follower
// forecasts are empty and Next posts is empty. Populated elements are checked when the API has them.
// Registering slots is covered in experiments.spec.ts; saving a goal in goal.spec.ts.

const int = (n: number) => Math.round(n).toLocaleString("en-US");

async function json(request: APIRequestContext, path: string) {
  const response = await request.get(path);
  expect(response.ok()).toBeTruthy();
  return response.json();
}

test.describe("Account screen", () => {
  test("follower tiles, chart, funnel, goal and past follower forecasts read the API", async ({ page, request }) => {
    const f = await json(request, "/api/followers");
    const goals = await json(request, "/api/goals");
    await page.goto("/#/account");
    await expect(page.getByRole("heading", { name: "Account" })).toBeVisible();
    await expect(page.getByText(`@your_account · ${int(f.tiles.followers.value)} followers`, { exact: false })).toBeVisible();

    const tiles = page.getByRole("region", { name: "Follower expectations" });
    await expect(tiles.getByText(int(f.tiles.followers.value), { exact: true })).toBeVisible();
    await expect(tiles.getByText(`+${f.tiles.net_7d} net in 7d`)).toBeVisible();
    await expect(tiles.getByText(int(f.tiles.new_follows_7d.value), { exact: true })).toBeVisible();
    await expect(tiles.getByText(`${(f.tiles.visit_to_follow_7d.value * 100).toFixed(1)}%`, { exact: true })).toBeVisible();
    const keys = ["followers", "new_follows_7d", "unfollows_7d", "profile_visits_7d", "visit_to_follow_7d", "replies_7d"];
    const withExpected = keys.filter((k) => f.tiles[k].expected != null);
    await expect(tiles.getByText("no forecast yet", { exact: true })).toHaveCount(keys.length - withExpected.length);
    // Always show how the past did: a tile with a frozen expectation reads its outcome in words.
    for (const k of withExpected) {
      const outcome = { above: "Above expected", as_expected: "As expected", below: "Below expected" }[
        f.tiles[k].expected.outcome as "above" | "as_expected" | "below"
      ];
      await expect(tiles.getByText(outcome, { exact: true }).first()).toBeVisible();
    }

    const chart = page.getByRole("region", { name: "Followers vs forecast" });
    await expect(chart.getByRole("img", { name: /^Followers per run/ })).toBeVisible();
    await expect(chart.getByText(`Today ${int(f.actual.at(-1).value)}`)).toBeVisible();
    if (f.forecast == null) {
      await expect(chart.getByText(/No follower forecast frozen yet/)).toBeVisible();
    } else {
      await expect(chart.getByRole("img", { name: /forecast median/ })).toBeVisible();
    }

    const funnel = page.getByRole("region", { name: "Follower funnel, 7 days" });
    for (const step of f.funnel) {
      const row = funnel.locator("div", { has: page.getByText(step.label, { exact: true }) }).last();
      await expect(row).toContainText(step.note);
    }

    const goal = page.getByRole("region", { name: "Goal" });
    if (goals.current.length === 0) {
      await expect(goal.getByText("No goal set", { exact: true })).toBeVisible();
      await expect(goal.getByRole("button", { name: "Set goal" })).toBeVisible();
    } else {
      await expect(goal.getByRole("button", { name: "Edit" })).toBeVisible();
    }

    await expect(page.getByRole("region", { name: "Posts that brought followers" }).getByText(/^Not available yet/)).toBeVisible();
    const past = page.getByRole("region", { name: "Past follower forecasts" });
    if (f.past.length === 0) {
      await expect(past.getByText(f.forecast ? /None scored yet/ : /No follower forecast has been frozen yet/)).toBeVisible();
    } else {
      await expect(past.getByText(/-day, made /)).toHaveCount(f.past.length);
    }
    await page.screenshot({ path: "e2e/.results/account.png" });
  });

  test("next posts empty state and the dialogs from the top bar", async ({ page }) => {
    await page.goto("/#/account");
    const next = page.getByRole("region", { name: "Next posts" });
    await expect(next.getByText(/Nothing registered for the next 7 days\. Start an experiment/)).toBeVisible();
    await expect(next.getByText("Agents", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Register post" }).click();
    const dialog = page.getByRole("dialog", { name: "Register a planned post" });
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);

    await page.getByRole("button", { name: "Register slots" }).click();
    const slots = page.getByRole("dialog", { name: "Register slots" });
    await expect(slots.getByText(/No experiment is scheduled or running/)).toBeVisible();
    await expect(slots.getByRole("button", { name: "Register and draw arms" })).toBeDisabled();
    await page.keyboard.press("Escape");

    // The goal panel's action opens the same dialog as the top bar's Set goal.
    await page.getByRole("region", { name: "Goal" }).getByRole("button", { name: /Set goal|Edit/ }).click();
    const goal = page.getByRole("dialog", { name: "Set a goal" });
    await expect(goal.getByRole("radio", { name: "Followers" })).toHaveAttribute("aria-checked", "true");
    await goal.getByRole("button", { name: "Cancel" }).click();
    await expect(goal).toHaveCount(0);
  });
});
