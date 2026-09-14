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

// Batch B10b-mand2: every breadcrumb link forces `view=tree`, since
// `/browse/:type` defaults to the flat, paginated `list` view for every
// type except `rules_section`. Exercised on a spell (not rules_section,
// which would pass even unfixed since its default is already the tree).
test("a spell record's chapter breadcrumb lands in the tree view, not the list", async ({
  page,
}) => {
  await page.goto("/r/spell/fireball");

  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb" });
  await breadcrumb.getByRole("link", { name: "Chapter 1: Magic" }).click();

  await expect(page).toHaveURL(/\/browse\/spell\?.*view=tree/);
  await expect(page.locator(".record-tree")).toBeVisible();
  await expect(page.locator(".record-tree summary", { hasText: /^Magic\s/ })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Pagination" })).not.toBeVisible();
});

// Batch B11: precedence's "Other printings"/"Overrides applied" sections
// against the fixture DB's duplicate "Ice Storm" printing (fixture-book,
// fixture-book-2) and its errata entry (fixture-errata).
test("a record with duplicate printings and an errata override shows both sections", async ({
  page,
}) => {
  await page.goto("/r/spell/ice-storm");

  await expect(page.getByRole("heading", { level: 1, name: "Ice Storm" })).toBeVisible();

  const otherPrintings = page.locator(".other-printings");
  await expect(otherPrintings).toBeVisible();
  await expect(otherPrintings.getByText("FB p. 6")).toBeVisible();

  const overridesApplied = page.locator(".overrides-applied");
  await expect(overridesApplied).toBeVisible();
  await expect(overridesApplied.getByRole("link", { name: /Ice Storm/ })).toBeVisible();
});
