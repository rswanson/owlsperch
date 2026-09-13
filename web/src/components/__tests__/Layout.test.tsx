import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { Layout } from "../Layout";

describe("Layout", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("lists every registered type in the header nav, linking to /browse/<type> (acceptance criterion 5)", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue({
      types: {
        spell: { label: "Spell", plural_label: "Spells", version: 1, fields: [] },
        feat: { label: "Feat", plural_label: "Feats", version: 1, fields: [] },
      },
    });

    render(
      <MemoryRouter>
        <Layout>
          <p>content</p>
        </Layout>
      </MemoryRouter>,
    );

    const spellsLink = await screen.findByRole("link", { name: "Spells" });
    expect(spellsLink).toHaveAttribute("href", "/browse/spell");
    const featsLink = screen.getByRole("link", { name: "Feats" });
    expect(featsLink).toHaveAttribute("href", "/browse/feat");
  });

  it("still renders children and the site title when the schema fetch fails", async () => {
    vi.spyOn(api, "getSchemas").mockRejectedValue(new Error("boom"));

    render(
      <MemoryRouter>
        <Layout>
          <p>content</p>
        </Layout>
      </MemoryRouter>,
    );

    expect(screen.getByText("content")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "owlsperch" })).toBeInTheDocument();
  });

  // -------------------------------------------------------------------------
  // "Rules" link + quick links (batch B10b, design decision D14)
  // -------------------------------------------------------------------------

  it("excludes rules_section from the generic type list and renders a dedicated Rules link", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue({
      types: {
        spell: { label: "Spell", plural_label: "Spells", version: 1, fields: [] },
        rules_section: {
          label: "Rules section",
          plural_label: "Rules sections",
          version: 1,
          fields: [],
        },
      },
    });
    vi.spyOn(api, "getFacets").mockResolvedValue({
      type: "rules_section",
      facets: [{ field: "category", label: "Category", values: [] }],
    });

    render(
      <MemoryRouter>
        <Layout>
          <p>content</p>
        </Layout>
      </MemoryRouter>,
    );

    const rulesLink = await screen.findByRole("link", { name: "Rules" });
    expect(rulesLink).toHaveAttribute("href", "/browse/rules_section");
    expect(screen.queryByRole("link", { name: "Rules sections" })).not.toBeInTheDocument();
  });

  it("renders quick links only for categories with count > 0, labeled with the facet value's label", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue({
      types: {
        rules_section: {
          label: "Rules section",
          plural_label: "Rules sections",
          version: 1,
          fields: [],
        },
      },
    });
    vi.spyOn(api, "getFacets").mockResolvedValue({
      type: "rules_section",
      facets: [
        {
          field: "category",
          label: "Category",
          values: [
            { value: "classes", count: 70, label: "Classes" },
            { value: "equipment", count: 28, label: "Equipment" },
            { value: "skills", count: 0, label: "Skills" },
            { value: "combat", count: 109, label: "Combat" },
          ],
        },
      ],
    });

    render(
      <MemoryRouter>
        <Layout>
          <p>content</p>
        </Layout>
      </MemoryRouter>,
    );

    const classesLink = await screen.findByRole("link", { name: "Classes" });
    expect(classesLink).toHaveAttribute("href", "/browse/rules_section?category=classes");
    expect(screen.getByRole("link", { name: "Equipment" })).toHaveAttribute(
      "href",
      "/browse/rules_section?category=equipment",
    );
    // "Skills" has zero records this build -- no quick link.
    expect(screen.queryByRole("link", { name: "Skills" })).not.toBeInTheDocument();
    // "Combat" isn't one of the categories a quick link is offered for.
    expect(screen.queryByRole("link", { name: "Combat" })).not.toBeInTheDocument();
  });

  it("renders no quick links (but keeps the rest of the nav working) when the facets fetch fails", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue({
      types: {
        spell: { label: "Spell", plural_label: "Spells", version: 1, fields: [] },
        rules_section: {
          label: "Rules section",
          plural_label: "Rules sections",
          version: 1,
          fields: [],
        },
      },
    });
    vi.spyOn(api, "getFacets").mockRejectedValue(new Error("boom"));

    render(
      <MemoryRouter>
        <Layout>
          <p>content</p>
        </Layout>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("link", { name: "Spells" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Rules" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Classes" })).not.toBeInTheDocument();
  });

  it("does not render a Rules link or fetch facets when rules_section isn't registered", async () => {
    vi.spyOn(api, "getSchemas").mockResolvedValue({
      types: { spell: { label: "Spell", plural_label: "Spells", version: 1, fields: [] } },
    });
    const facetsSpy = vi.spyOn(api, "getFacets").mockResolvedValue({
      type: "rules_section",
      facets: [],
    });

    render(
      <MemoryRouter>
        <Layout>
          <p>content</p>
        </Layout>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("link", { name: "Spells" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Rules" })).not.toBeInTheDocument();
    expect(facetsSpy).not.toHaveBeenCalled();
  });
});
