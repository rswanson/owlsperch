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
    },
    {
      id: "spell:book-a:alarm",
      type: "spell",
      name: "Alarm",
      slug: "alarm",
      book_id: "book-a",
      citation: "BA p. 2",
      facets: { school: ["Abjuration"] },
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
