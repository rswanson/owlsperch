import { expect, test } from "@playwright/test";

// Batch B10c, flow D: nav to Classes, open the fixture class, see the
// progression table and both class features render, and click a Spells
// section spell link through to the spell page. Runs against the fixture
// database built fresh by `e2e/serve-fixture.py` (see `playwright.config
// .ts`'s `webServer`) -- see `fixture_db.py`'s `_CLASS`/`_CLASS_TABLE`.
test("nav to Classes, open the fixture class, see its table/features/spells", async ({
  page,
}) => {
  await page.goto("/");

  await page
    .getByRole("navigation", { name: "Browse" })
    .getByRole("link", { name: "Classes", exact: true })
    .click();
  await expect(page).toHaveURL(/\/browse\/class$/);

  await page.getByRole("link", { name: "Fixture Mage" }).click();
  await expect(page).toHaveURL(/\/r\/class\/fixture-mage$/);
  await expect(page.getByRole("heading", { level: 1, name: "Fixture Mage" })).toBeVisible();

  // Header facts include class_type and the book's abbreviation, not just
  // the generic "Class" type badge (acceptance criterion 7).
  const facts = page.locator(".class-facts");
  await expect(facts.getByText("Base", { exact: true })).toBeVisible();
  await expect(facts.getByText("FxM", { exact: true })).toBeVisible();

  // Each class feature has its own deep-linkable anchor id.
  await expect(page.locator("#feature-arcane-bond-0")).toContainText("Arcane Bond");
  await expect(page.locator("#feature-bonus-feat-1")).toContainText("Bonus Feat");

  // The progression table renders as a real HTML table.
  const table = page.locator(".class-progression table");
  await expect(table).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "Level" })).toBeVisible();
  await expect(table.getByRole("cell", { name: "Arcane bond" })).toBeVisible();

  // Both class features render.
  await expect(page.getByRole("heading", { name: /Arcane Bond/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Bonus Feat/ })).toBeVisible();

  // The Spells section lists a real spell (Fireball is a Wizard 3 spell in
  // the fixture data) and links through to its own page.
  const spellsSection = page.locator(".class-spells");
  await expect(spellsSection.getByRole("heading", { name: "Spells" })).toBeVisible();
  const fireballLink = spellsSection.getByRole("link", { name: "Fireball" });
  await expect(fireballLink).toBeVisible();
  await fireballLink.click();

  await expect(page).toHaveURL(/\/r\/spell\/fireball$/);
  await expect(page.getByRole("heading", { level: 1, name: "Fireball" })).toBeVisible();
});
