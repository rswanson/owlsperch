import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SchemaField } from "../../api";
import { buildFieldGroups, FieldGroups, formatFieldValue, formatGroupLabel } from "../FieldGroups";

const SCHEMA_FIELDS: SchemaField[] = [
  {
    name: "school",
    "x-ui": { label: "School", group: "classification", order: 1 },
  },
  {
    name: "levels",
    "x-ui": { label: "Levels", group: "classification", order: 4 },
  },
  {
    name: "descriptors",
    "x-ui": { label: "Descriptors", group: "classification", order: 3 },
  },
  {
    name: "casting_time",
    "x-ui": { label: "Casting time", group: "casting", order: 6 },
  },
  {
    name: "subschool",
    "x-ui": { label: "Subschool", group: "classification", order: 2 },
  },
];

describe("formatFieldValue", () => {
  it("hides null and undefined", () => {
    expect(formatFieldValue(null)).toBeNull();
    expect(formatFieldValue(undefined)).toBeNull();
  });

  it("hides empty string and empty array", () => {
    expect(formatFieldValue("")).toBeNull();
    expect(formatFieldValue([])).toBeNull();
  });

  it("renders an array of scalars as a comma list", () => {
    expect(formatFieldValue(["Fire", "Evil"])).toBe("Fire, Evil");
  });

  it("renders an array of objects (levels) as 'Cleric 3, Wizard 3'", () => {
    expect(
      formatFieldValue([
        { class: "Cleric", level: 3 },
        { class: "Wizard", level: 3 },
      ]),
    ).toBe("Cleric 3, Wizard 3");
  });

  it("renders a plain scalar as its string form", () => {
    expect(formatFieldValue("Evocation")).toBe("Evocation");
    expect(formatFieldValue(3)).toBe("3");
  });
});

describe("formatGroupLabel", () => {
  it("capitalizes a single-word group key", () => {
    expect(formatGroupLabel("classification")).toBe("Classification");
    expect(formatGroupLabel("casting")).toBe("Casting");
    expect(formatGroupLabel("other")).toBe("Other");
  });

  it("capitalizes each word of a multi-word group key", () => {
    expect(formatGroupLabel("spell_resistance")).toBe("Spell Resistance");
  });
});

describe("buildFieldGroups", () => {
  it("groups fields by x-ui.group, ordered by group then field order", () => {
    const groups = buildFieldGroups(
      {
        school: "Evocation",
        subschool: null,
        descriptors: ["Fire"],
        levels: [{ class: "Sorcerer", level: 3 }],
        casting_time: "1 standard action",
      },
      SCHEMA_FIELDS,
    );

    expect(groups.map((g) => g.name)).toEqual(["classification", "casting"]);
    // subschool is null -> hidden, so classification has school, descriptors, levels in order.
    expect(groups[0].rows.map((r) => r.label)).toEqual(["School", "Descriptors", "Levels"]);
    expect(groups[0].rows.map((r) => r.value)).toEqual(["Evocation", "Fire", "Sorcerer 3"]);
    expect(groups[1].rows).toEqual([
      { key: "casting_time", label: "Casting time", order: 6, value: "1 standard action" },
    ]);
  });

  it("drops fields whose value is null/empty and groups with no visible fields", () => {
    const groups = buildFieldGroups(
      { school: null, subschool: null, descriptors: [], levels: [], casting_time: null },
      SCHEMA_FIELDS,
    );
    expect(groups).toEqual([]);
  });

  it("excludes fields named in hiddenFields (batch B10: a table record's own columns/rows)", () => {
    const groups = buildFieldGroups(
      { school: "Evocation", descriptors: ["Fire"] },
      SCHEMA_FIELDS,
      ["descriptors"],
    );
    expect(groups[0].rows.map((r) => r.label)).toEqual(["School"]);
  });
});

describe("FieldGroups component", () => {
  it("renders visible field labels and values, grouped", () => {
    render(
      <FieldGroups
        fields={{
          school: "Evocation",
          subschool: null,
          descriptors: ["Fire"],
          levels: [{ class: "Sorcerer", level: 3 }],
          casting_time: "1 standard action",
        }}
        schemaFields={SCHEMA_FIELDS}
      />,
    );

    expect(screen.getByText("School")).toBeInTheDocument();
    expect(screen.getByText("Evocation")).toBeInTheDocument();
    expect(screen.getByText("Levels")).toBeInTheDocument();
    expect(screen.getByText("Sorcerer 3")).toBeInTheDocument();
    expect(screen.queryByText("Subschool")).not.toBeInTheDocument();

    // Each group renders a heading from its key, so groups are visually
    // distinguishable (finding 5).
    expect(screen.getByRole("heading", { name: "Classification" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Casting" })).toBeInTheDocument();
  });

  it("renders nothing when every field is hidden", () => {
    const { container } = render(
      <FieldGroups fields={{ school: null }} schemaFields={SCHEMA_FIELDS} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("omits a field named in hiddenFields even when it has a value", () => {
    render(
      <FieldGroups
        fields={{ school: "Evocation", descriptors: ["Fire"] }}
        schemaFields={SCHEMA_FIELDS}
        hiddenFields={["descriptors"]}
      />,
    );
    expect(screen.getByText("School")).toBeInTheDocument();
    expect(screen.queryByText("Descriptors")).not.toBeInTheDocument();
  });
});
