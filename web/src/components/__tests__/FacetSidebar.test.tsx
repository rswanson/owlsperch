import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Facet } from "../../api";
import { FacetSidebar } from "../FacetSidebar";

const FACETS: Facet[] = [
  {
    field: "school",
    label: "School",
    values: [
      { value: "Evocation", count: 3 },
      { value: "Abjuration", count: 2 },
    ],
  },
  {
    field: "source",
    label: "Source",
    values: [{ value: "book-a", count: 5, label: "Book A" }],
  },
];

describe("FacetSidebar", () => {
  it("renders one group per facet, with counts", () => {
    render(<FacetSidebar facets={FACETS} selected={{}} onToggle={vi.fn()} />);
    expect(screen.getByText("School")).toBeInTheDocument();
    expect(screen.getByText("Source")).toBeInTheDocument();
    expect(screen.getByText("(3)")).toBeInTheDocument();
  });

  it("shows the book label instead of the raw book_id for the source facet", () => {
    render(<FacetSidebar facets={FACETS} selected={{}} onToggle={vi.fn()} />);
    expect(screen.getByText(/Book A/)).toBeInTheDocument();
    expect(screen.queryByText(/book-a/)).not.toBeInTheDocument();
  });

  it("checks boxes whose value is in `selected`", () => {
    render(
      <FacetSidebar facets={FACETS} selected={{ school: ["Evocation"] }} onToggle={vi.fn()} />,
    );
    expect(screen.getByLabelText(/Evocation/)).toBeChecked();
    expect(screen.getByLabelText(/Abjuration/)).not.toBeChecked();
  });

  it("calls onToggle with the facet field and value when a checkbox is clicked", async () => {
    const onToggle = vi.fn();
    render(<FacetSidebar facets={FACETS} selected={{}} onToggle={onToggle} />);
    await userEvent.click(screen.getByLabelText(/Evocation/));
    expect(onToggle).toHaveBeenCalledWith("school", "Evocation");
  });

  it("renders nothing when there are no facets", () => {
    const { container } = render(<FacetSidebar facets={[]} selected={{}} onToggle={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("builds a valid, matching id/htmlFor pair for a value with spaces and punctuation", () => {
    const facetsWithSpaces: Facet[] = [
      {
        field: "range",
        label: "Range",
        values: [{ value: "Close (25 ft.)", count: 1 }],
      },
    ];
    const { container } = render(
      <FacetSidebar facets={facetsWithSpaces} selected={{}} onToggle={vi.fn()} />,
    );

    const checkbox = screen.getByLabelText(/Close \(25 ft\.\)/);
    const label = container.querySelector("label");
    expect(label).not.toBeNull();
    expect(label?.getAttribute("for")).toBe(checkbox.id);
    // A valid HTML id: no whitespace or characters that break CSS
    // selectors/URL fragments.
    expect(checkbox.id).toMatch(/^[A-Za-z][A-Za-z0-9_-]*$/);
  });
});
