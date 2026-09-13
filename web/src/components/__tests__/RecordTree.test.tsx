import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { BrowseItem, FacetValue } from "../../api";
import { buildTree, RecordTree } from "../RecordTree";

function item(overrides: Partial<BrowseItem> = {}): BrowseItem {
  return {
    id: "rules_section:fixture-book:x",
    type: "rules_section",
    name: "X",
    slug: "x",
    book_id: "fixture-book",
    citation: "FB p. 1",
    facets: {},
    toc: { category: "combat", category_label: "Combat", chapter: "Chapter 2: Combat", section: "X" },
    page: 3,
    ...overrides,
  };
}

const CATEGORY_ORDER: FacetValue[] = [
  { value: "combat", count: 2, label: "Combat" },
  { value: "equipment", count: 1, label: "Equipment" },
];

describe("buildTree", () => {
  it("groups items into category -> chapter -> section", () => {
    const items = [
      item({
        id: "a",
        name: "Grapple Ranks",
        toc: { category: "combat", category_label: "Combat", chapter: "Chapter 2: Combat", section: "Grapple Ranks" },
        page: 3,
      }),
      item({
        id: "b",
        name: "Hauling Gear",
        toc: { category: "equipment", category_label: "Equipment", chapter: "Chapter 3: Equipment", section: "Hauling Gear" },
        page: 4,
      }),
    ];

    const tree = buildTree(items, CATEGORY_ORDER);

    expect(tree.map((c) => c.key)).toEqual(["combat", "equipment"]);
    expect(tree[0].label).toBe("Combat");
    expect(tree[0].count).toBe(1);
    expect(tree[0].chapters[0].key).toBe("Chapter 2: Combat");
    expect(tree[0].chapters[0].sections[0].key).toBe("Grapple Ranks");
    expect(tree[0].chapters[0].sections[0].items[0].name).toBe("Grapple Ranks");
  });

  it("orders categories by the given categoryOrder, not alphabetically", () => {
    const items = [
      item({ id: "a", toc: { category: "equipment", category_label: "Equipment", chapter: "C", section: "S" } }),
      item({ id: "b", toc: { category: "combat", category_label: "Combat", chapter: "C", section: "S" } }),
    ];
    // "combat" sorts after "equipment" alphabetically, but CATEGORY_ORDER
    // puts combat first.
    const tree = buildTree(items, CATEGORY_ORDER);
    expect(tree.map((c) => c.key)).toEqual(["combat", "equipment"]);
  });

  it("a category not present in categoryOrder still appears, sorted last", () => {
    const items = [
      item({ id: "a", toc: { category: "unknown-cat", category_label: "Unknown", chapter: "C", section: "S" } }),
      item({ id: "b", toc: { category: "combat", category_label: "Combat", chapter: "C", section: "S" } }),
    ];
    const tree = buildTree(items, CATEGORY_ORDER);
    expect(tree.map((c) => c.key)).toEqual(["combat", "unknown-cat"]);
  });

  it("orders chapters and sections by minimum page, then title", () => {
    const items = [
      item({ id: "a", toc: { category: "combat", category_label: "Combat", chapter: "Chapter 8", section: "Zed" }, page: 200 }),
      item({ id: "b", toc: { category: "combat", category_label: "Combat", chapter: "Chapter 8", section: "Alpha" }, page: 100 }),
      item({ id: "c", toc: { category: "combat", category_label: "Combat", chapter: "Chapter 1", section: "S" }, page: 300 }),
    ];
    const tree = buildTree(items, CATEGORY_ORDER);
    // Chapter 8 (min page 100) sorts before Chapter 1 (min page 300) --
    // page order, not title order.
    expect(tree[0].chapters.map((c) => c.key)).toEqual(["Chapter 8", "Chapter 1"]);
    // Within Chapter 8, "Alpha" (page 100) sorts before "Zed" (page 200).
    expect(tree[0].chapters[0].sections.map((s) => s.key)).toEqual(["Alpha", "Zed"]);
  });

  it("groups items with no chapter under an (Unsectioned) leaf", () => {
    const items = [
      item({ id: "a", toc: { category: "combat", category_label: "Combat", chapter: null, section: null } }),
    ];
    const tree = buildTree(items, CATEGORY_ORDER);
    expect(tree[0].chapters[0].key).toBe("(Unsectioned)");
    expect(tree[0].chapters[0].sections[0].key).toBe("(Unsectioned)");
    expect(tree[0].chapters[0].sections[0].items[0].id).toBe("a");
  });

  it("groups items with a chapter but no section under an (Unsectioned) section leaf", () => {
    const items = [
      item({ id: "a", toc: { category: "combat", category_label: "Combat", chapter: "Chapter 8", section: null } }),
    ];
    const tree = buildTree(items, CATEGORY_ORDER);
    expect(tree[0].chapters[0].key).toBe("Chapter 8");
    expect(tree[0].chapters[0].sections[0].key).toBe("(Unsectioned)");
  });

  it("counts are correct at every level", () => {
    const items = [
      item({ id: "a", toc: { category: "combat", category_label: "Combat", chapter: "C1", section: "S1" } }),
      item({ id: "b", toc: { category: "combat", category_label: "Combat", chapter: "C1", section: "S2" } }),
      item({ id: "c", toc: { category: "combat", category_label: "Combat", chapter: "C2", section: "S1" } }),
    ];
    const tree = buildTree(items, CATEGORY_ORDER);
    expect(tree[0].count).toBe(3);
    const c1 = tree[0].chapters.find((c) => c.key === "C1");
    expect(c1?.count).toBe(2);
    expect(c1?.sections.find((s) => s.key === "S1")?.count).toBe(1);
  });
});

describe("RecordTree", () => {
  it("renders collapsed <details>/<summary> groups with counts", () => {
    const items = [item({ id: "a", name: "Grapple Ranks" })];
    render(
      <MemoryRouter>
        <RecordTree items={items} categoryOrder={CATEGORY_ORDER} />
      </MemoryRouter>,
    );

    const summary = screen.getByText("Combat", { selector: "summary" });
    expect(summary).toBeInTheDocument();
    expect(summary.closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Grapple Ranks")).toBeInTheDocument();
  });

  it("auto-expands the category matching autoExpandCategory", () => {
    const items = [item({ id: "a" })];
    render(
      <MemoryRouter>
        <RecordTree items={items} categoryOrder={CATEGORY_ORDER} autoExpandCategory="combat" />
      </MemoryRouter>,
    );
    const summary = screen.getByText("Combat", { selector: "summary" });
    expect(summary.closest("details")).toHaveAttribute("open");
  });

  it("renders a no-results message for an empty item list", () => {
    render(
      <MemoryRouter>
        <RecordTree items={[]} categoryOrder={CATEGORY_ORDER} />
      </MemoryRouter>,
    );
    expect(screen.getByText("No results.")).toBeInTheDocument();
  });
});
