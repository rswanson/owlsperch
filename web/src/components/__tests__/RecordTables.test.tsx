import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RecordDetail, RecordTable } from "../../api";
import { buildOwnTable, RecordTables } from "../RecordTables";

const RESOLVED_TABLE: RecordTable = {
  id: "table:fixture-book:table-1-grapple-ranks",
  pending: false,
  name: "Table 1: Grapple Ranks",
  slug: "table-1-grapple-ranks",
  caption: "Table 1: Grapple Ranks",
  columns: ["Rank", "Bonus"],
  rows: [
    ["1", "+0"],
    ["2", "+2"],
  ],
  citation: "FB p. 3",
  book_id: "fixture-book",
};

const PENDING_TABLE: RecordTable = {
  id: "table:fixture-book:does-not-exist-yet",
  pending: true,
  name: null,
  slug: null,
  caption: null,
  columns: [],
  rows: [],
  citation: null,
  book_id: null,
};

function _recordDetail(overrides: Partial<RecordDetail> = {}): RecordDetail {
  return {
    id: "table:fixture-book:table-1-grapple-ranks",
    type: "table",
    name: "Table 1: Grapple Ranks",
    slug: "table-1-grapple-ranks",
    aliases: [],
    book_id: "fixture-book",
    pages: [3],
    citation: "FB p. 3",
    text_md: "",
    fields: {
      caption: "Table 1: Grapple Ranks",
      columns: ["Rank", "Bonus"],
      rows: [
        ["1", "+0"],
        ["2", "+2"],
      ],
      parent_record: null,
    },
    tables: [],
    canonical: true,
    variant_of: null,
    applied_overrides: [],
    macro_eligible: false,
    schema_version: 1,
    extraction: {},
    variants: [],
    links: [],
    referenced_by: [],
    ...overrides,
  };
}

describe("RecordTables", () => {
  it("renders nothing for an empty tables list", () => {
    const { container } = render(<RecordTables tables={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a resolved table's caption, header cells, and rows", () => {
    render(<RecordTables tables={[RESOLVED_TABLE]} />);

    expect(screen.getByText("Table 1: Grapple Ranks")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Rank" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Bonus" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "+0" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "+2" })).toBeInTheDocument();
  });

  it("renders a 'Table pending' marker naming the id for an unresolved table", () => {
    render(<RecordTables tables={[PENDING_TABLE]} />);

    expect(screen.getByText(/Table pending/)).toBeInTheDocument();
    expect(screen.getByText(/table:fixture-book:does-not-exist-yet/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("renders both a resolved and a pending table when a record has both", () => {
    render(<RecordTables tables={[RESOLVED_TABLE, PENDING_TABLE]} />);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText(/Table pending/)).toBeInTheDocument();
  });
});

describe("buildOwnTable", () => {
  it("builds a resolved RecordTable from a table-type record's own fields", () => {
    const record = _recordDetail();
    const table = buildOwnTable(record);

    expect(table).toEqual({
      id: "table:fixture-book:table-1-grapple-ranks",
      pending: false,
      name: "Table 1: Grapple Ranks",
      slug: "table-1-grapple-ranks",
      caption: "Table 1: Grapple Ranks",
      columns: ["Rank", "Bonus"],
      rows: [
        ["1", "+0"],
        ["2", "+2"],
      ],
      citation: "FB p. 3",
      book_id: "fixture-book",
    });
  });

  it("falls back to empty columns/rows and a null caption for malformed fields", () => {
    const record = _recordDetail({ fields: {} });
    const table = buildOwnTable(record);

    expect(table.columns).toEqual([]);
    expect(table.rows).toEqual([]);
    expect(table.caption).toBeNull();
  });
});
