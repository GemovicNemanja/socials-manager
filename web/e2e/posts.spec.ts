import { expect, test } from "@playwright/test";

// Fixture: 43 daily runs to 2026-11-25 06:00 UTC -> 129 posts (123 matured, 6 provisional).
const TOTAL = 129;

test.describe("Posts screen against the real backend", () => {
  test("shell shows the owner account, the v1 navigation and the last run", async ({ page }) => {
    await page.goto("/#/posts");
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByText("@your_account")).toBeVisible();
    await expect(nav.getByText("YA", { exact: true })).toBeVisible();
    for (const item of ["Overview", "Posts", "Account", "Forecasts", "Experiments"]) {
      await expect(nav.getByText(item, { exact: true })).toBeVisible();
    }
    for (const removed of ["Patterns", "Insights", "Collection", "Agents", "Journal"]) {
      await expect(nav.getByText(removed, { exact: true })).toHaveCount(0);
    }
    await expect(nav.getByText(/Last run/)).toBeVisible();
    await expect(nav.getByText(/\$15/)).toBeVisible();
  });

  test("table lists posts newest first with counts from the API", async ({ page }) => {
    await page.goto("/#/posts");
    await expect(page.getByText(`${TOTAL} tracked · 123 matured · 6 provisional (under 48h)`)).toBeVisible();
    await expect(page.getByText(`${TOTAL} posts · newest first`)).toBeVisible();
    const rows = page.getByRole("rowgroup").getByRole("row");
    await expect(rows).toHaveCount(50);
    await expect(rows.first().getByText("Provisional")).toBeVisible();
    await expect(rows.first().getByText(/^≈/)).toBeVisible();
  });

  test("status filter narrows the table through the API", async ({ page }) => {
    await page.goto("/#/posts");
    await page.getByRole("button", { name: /Status/ }).click();
    await page.getByRole("listbox", { name: "Status" }).getByText("Above", { exact: true }).click();
    await expect(page.getByText("23 posts · newest first")).toBeVisible();
    const pills = page.getByRole("rowgroup").getByRole("row");
    await expect(pills).toHaveCount(23);
    for (const label of ["As expected", "Below", "Provisional"]) {
      await expect(page.getByRole("rowgroup").getByText(label, { exact: true })).toHaveCount(0);
    }
  });

  test("search filters the loaded rows", async ({ page }) => {
    await page.goto("/#/posts");
    await expect(page.getByRole("rowgroup").getByRole("row")).toHaveCount(50);
    await page.getByPlaceholder("Filter by text, label or id").fill("cadence");
    const rows = page.getByRole("rowgroup").getByRole("row");
    await expect(rows.first()).toContainText(/cadence/i);
    expect(await rows.count()).toBeLessThan(50);
  });

  test("other screens are placeholders until their milestone", async ({ page }) => {
    await page.goto("/#/posts");
    await page.getByRole("navigation", { name: "Main" }).getByText("Forecasts", { exact: true }).click();
    await expect(page.getByRole("heading", { name: "Forecasts" })).toBeVisible();
    await expect(page.locator("p", { hasText: "arrives in" })).toContainText("Forecasts arrives in M3");
  });

  test("full-page screenshot for visual review", async ({ page }) => {
    await page.goto("/#/posts");
    await expect(page.getByRole("rowgroup").getByRole("row")).toHaveCount(50);
    await page.screenshot({ path: "e2e/.results/posts.png" });
  });
});
