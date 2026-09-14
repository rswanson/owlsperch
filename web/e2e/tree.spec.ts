import { expect, test } from "@playwright/test";

// Flow C (batch B10b, design decision D16 / "how to observe"): from the
// home page, follow the header nav's "Rules" link, expand the Combat
// category and its chapter, open "Grapple Ranks", and see the
// Book > Chapter > Section breadcrumb. Runs against the fixture database
// built fresh by `e2e/serve-fixture.py`, whose `toc/fixture-book.json`
// gives the fixture's two rules_section records ("Grapple Ranks", combat;
// "Hauling Gear", equipment) two different populated tree categories.
test("open Rules, expand Combat, open a section, see the breadcrumb", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("link", { name: "Rules" }).click();
  await expect(page).toHaveURL(/\/browse\/rules_section$/);
  await expect(page.getByRole("heading", { name: "Rules sections" })).toBeVisible();

  // Tree view is the default for rules_section -- no pagination controls,
  // and both categories are visible (collapsed) before anything is clicked.
  await expect(page.getByRole("navigation", { name: "Pagination" })).not.toBeVisible();
  const combatSummary = page.locator("summary", { hasText: /^Combat\s/ });
  const equipmentSummary = page.locator("summary", { hasText: /^Equipment\s/ });
  await expect(combatSummary).toBeVisible();
  await expect(equipmentSummary).toBeVisible();

  // Expand the Combat category, then its one chapter.
  await combatSummary.click();
  const chapterSummary = page.locator("summary", { hasText: /^Chapter 2: Combat\s/ });
  await expect(chapterSummary).toBeVisible();
  await chapterSummary.click();

  // The section leaf and its record link appear once the chapter is open.
  const sectionSummary = page.locator("summary", { hasText: /^Grapple Ranks\s/ });
  await expect(sectionSummary).toBeVisible();
  await sectionSummary.click();

  await page.getByRole("link", { name: "Grapple Ranks" }).click();
  await expect(page).toHaveURL(/\/r\/rules_section\/grapple-ranks$/);
  await expect(page.getByRole("heading", { level: 1, name: "Grapple Ranks" })).toBeVisible();

  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(breadcrumb.getByRole("link", { name: "FB" })).toBeVisible();
  await expect(breadcrumb.getByRole("link", { name: "Chapter 2: Combat" })).toBeVisible();
  await expect(breadcrumb.getByText("Grapple Ranks")).toBeVisible();
});

// B10c-mand3 Part 6: the fixture toc now gives the fixture class record
// ("Fixture Mage") a real "classes" toc category, so the tree's Classes
// branch (populated via BrowsePage's TREE_MERGE_TYPES pulling class/
// prestige_class records into the rules_section tree) has something to
// open here, alongside the pre-existing Combat/Equipment branches.
test("expand Classes, open Fixture Mage", async ({ page }) => {
  await page.goto("/browse/rules_section");
  await expect(page.getByRole("heading", { name: "Rules sections" })).toBeVisible();

  const classesSummary = page.locator("summary", { hasText: /^Classes\s/ });
  await expect(classesSummary).toBeVisible();
  // The pre-existing categories are still there alongside the new one.
  await expect(page.locator("summary", { hasText: /^Combat\s/ })).toBeVisible();
  await expect(page.locator("summary", { hasText: /^Equipment\s/ })).toBeVisible();
  await classesSummary.click();

  const chapterSummary = page.locator("summary", { hasText: /^Chapter 4: Classes\s/ });
  await expect(chapterSummary).toBeVisible();
  await chapterSummary.click();

  const sectionSummary = page.locator("summary", { hasText: /^Fixture Mage\s/ });
  await expect(sectionSummary).toBeVisible();
  await sectionSummary.click();

  await page.getByRole("link", { name: "Fixture Mage" }).click();
  await expect(page).toHaveURL(/\/r\/class\/fixture-mage$/);
  await expect(page.getByRole("heading", { level: 1, name: "Fixture Mage" })).toBeVisible();
});

test("a quick link from the header nav opens the tree pre-filtered to that category", async ({
  page,
}) => {
  await page.goto("/");

  await expect(page.getByRole("link", { name: "Equipment" })).toBeVisible();
  await page.getByRole("link", { name: "Equipment" }).click();

  await expect(page).toHaveURL(/\/browse\/rules_section\?category=equipment$/);
  // Exactly one category selected in the URL -- it auto-expands.
  const equipmentDetails = page.locator("details.tree-category", { hasText: /^Equipment\s/ });
  await expect(equipmentDetails).toHaveAttribute("open", "");
  await expect(page.getByText("Grapple Ranks")).not.toBeVisible();
});
