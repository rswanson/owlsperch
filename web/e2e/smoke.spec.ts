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
  const results = page.locator("#search-results");
  await expect(results.getByText("Fireball")).toBeVisible();
  await expect(results.getByText("Spells", { exact: true })).toBeVisible();

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

// Batch B10 acceptance criterion 12: open a rules_section record that owns
// a table via its `tables` list, and see the table rendered as a real HTML
// <table> (header cells included) below the record's own text.
test("a rules_section record renders its owned table below the text", async ({ page }) => {
  await page.goto("/r/rules_section/grapple-ranks");

  await expect(page.getByRole("heading", { level: 1, name: "Grapple Ranks" })).toBeVisible();
  await expect(page.getByText(/combatant's grapple rank/)).toBeVisible();

  const table = page.locator(".record-tables table");
  await expect(table).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "Rank" })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "Bonus" })).toBeVisible();
  await expect(table.getByRole("cell", { name: "+2" })).toBeVisible();

  // The table renders below the record's own text, not above it.
  const textBox = await page.locator(".text-md").boundingBox();
  const tableBox = await table.boundingBox();
  expect(textBox).not.toBeNull();
  expect(tableBox).not.toBeNull();
  expect(tableBox!.y).toBeGreaterThan(textBox!.y);
});
