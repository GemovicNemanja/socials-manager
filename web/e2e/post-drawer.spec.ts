import { expect, test } from "@playwright/test";

const int = (n: number) => Math.round(n).toLocaleString("en-US");

test.describe("Posts detail drawer", () => {
  test("clicking a matured post opens its frozen forecast against the outcome", async ({ page, request }) => {
    const list = await (await request.get("/api/posts?status=as_expected&limit=1")).json();
    const item = list.items[0];
    const id = item.id.replace("post:", "");
    const detail = await (await request.get(`/api/posts/${item.id}`)).json();

    await page.goto("/#/posts");
    await page.getByRole("button", { name: /Status/ }).click();
    await page.getByRole("listbox", { name: "Status" }).getByText("As expected", { exact: true }).click();
    const row = page.getByRole("rowgroup").getByRole("row").first();
    await expect(row).toContainText(item.text);
    await row.click();

    await expect(page).toHaveURL(new RegExp(`#/posts/${id}$`));
    await expect(row).toHaveAttribute("aria-selected", "true");
    const drawer = page.getByRole("dialog", { name: "Post detail" });
    await expect(drawer).toContainText(item.text);
    await expect(drawer.getByText(/^As expected · p\d+$/)).toBeVisible();

    const quantiles = drawer.getByLabel("Forecast quantiles");
    const q = detail.forecast.quantiles;
    for (const [name, value] of [
      ["p5", q.q05],
      ["p25", q.q25],
      ["p50", q.q50],
      ["p75", q.q75],
      ["p95", q.q95],
    ] as const) {
      // The range strip's legend repeats some labels, so match the first occurrence.
      await expect(quantiles.getByText(name, { exact: true }).first()).toBeVisible();
      await expect(quantiles.getByText(int(value), { exact: true }).first()).toBeVisible();
    }
    await expect(drawer.getByRole("img", { name: /expected 80% range|expected range/i })).toBeVisible();
    const score = drawer.getByLabel("Score");
    await expect(score.getByText(detail.score.pit_mid.toFixed(2), { exact: true })).toBeVisible();
    await expect(score.getByText(detail.score.crps_log.toFixed(3), { exact: true })).toBeVisible();

    await expect(drawer.getByLabel("Captures by age").getByRole("row")).toHaveCount(detail.snapshots.length);
    await expect(drawer.getByText(detail.forecast.model_version)).toBeVisible();
    await expect(drawer.getByText(`${detail.forecast.manifest_hash.slice(0, 16)}…`)).toBeVisible();
    await page.screenshot({ path: "e2e/.results/posts-drawer.png" });

    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
    await expect(page).toHaveURL(/#\/posts$/);
  });

  test("the URL keeps the selected post; the close button closes it", async ({ page, request }) => {
    const list = await (await request.get("/api/posts?status=provisional&limit=1")).json();
    const item = list.items[0];
    await page.goto(`/#/posts/${item.id.replace("post:", "")}`);
    const drawer = page.getByRole("dialog", { name: "Post detail" });
    await expect(drawer).toContainText(item.text);
    await expect(drawer.getByText("Provisional · under 48h")).toBeVisible();
    await expect(drawer.getByText(/Scored at the first capture past 48h/)).toBeVisible();
    await drawer.getByRole("button", { name: "Close post detail" }).click();
    await expect(drawer).toHaveCount(0);
  });

  test("an unknown post says so", async ({ page }) => {
    await page.goto("/#/posts/999999");
    await expect(page.getByRole("dialog", { name: "Post detail" })).toContainText("Post 999999 not found.");
  });
});
