const { test, expect } = require("@playwright/test");

test("operator investigates; separate human approves; worker verifies recovery", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "Demo operator", exact: true }).click();
  await page.locator('#scenario option[value="INC-001"]').waitFor({ state: "attached" });
  await page.getByLabel("Scenario", { exact: true }).selectOption("INC-001");
  await page.getByLabel("Incident description").fill(
    "Investigate why checkout-service is returning elevated 5xx errors."
  );
  await page.getByRole("button", { name: "Start investigation" }).click();
  await expect(page.getByText("Recent deployment regression", { exact: true }).first())
    .toBeVisible({ timeout: 20000 });
  await expect(page.getByRole("heading", { name: "Evidence library" })).toBeVisible();
  await page.getByRole("button", { name: /Approvals/ }).click();
  await expect(page.getByRole("button", { name: "Approve action", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Switch identity / sign out", exact: true }).click();
  await page.getByRole("button", { name: "Demo approver", exact: true }).click();
  await page.getByRole("button", { name: /Approvals/ }).click();
  await page.getByRole("checkbox").check();
  const decision = page.waitForResponse(response =>
    response.url().endsWith("/decision") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Approve action", exact: true }).click();
  expect((await decision).status()).toBe(200);
  await page.getByRole("button", { name: /Investigations/ }).click();
  await expect(page.getByText(/Recovery verified/).first()).toBeVisible({ timeout: 20000 });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download report", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/\.md$/);
  expect(errors).toEqual([]);
});

test("mobile layout and honest empty evaluation state", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Demo operator", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBe(true);
  await page.getByRole("button", { name: "Demo operator", exact: true }).click();
  await page.getByRole("button", { name: /Evaluations/ }).click();
  await expect(page.getByText(/ops evaluate --persist/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBe(true);
});
