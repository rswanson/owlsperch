import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { RecordPage } from "../RecordPage";

function renderRecordPage(type = "spell", slug = "fireball") {
  return render(
    <MemoryRouter initialEntries={[`/r/${type}/${slug}`]}>
      <Routes>
        <Route path="/r/:type/:slug" element={<RecordPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const SCHEMAS_RESPONSE: api.SchemasResponse = {
  types: {
    spell: {
      label: "Spell",
      plural_label: "Spells",
      version: 2,
      fields: [
        // Declared out of x-ui order on purpose, so a test that the
        // rendered DOM follows x-ui.order (not declaration order) is
        // actually exercising something.
        { name: "casting_time", "x-ui": { label: "Casting time", group: "casting", order: 6 } },
        { name: "school", "x-ui": { label: "School", group: "classification", order: 1 } },
      ],
    },
  },
};

function makeRecord(overrides: Partial<api.RecordDetail> = {}): api.RecordDetail {
  return {
    id: "spell:phb1:fireball",
    type: "spell",
    name: "Fireball",
    slug: "fireball",
    aliases: [],
    book_id: "phb1",
    pages: [172],
    citation: "PHB p. 172",
    text_md: "A **fireball** explodes.\n\n- One\n- Two",
    fields: { school: "Evocation", casting_time: "1 standard action" },
    tables: [],
    canonical: true,
    variant_of: null,
    applied_overrides: [],
    macro_eligible: false,
    schema_version: 2,
    extraction: {},
    variants: [],
    links: [],
    referenced_by: [],
    ...overrides,
  };
}

describe("RecordPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders name, citation, text_md as Markdown, and field groups in schema x-ui order", async () => {
    vi.spyOn(api, "getRecord").mockResolvedValue(makeRecord());
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS_RESPONSE);

    renderRecordPage();

    expect(
      await screen.findByRole("heading", { level: 1, name: "Fireball" }),
    ).toBeInTheDocument();
    expect(screen.getByText("PHB p. 172")).toBeInTheDocument();

    // text_md rendered as Markdown, not literal text: a **bold** span
    // becomes <strong>, and a `- item` list becomes <li>.
    const bold = screen.getByText("fireball", { selector: "strong" });
    expect(bold).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual(["One", "Two"]);

    // Field groups appear in schema x-ui order (School: order 1, before
    // Casting time: order 6) even though SCHEMAS_RESPONSE declares them the
    // other way around.
    const labels = screen.getAllByText(/^(School|Casting time)$/).map((el) => el.textContent);
    expect(labels).toEqual(["School", "Casting time"]);
  });

  it("shows the not-found page on a 404", async () => {
    vi.spyOn(api, "getRecord").mockRejectedValue(new api.ApiError(404, "not found"));
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS_RESPONSE);

    renderRecordPage();

    expect(await screen.findByText("Not found")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1, name: "Fireball" })).not.toBeInTheDocument();
  });

  it("shows an error message on a non-404 failure", async () => {
    vi.spyOn(api, "getRecord").mockRejectedValue(new api.ApiError(500, "server exploded"));
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS_RESPONSE);

    renderRecordPage();

    expect(await screen.findByText("server exploded")).toBeInTheDocument();
  });

  it("does not render raw HTML embedded in text_md (react-markdown has no rehype-raw)", async () => {
    vi.spyOn(api, "getRecord").mockResolvedValue(
      makeRecord({
        text_md: "Look: <img src=x onerror=alert(1)> and <script>alert(2)</script>",
      }),
    );
    vi.spyOn(api, "getSchemas").mockResolvedValue(SCHEMAS_RESPONSE);

    const { container } = renderRecordPage();
    await screen.findByRole("heading", { level: 1, name: "Fireball" });

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(container.textContent).toContain("<script>alert(2)</script>");
  });
});
