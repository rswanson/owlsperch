import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import type { BrowseItem, RecordDetail } from "../../api";
import { ClassRecord, featureAnchorId, groupSpellsByLevel, levelForClass } from "../ClassRecord";

function makeRecord(overrides: Partial<RecordDetail> = {}): RecordDetail {
  return {
    id: "class:phb1:barbarian",
    type: "class",
    name: "Barbarian",
    slug: "barbarian",
    aliases: [],
    book_id: "phb1",
    pages: [25, 26],
    citation: "PHB pp. 25-26",
    text_md: "A barbarian is a fierce warrior.",
    fields: {
      hit_die: "d12",
      class_type: "base",
      abbreviation: "Brb",
      max_level: 20,
      alignment: "Any nonlawful",
      bab_progression: "good",
      save_progressions: { fort: "good", ref: "poor", will: "poor" },
      class_skills: [{ skill: "Climb", key_ability: "Str" }],
      skill_points: { base: 4, ability: "Int" },
      weapon_and_armor_proficiency: "A barbarian is proficient with all simple weapons.",
      class_features: [
        { name: "Rage", level: 1, text_md: "You rage." },
        { name: "Bonus Feat", level: 6, text_md: "" },
      ],
      description_sections: [{ heading: "Adventures", text_md: "Barbarians adventure." }],
    },
    tables: [
      {
        id: "table:phb1:table-3-3-the-barbarian",
        pending: false,
        name: "Table 3-3: The Barbarian",
        slug: "table-3-3-the-barbarian",
        caption: "Table 3-3: The Barbarian",
        columns: ["Level", "Special"],
        rows: [["1st", "Rage 1/day"]],
        citation: "PHB p. 25",
        book_id: "phb1",
      },
    ],
    canonical: true,
    variant_of: null,
    applied_overrides: [],
    macro_eligible: false,
    schema_version: 1,
    extraction: {},
    variants: [],
    links: [],
    referenced_by: [],
    book_title: "PHB",
    toc: {
      category: "classes",
      category_label: "Classes",
      chapter: "Chapter 3: Classes",
      section: "Barbarian",
      path: ["Chapter 3: Classes", "Barbarian"],
    },
    superseded_by: null,
    ...overrides,
  };
}

function renderClassRecord(record: RecordDetail) {
  return render(
    <MemoryRouter>
      <ClassRecord record={record} />
    </MemoryRouter>,
  );
}

function browseItem(overrides: Partial<BrowseItem> = {}): BrowseItem {
  return {
    id: "spell:phb1:fireball",
    type: "spell",
    name: "Fireball",
    slug: "fireball",
    book_id: "phb1",
    citation: "PHB p. 172",
    facets: { levels: ["Wizard 3"] },
    toc: { category: "magic", category_label: "Magic", chapter: null, section: null },
    page: 172,
    ...overrides,
  };
}

describe("levelForClass / groupSpellsByLevel", () => {
  it("parses the level out of the matching combined levels facet value", () => {
    const item = browseItem({ facets: { levels: ["Sorcerer 3", "Wizard 3"] } });
    expect(levelForClass(item, "Wizard")).toBe(3);
    expect(levelForClass(item, "Sorcerer")).toBe(3);
    expect(levelForClass(item, "Cleric")).toBeNull();
  });

  it("groups and sorts by level, then by name within a level", () => {
    const items = [
      browseItem({ id: "s:1", name: "Zzz Spell", facets: { levels: ["Wizard 3"] } }),
      browseItem({ id: "s:2", name: "Alarm", facets: { levels: ["Wizard 1"] } }),
      browseItem({ id: "s:3", name: "Aaa Spell", facets: { levels: ["Wizard 3"] } }),
    ];
    const groups = groupSpellsByLevel(items, "Wizard");
    expect(groups.map((g) => g.level)).toEqual([1, 3]);
    expect(groups[1].spells.map((s) => s.name)).toEqual(["Aaa Spell", "Zzz Spell"]);
  });
});

describe("ClassRecord", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders header facts, description sections, class skills, and proficiency", () => {
    renderClassRecord(makeRecord());

    expect(screen.getByText("Base")).toBeInTheDocument();
    expect(screen.getByText("Brb")).toBeInTheDocument();
    expect(screen.getByText("d12")).toBeInTheDocument();
    expect(screen.getByText("Any nonlawful")).toBeInTheDocument();
    expect(screen.getByText("good")).toBeInTheDocument();
    expect(screen.getByText(/Fort good, Ref poor, Will poor/)).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Adventures" })).toBeInTheDocument();
    expect(screen.getByText("Barbarians adventure.")).toBeInTheDocument();

    const skillsSection = screen.getByRole("heading", { name: "Class Skills" }).closest("section")!;
    expect(within(skillsSection).getByText("Climb (Str)")).toBeInTheDocument();

    expect(
      screen.getByText("A barbarian is proficient with all simple weapons."),
    ).toBeInTheDocument();
  });

  it("renders the progression table via the resolved record.tables", () => {
    renderClassRecord(makeRecord());

    expect(screen.getByRole("heading", { name: "Class Progression" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Special" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Rage 1/day" })).toBeInTheDocument();
  });

  it("renders every class feature, allowing an empty text_md", () => {
    renderClassRecord(makeRecord());

    expect(screen.getByRole("heading", { name: /Rage/ })).toBeInTheDocument();
    expect(screen.getByText("You rage.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Bonus Feat/ })).toBeInTheDocument();
  });

  it("gives every class feature its own deep-linkable anchor id", () => {
    renderClassRecord(makeRecord());

    const rageHeading = screen.getByRole("heading", { name: /Rage/ });
    const rageContainer = rageHeading.closest(".class-feature")!;
    expect(rageContainer).toHaveAttribute("id", featureAnchorId("Rage", 0));

    const bonusFeatHeading = screen.getByRole("heading", { name: /Bonus Feat/ });
    const bonusFeatContainer = bonusFeatHeading.closest(".class-feature")!;
    expect(bonusFeatContainer).toHaveAttribute("id", featureAnchorId("Bonus Feat", 1));

    expect(rageContainer.id).not.toEqual(bonusFeatContainer.id);
  });

  it("renders nothing for the Spells section when spellcasting is absent", () => {
    renderClassRecord(makeRecord());
    expect(screen.queryByRole("heading", { name: "Spells" })).not.toBeInTheDocument();
  });

  it("fetches and renders the Spells section, grouped by level, for a caster", async () => {
    vi.spyOn(api, "browseRecords").mockResolvedValue({
      type: "spell",
      total: 1,
      page: 1,
      page_size: 200,
      items: [browseItem({ facets: { levels: ["Wizard 3"] } })],
    });

    renderClassRecord(
      makeRecord({
        fields: {
          ...makeRecord().fields,
          spellcasting: { kind: "arcane", ability: "Int", type: "prepared", spell_list: "Wizard" },
        },
      }),
    );

    await screen.findByRole("heading", { name: "Spells" });
    expect(screen.getByRole("heading", { name: "Level 3" })).toBeInTheDocument();
    const spellLink = screen.getByRole("link", { name: "Fireball" });
    expect(spellLink).toHaveAttribute("href", "/r/spell/fireball");

    expect(api.browseRecords).toHaveBeenCalledWith(
      "spell",
      expect.any(URLSearchParams),
      expect.anything(),
    );
    const params = vi.mocked(api.browseRecords).mock.calls[0][1];
    expect(params.get("class")).toBe("Wizard");
  });
});
