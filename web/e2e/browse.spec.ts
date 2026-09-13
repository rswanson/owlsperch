import { expect, test } from "@playwright/test";

// Flow B (spec 4.10, batch B9 "how to observe"): from the home page, follow
// the header nav to Spells, narrow the list with the facet sidebar (class
// Cleric, level 3, school Conjuration), sort by name, and open the single
// remaining spell. Runs against the fixture database built fresh by
// `e2e/serve-fixture.py`, which (batch B9) includes "Summon Monster III"
// (Conjuration, with a Cleric-3 `levels` item among others) alongside
// "Fireball" (Evocation) and "Alarm" (Abjuration, no Cleric level) -- both
// of which must be filtered out.
test("browse spells, filter by class/level/school, sort by name, open one", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("link", { name: "Spells" }).click();
  await expect(page).toHaveURL(/\/browse\/spell$/);
  await expect(page.getByRole("heading", { name: "Spells" })).toBeVisible();

  // All three fixture spells are visible before any filter is applied.
  await expect(page.getByText("Fireball")).toBeVisible();
  await expect(page.getByText("Alarm")).toBeVisible();
  await expect(page.getByText("Summon Monster III")).toBeVisible();

  // Each checkbox's `checked` state is fully controlled by React from the
  // URL (rather than native, uncontrolled DOM state) -- `.click()` +
  // an auto-retrying `toBeChecked()` assertion tolerates the render that
  // follows the click, where `.check()`'s single immediate verification
  // can occasionally observe a checkbox mid-update.
  const clericCheckbox = page.getByLabel(/^Cleric /);
  await clericCheckbox.click();
  await expect(clericCheckbox).toBeChecked();

  const levelThreeCheckbox = page.getByLabel(/^3 /);
  await levelThreeCheckbox.click();
  await expect(levelThreeCheckbox).toBeChecked();

  const conjurationCheckbox = page.getByLabel(/^Conjuration /);
  await conjurationCheckbox.click();
  await expect(conjurationCheckbox).toBeChecked();

  await expect(page.getByText("Summon Monster III")).toBeVisible();
  await expect(page.getByText("Fireball")).not.toBeVisible();
  await expect(page.getByText("Alarm")).not.toBeVisible();

  // The filters landed in the URL query string (repeated params).
  const url = new URL(page.url());
  expect(url.searchParams.getAll("class")).toEqual(["Cleric"]);
  expect(url.searchParams.getAll("level")).toEqual(["3"]);
  expect(url.searchParams.getAll("school")).toEqual(["Conjuration"]);

  await page.getByLabel("Sort by").selectOption("name");

  await page.getByRole("link", { name: /Summon Monster III/ }).click();
  await expect(page).toHaveURL(/\/r\/spell\/summon-monster-iii$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Summon Monster III" }),
  ).toBeVisible();
});
