import { expect, test } from "@playwright/test";

// The read-only fixture backend has no experiment: Next posts is empty and the follower panels
// are labelled as arriving in M3. Registering slots is covered in experiments.spec.ts.

test.describe("Account screen", () => {
  test("next posts empty state, M3 panels and the planned-post dialog", async ({ page }) => {
    await page.goto("/#/account");
    await expect(page.getByRole("heading", { name: "Account" })).toBeVisible();
    await expect(page.getByText("@your_account ·", { exact: false })).toBeVisible();

    for (const title of ["Followers vs forecast", "Follower funnel, 7 days", "Goal", "Posts that brought followers"]) {
      await expect(page.getByRole("region", { name: title }).getByText("arrives in M3")).toBeVisible();
    }
    const next = page.getByRole("region", { name: "Next posts" });
    await expect(next.getByText(/Nothing registered for the next 7 days\. Start an experiment/)).toBeVisible();
    await expect(next.getByText("Agents", { exact: true })).toBeVisible();
    await page.screenshot({ path: "e2e/.results/account.png" });

    await page.getByRole("button", { name: "Register post" }).click();
    const dialog = page.getByRole("dialog", { name: "Register a planned post" });
    await expect(dialog).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);

    await page.getByRole("button", { name: "Register slots" }).click();
    const slots = page.getByRole("dialog", { name: "Register slots" });
    await expect(slots.getByText(/No experiment is scheduled or running/)).toBeVisible();
    await expect(slots.getByRole("button", { name: "Register and draw arms" })).toBeDisabled();
  });
});
