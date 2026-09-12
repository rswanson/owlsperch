import { expect, test } from "@playwright/test";

// Flow A (spec 4.10, batch B7 "how to observe"): type a prefix, see grouped
// hits, press Enter, land on the record page, assert the name and a field
// label render. Runs against the fixture database built fresh by
// `e2e/serve-fixture.py` (see `playwright.config.ts`'s `webServer`).
test("search a prefix, press Enter, land on the record page", async ({ page }) => {
  await page.goto("/");

  const input = page.getByRole("combobox", { name: "Search" });
  await expect(input).toBeFocused();

  await input.fill("fireb");
  await expect(page.getByText("Fireball")).toBeVisible();
  await expect(page.getByText("Spells", { exact: true })).toBeVisible();

  await input.press("Enter");

  await expect(page).toHaveURL(/\/r\/spell\/fireball$/);
  await expect(page.getByRole("heading", { level: 1, name: "Fireball" })).toBeVisible();
  await expect(page.getByText("School")).toBeVisible();

  // The record's citation renders, and its `text_md` renders as Markdown
  // (the fixture record's body has a `**fireball**` bold span) rather than
  // as literal asterisk text.
  await expect(page.locator(".citation")).toHaveText("FB p. 1");
  await expect(page.locator(".text-md strong").first()).toHaveText("fireball");
});
