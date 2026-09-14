import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { BrowsePage } from "../BrowsePage";

function renderBrowsePage(initialPath = "/browse/spell") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/browse/:type" element={<BrowsePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const SCHEMAS: api.SchemasResponse = {
  types: {
    spell: {
      label: "Spell",
      plural_label: "Spells",
      version: 1,
      fields: [
        { name: "school", "x-ui": { label: "School", filterable: true, sortable: false } },
      ],
    },
  },
};

const BROWSE_RESPONSE: api.BrowseResponse = {
  type: "spell",
  total: 2,
  page: 1,
  page_size: 50,
  items: [
    {
      id: "spell:book-a:fireball",
      type: "spell",
      name: "Fireball",
      slug: "fireball",
      book_id: "book-a",
      citation: "BA p. 1",
      facets: { school: ["Evocation"] },
      toc: { category: "magic", category_label: "Magic", chapter: null, section: null },
      page: 1,
    },
    {
      id: "spell:book-a:alarm",
      type: "spell",
      name: "Alarm",
      slug: "alarm",
      book_id: "book-a",
      citation: "BA p. 2",
      facets: { school: ["Abjuration"] },
      toc: { category: "magic", category_label: "Magic", chapter: null, section: null },
      page: 2,
    },
  ],
};

const FACETS_RESPONSE: api.FacetsResponse = {
  type: "spell",
  facets: [
    {
      field: "school",
      label: "School",
      values: [
        { value: "Evocation", count: 1 },
        { value: "Abjuration", count: 1 },
      ],
    },
  ],
};

describe("BrowsePage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the type heading, facet sidebar, and result list", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS);
    vi.spyOn(api, "browseRecords").mockResolvedValue(BROWSE_RESPONSE);
    vi.spyOn(api, "getFacets").mockResolvedValue(FACETS_RESPONSE);

    renderBrowsePage();

    expect(await screen.findByRole("heading", { name: "Spells" })).toBeInTheDocument();
    expect(await screen.findByText("Fireball")).toBeInTheDocument();
    expect(screen.getByText("Alarm")).toBeInTheDocument();
    expect(screen.getByText("School")).toBeInTheDocument();
    expect(screen.getByText("Page 1 of 1 (2 total)")).toBeInTheDocument();

    const fireballLink = screen.getByRole("link", { name: /Fireball/ });
    expect(fireballLink).toHaveAttribute("href", "/r/spell/fireball");
  });

  it("reads initial filters from the URL and passes them to the API calls", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS);
    const browseSpy = vi.spyOn(api, "browseRecords").mockResolvedValue(BROWSE_RESPONSE);
    const facetsSpy = vi.spyOn(api, "getFacets").mockResolvedValue(FACETS_RESPONSE);

    renderBrowsePage("/browse/spell?school=Abjuration");

    await waitFor(() => expect(browseSpy).toHaveBeenCalled());
    expect(browseSpy.mock.calls[0][1].getAll("school")).toEqual(["Abjuration"]);
    expect(facetsSpy.mock.calls[0][1].getAll("school")).toEqual(["Abjuration"]);
  });

  it("checks the matching facet checkbox for a filter already in the URL", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS);
    vi.spyOn(api, "browseRecords").mockResolvedValue(BROWSE_RESPONSE);
    vi.spyOn(api, "getFacets").mockResolvedValue(FACETS_RESPONSE);

    renderBrowsePage("/browse/spell?school=Abjuration");

    expect(await screen.findByLabelText(/Abjuration/)).toBeChecked();
    expect(screen.getByLabelText(/Evocation/)).not.toBeChecked();
  });

  it("clicking a facet checkbox re-fetches with the new filter applied", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS);
    const browseSpy = vi.spyOn(api, "browseRecords").mockResolvedValue(BROWSE_RESPONSE);
    vi.spyOn(api, "getFacets").mockResolvedValue(FACETS_RESPONSE);

    renderBrowsePage();

    await screen.findByText("Fireball");
    await userEvent.click(await screen.findByLabelText(/Evocation/));

    await waitFor(() => {
      const lastCall = browseSpy.mock.calls[browseSpy.mock.calls.length - 1];
      expect(lastCall[1].getAll("school")).toEqual(["Evocation"]);
    });
  });

  it("disables Previous on page 1 and Next on the last page", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS);
    vi.spyOn(api, "browseRecords").mockResolvedValue({
      ...BROWSE_RESPONSE,
      total: 120,
      page: 1,
      page_size: 50,
    });
    vi.spyOn(api, "getFacets").mockResolvedValue(FACETS_RESPONSE);

    renderBrowsePage();

    await screen.findByText("Fireball");
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).not.toBeDisabled();
  });

  it("disables Next on the last page", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS);
    vi.spyOn(api, "browseRecords").mockResolvedValue({
      ...BROWSE_RESPONSE,
      total: 120,
      page: 3,
      page_size: 50,
    });
    vi.spyOn(api, "getFacets").mockResolvedValue(FACETS_RESPONSE);

    renderBrowsePage("/browse/spell?page=3");

    await screen.findByText("Fireball");
    expect(screen.getByRole("button", { name: "Previous" })).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("clicking Next updates the page query param and refetches with page=2", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS);
    const browseSpy = vi.spyOn(api, "browseRecords").mockResolvedValue({
      ...BROWSE_RESPONSE,
      total: 120,
      page: 1,
      page_size: 50,
    });
    vi.spyOn(api, "getFacets").mockResolvedValue(FACETS_RESPONSE);

    renderBrowsePage();

    await screen.findByText("Fireball");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() => {
      const lastCall = browseSpy.mock.calls[browseSpy.mock.calls.length - 1];
      expect(lastCall[1].get("page")).toBe("2");
    });
  });

  it("resets the page query param when a filter changes", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS);
    const browseSpy = vi.spyOn(api, "browseRecords").mockResolvedValue({
      ...BROWSE_RESPONSE,
      total: 120,
      page: 3,
      page_size: 50,
    });
    vi.spyOn(api, "getFacets").mockResolvedValue(FACETS_RESPONSE);

    renderBrowsePage("/browse/spell?page=3");

    await screen.findByText("Fireball");
    await userEvent.click(await screen.findByLabelText(/Evocation/));

    await waitFor(() => {
      const lastCall = browseSpy.mock.calls[browseSpy.mock.calls.length - 1];
      expect(lastCall[1].has("page")).toBe(false);
      expect(lastCall[1].getAll("school")).toEqual(["Evocation"]);
    });
  });

  it("shows an error message when the request fails", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS);
    vi.spyOn(api, "browseRecords").mockRejectedValue(new api.ApiError(500, "boom"));
    vi.spyOn(api, "getFacets").mockResolvedValue(FACETS_RESPONSE);

    renderBrowsePage();

    expect(await screen.findByText("boom")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Tree view (batch B10b, design decision D13)
// ---------------------------------------------------------------------------

const RULES_SCHEMAS: api.SchemasResponse = {
  types: {
    rules_section: { label: "Rules section", plural_label: "Rules sections", version: 1, fields: [] },
  },
};

function ruleItem(id: string, name: string, category: string, chapter: string): api.BrowseItem {
  const categoryLabel = category.charAt(0).toUpperCase() + category.slice(1);
  return {
    id,
    type: "rules_section",
    name,
    slug: id,
    book_id: "fixture-book",
    citation: null,
    facets: {},
    // A distinct `section` (not equal to `name`) avoids an ambiguous
    // getByText match between the section-group <summary> and the leaf
    // record <a> link, which share the item's group in a single-item group.
    toc: { category, category_label: categoryLabel, chapter, section: `${name} section` },
    page: 1,
  };
}

const TREE_FACETS_RESPONSE: api.FacetsResponse = {
  type: "rules_section",
  facets: [
    { field: "category", label: "Category", values: [{ value: "combat", count: 1, label: "Combat" }] },
    { field: "source", label: "Source", values: [] },
  ],
};

const EMPTY_BROWSE = (type: string): api.BrowseResponse => ({
  type,
  total: 0,
  page: 1,
  page_size: 200,
  items: [],
});

/** The tree view (acceptance criterion 6) also fetches `class`/
 * `prestige_class` to populate the `classes` category branch whenever the
 * page's own type is `rules_section`. Most tree tests don't care about
 * that merge, so this stubs those two types to an empty page and lets
 * `response` answer for `rules_section` itself -- keeping every existing
 * assertion about `rules_section`-only content unchanged. */
function mockTreeBrowse(response: api.BrowseResponse) {
  return vi.spyOn(api, "browseRecords").mockImplementation((type) =>
    Promise.resolve(type === "rules_section" ? response : EMPTY_BROWSE(type)),
  );
}

describe("BrowsePage tree view", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("defaults to tree view for rules_section with no ?view= param", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(RULES_SCHEMAS);
    mockTreeBrowse({
      type: "rules_section",
      total: 1,
      page: 1,
      page_size: 200,
      items: [ruleItem("a", "Grapple Ranks", "combat", "Chapter 2: Combat")],
    });
    vi.spyOn(api, "getFacets").mockResolvedValue(TREE_FACETS_RESPONSE);

    renderBrowsePage("/browse/rules_section");

    // The tree renders a <summary> for the category, not a flat <ul> item
    // link the way list view would.
    expect(await screen.findByText("Grapple Ranks")).toBeInTheDocument();
    expect(screen.getByText("Combat", { selector: "summary" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Pagination" })).not.toBeInTheDocument();
  });

  it("?view=list forces the flat list even for rules_section", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(RULES_SCHEMAS);
    mockTreeBrowse({
      type: "rules_section",
      total: 1,
      page: 1,
      page_size: 50,
      items: [ruleItem("a", "Grapple Ranks", "combat", "Chapter 2: Combat")],
    });
    vi.spyOn(api, "getFacets").mockResolvedValue(TREE_FACETS_RESPONSE);

    renderBrowsePage("/browse/rules_section?view=list");

    expect(await screen.findByRole("navigation", { name: "Pagination" })).toBeInTheDocument();
    expect(screen.queryByText("Combat", { selector: "summary" })).not.toBeInTheDocument();
  });

  it("the view toggle never forwards `view` to the API calls", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(RULES_SCHEMAS);
    const browseSpy = mockTreeBrowse({
      type: "rules_section",
      total: 1,
      page: 1,
      page_size: 200,
      items: [ruleItem("a", "Grapple Ranks", "combat", "Chapter 2: Combat")],
    });
    const facetsSpy = vi.spyOn(api, "getFacets").mockResolvedValue(TREE_FACETS_RESPONSE);

    renderBrowsePage("/browse/rules_section?view=tree");

    await screen.findByText("Grapple Ranks");
    for (const call of browseSpy.mock.calls) expect(call[1].has("view")).toBe(false);
    for (const call of facetsSpy.mock.calls) expect(call[1].has("view")).toBe(false);
  });

  it("clicking List/Tree updates the ?view= query param", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(RULES_SCHEMAS);
    mockTreeBrowse({
      type: "rules_section",
      total: 1,
      page: 1,
      page_size: 200,
      items: [ruleItem("a", "Grapple Ranks", "combat", "Chapter 2: Combat")],
    });
    vi.spyOn(api, "getFacets").mockResolvedValue(TREE_FACETS_RESPONSE);

    renderBrowsePage("/browse/rules_section");
    await screen.findByText("Grapple Ranks");

    await userEvent.click(screen.getByRole("button", { name: "List" }));
    expect(await screen.findByRole("navigation", { name: "Pagination" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Tree" }));
    await waitFor(() => {
      expect(screen.queryByRole("navigation", { name: "Pagination" })).not.toBeInTheDocument();
    });
  });

  it("fetches sequential pages at page_size=200 until every record is loaded", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(RULES_SCHEMAS);
    const browseSpy = vi.spyOn(api, "browseRecords").mockImplementation((type, params) => {
      if (type !== "rules_section") return Promise.resolve(EMPTY_BROWSE(type));
      const page = Number(params.get("page") ?? "1");
      const item = ruleItem(`item-${page}`, `Item ${page}`, "combat", "Chapter 2: Combat");
      return Promise.resolve({
        type: "rules_section",
        total: 3,
        page,
        page_size: 200,
        items: page <= 3 ? [item] : [],
      });
    });
    vi.spyOn(api, "getFacets").mockResolvedValue(TREE_FACETS_RESPONSE);

    renderBrowsePage("/browse/rules_section");

    expect(await screen.findByRole("link", { name: "Item 3" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Item 1" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Item 2" })).toBeInTheDocument();
    expect(browseSpy.mock.calls.every((call) => call[1].get("page_size") === "200")).toBe(true);
  });

  it("auto-expands the category when exactly one is selected in the URL", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(RULES_SCHEMAS);
    mockTreeBrowse({
      type: "rules_section",
      total: 1,
      page: 1,
      page_size: 200,
      items: [ruleItem("a", "Grapple Ranks", "combat", "Chapter 2: Combat")],
    });
    vi.spyOn(api, "getFacets").mockResolvedValue(TREE_FACETS_RESPONSE);

    renderBrowsePage("/browse/rules_section?category=combat");

    const summary = await screen.findByText("Combat", { selector: "summary" });
    expect(summary.closest("details")).toHaveAttribute("open");
  });

  it("merges class/prestige_class records into the classes category (acceptance criterion 6)", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue(RULES_SCHEMAS);
    const browseSpy = vi.spyOn(api, "browseRecords").mockImplementation((type) => {
      if (type === "class") {
        return Promise.resolve({
          type: "class",
          total: 1,
          page: 1,
          page_size: 200,
          items: [
            {
              id: "class:phb1:barbarian",
              type: "class",
              name: "Barbarian",
              slug: "barbarian",
              book_id: "phb1",
              citation: "PHB p. 25",
              facets: {},
              toc: { category: "classes", category_label: "Classes", chapter: null, section: null },
              page: 25,
            },
          ],
        });
      }
      if (type === "prestige_class") return Promise.resolve(EMPTY_BROWSE(type));
      // The rules_section fragment that used to live in the classes chapter
      // is now non-canonical (superseded) and so never comes back from
      // `/records/rules_section` -- this test models that by returning no
      // rules_section items at all, and asserts the branch is still
      // populated, by the class record instead.
      return Promise.resolve(EMPTY_BROWSE(type));
    });
    vi.spyOn(api, "getFacets").mockResolvedValue(TREE_FACETS_RESPONSE);

    renderBrowsePage("/browse/rules_section");

    expect(await screen.findByText("Barbarian")).toBeInTheDocument();
    expect(browseSpy.mock.calls.some((call) => call[0] === "class")).toBe(true);
    expect(browseSpy.mock.calls.some((call) => call[0] === "prestige_class")).toBe(true);
  });
});
