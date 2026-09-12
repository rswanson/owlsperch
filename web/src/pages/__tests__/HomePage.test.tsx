import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { HomePage } from "../HomePage";

function renderHomePage() {
  return render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );
}

describe("HomePage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows an empty state naming `owlsperch build-db` when the database hasn't been built (acceptance criterion 4)", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok", db: false });
    const schemasSpy = vi.spyOn(api, "getSchemas");
    const statsSpy = vi.spyOn(api, "getStats");

    renderHomePage();

    expect(await screen.findByText("No data yet")).toBeInTheDocument();
    expect(screen.getByText("uv run owlsperch build-db")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    // Counts are only fetched once the db is known to be ready.
    expect(schemasSpy).not.toHaveBeenCalled();
    expect(statsSpy).not.toHaveBeenCalled();
  });

  it("shows the search box and a type count hint when the database is ready", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok", db: true });
    vi.spyOn(api, "getSchemas").mockResolvedValue({
      types: {
        spell: { label: "Spell", plural_label: "Spells", version: 1, fields: [] },
      },
    });
    vi.spyOn(api, "getStats").mockResolvedValue({ counts: { spell: 186 } });

    renderHomePage();

    expect(await screen.findByRole("combobox", { name: "Search" })).toBeInTheDocument();
    expect(await screen.findByText("186 Spells")).toBeInTheDocument();
    expect(screen.queryByText("No data yet")).not.toBeInTheDocument();
  });

  it("falls back to the search box (no count hint) if health reports ok but stats/schemas fail", async () => {
    vi.spyOn(api, "getHealth").mockResolvedValue({ status: "ok", db: true });
    vi.spyOn(api, "getSchemas").mockRejectedValue(new Error("boom"));
    vi.spyOn(api, "getStats").mockResolvedValue({ counts: {} });

    renderHomePage();

    expect(await screen.findByRole("combobox", { name: "Search" })).toBeInTheDocument();
    expect(screen.queryByText(/Spells/)).not.toBeInTheDocument();
  });
});
