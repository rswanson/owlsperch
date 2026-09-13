import { expect, test } from "@playwright/test";

// Acceptance criterion 6 (400px layout): runs flow A (see smoke.spec.ts)
// under the `mobile-400` project's 400x800 viewport (playwright.config.ts),
// and additionally asserts there's no horizontal overflow on either page.
test("flow A fits without horizontal scrolling at 400px width", async ({ page }) => {
  await page.goto("/");

  const input = page.getByRole("combobox", { name: "Search" });
  await expect(input).toBeFocused();

  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(400);

  await input.fill("fireb");
  const results = page.locator("#search-results");
  await expect(results.getByText("Fireball")).toBeVisible();
  await expect(results.getByText("Spells", { exact: true })).toBeVisible();

  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(400);

  await input.press("Enter");

  await expect(page).toHaveURL(/\/r\/spell\/fireball$/);
  await expect(page.getByRole("heading", { level: 1, name: "Fireball" })).toBeVisible();

  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(400);
});
