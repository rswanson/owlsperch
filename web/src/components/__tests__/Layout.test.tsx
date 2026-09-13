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
});
