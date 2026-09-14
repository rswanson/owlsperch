// Typed wrappers around the `owlsperch_server` JSON API (spec 4.9), mirrored
// here from `server/owlsperch_server/app.py`'s response shapes. Every call
// goes through `/api/*` -- see `vite.config.ts`'s dev-server proxy, which
// strips the `/api` prefix before forwarding to the FastAPI server.

export interface SearchHit {
  id: string;
  type: string;
  name: string;
  slug: string;
  citation: string | null;
  book_id: string;
}

export interface SearchGroup {
  type: string;
  label: string;
  hits: SearchHit[];
}

export interface SearchResponse {
  groups: SearchGroup[];
}

export interface SchemaUiHint {
  label?: string;
  filterable?: boolean;
  sortable?: boolean;
  group?: string;
  order?: number;
}

export interface SchemaField {
  name: string;
  "x-ui": SchemaUiHint;
}

export interface SchemaType {
  label: string;
  plural_label: string;
  version: number;
  fields: SchemaField[];
}

export interface SchemasResponse {
  types: Record<string, SchemaType>;
}

export interface StatsResponse {
  counts: Record<string, number>;
}

export interface HealthResponse {
  status: string;
  db: boolean;
}

// A table a record references via its own `tables` id list (spec 4.9,
// batch B10): a resolved one carries its grid; an id with no matching
// `table` record yet comes back with `pending: true` and every other field
// null/empty (spec edge case: "Record references a table that failed").
export interface RecordTable {
  id: string;
  pending: boolean;
  name: string | null;
  slug: string | null;
  caption: string | null;
  columns: string[];
  rows: string[][];
  citation: string | null;
  book_id: string | null;
}

/** A record's derived rules-taxonomy placement (batch B10b, design decision
 * D11/D18): `category` is always a real key (`"uncategorized"` at worst);
 * `chapter`/`section`/`path` are `null`/`[]` when unresolved (no toc file
 * for the book, or the record's page falls before the toc's first entry)
 * but always present as keys. */
export interface RecordToc {
  category: string;
  category_label: string;
  chapter: string | null;
  section: string | null;
}

/** One other printing of this record (batch B11, design decision D19) --
 * another record whose own `variant_of` points at this one: an errata/
 * update override target, a Rules Compendium override, or an older
 * printing demoted by latest-wins. */
export interface RecordVariant {
  id: string;
  book_id: string;
  book_title: string | null;
  citation: string | null;
  published: string | null;
}

/** One errata/update entry applied to this record (batch B11, design
 * decision D19), resolved from the stored `applied_overrides` id list. */
export interface AppliedOverride {
  id: string;
  name: string;
  type: string;
  book_id: string;
  book_title: string | null;
  citation: string | null;
  target_page: number | null;
  replacement_text: string;
}

export interface RecordDetail {
  id: string;
  type: string;
  name: string;
  slug: string;
  aliases: string[];
  book_id: string;
  pages: number[];
  citation: string | null;
  text_md: string;
  fields: Record<string, unknown>;
  tables: RecordTable[];
  canonical: boolean;
  variant_of: string | null;
  applied_overrides: AppliedOverride[];
  macro_eligible: boolean;
  schema_version: number;
  extraction: Record<string, unknown>;
  variants: RecordVariant[];
  links: unknown[];
  referenced_by: unknown[];
  book_title: string | null;
  toc: RecordToc & { path: string[] };
  /** The id of the class/prestige_class record whose page span swallowed
   * this one (batch B10c, design decision D11/D12), or `null` when this
   * record isn't superseded (every record before this batch). */
  superseded_by: string | null;
}

// ---------------------------------------------------------------------------
// class / prestige_class `fields` shapes (batch B10c, design decision D5) --
// mirrored from schemas/class.json and schemas/prestige_class.json.
// ---------------------------------------------------------------------------

export interface ClassSkill {
  skill: string;
  key_ability: string;
}

export interface SkillPoints {
  base: number;
  ability: string;
  first_level_multiplier?: number;
}

export interface SaveProgressions {
  fort: string;
  ref: string;
  will: string;
}

export interface Spellcasting {
  kind: string;
  ability: string;
  type: string;
  spell_list: string;
}

export interface ClassFeature {
  name: string;
  level: number;
  text_md: string;
}

export interface DescriptionSection {
  heading: string;
  text_md: string;
}

export interface ClassRequirement {
  kind: string;
  text: string;
}

export interface ClassFields {
  hit_die?: string;
  class_type?: string;
  max_level?: number;
  alignment?: string | null;
  abbreviation?: string | null;
  class_skills?: ClassSkill[];
  skill_points?: SkillPoints;
  bab_progression?: string;
  save_progressions?: SaveProgressions;
  spellcasting?: Spellcasting;
  level_table?: string;
  class_features?: ClassFeature[];
  description_sections?: DescriptionSection[];
  weapon_and_armor_proficiency?: string | null;
  source_pages?: { start: number; end: number };
  requirements?: ClassRequirement[];
}

/** Raised for any non-2xx response; `status` is the HTTP status code and
 * `message` is the server's `detail` field when present. */
export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, { signal });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body: unknown = await response.json();
      if (
        body !== null &&
        typeof body === "object" &&
        "detail" in body &&
        typeof (body as { detail: unknown }).detail === "string"
      ) {
        detail = (body as { detail: string }).detail;
      }
    } catch {
      // Non-JSON error body -- fall back to the status text above.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export function search(query: string, signal?: AbortSignal): Promise<SearchResponse> {
  return getJson(`/search?q=${encodeURIComponent(query)}`, signal);
}

export function getSchemas(signal?: AbortSignal): Promise<SchemasResponse> {
  return getJson("/schemas", signal);
}

export function getStats(signal?: AbortSignal): Promise<StatsResponse> {
  return getJson("/stats", signal);
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return getJson("/health", signal);
}

export function getRecord(
  type: string,
  slug: string,
  signal?: AbortSignal,
): Promise<RecordDetail> {
  return getJson(`/records/${encodeURIComponent(type)}/${encodeURIComponent(slug)}`, signal);
}

// ---------------------------------------------------------------------------
// Browse (spec 4.9/4.10, batch B9): GET /records/{type} (filtered, sorted,
// paginated list) and GET /facets/{type} (distinct filter values + counts).
// ---------------------------------------------------------------------------

export interface BrowseItem {
  id: string;
  type: string;
  name: string;
  slug: string;
  book_id: string;
  citation: string | null;
  facets: Record<string, string[]>;
  /** Derived rules-taxonomy placement (batch B10b) -- see `RecordToc`. */
  toc: RecordToc;
  /** The record's first page, or `null` when it has none -- lets the tree
   * (`RecordTree.tsx`) order chapter/section groups deterministically
   * without a second request. */
  page: number | null;
}

export interface BrowseResponse {
  type: string;
  total: number;
  page: number;
  page_size: number;
  items: BrowseItem[];
}

export interface FacetValue {
  value: string;
  count: number;
  /** Only present on the `source` facet (book_id -> book title). */
  label?: string;
}

export interface Facet {
  field: string;
  label: string;
  values: FacetValue[];
}

export interface FacetsResponse {
  type: string;
  facets: Facet[];
}

export function browseRecords(
  type: string,
  params: URLSearchParams,
  signal?: AbortSignal,
): Promise<BrowseResponse> {
  const qs = params.toString();
  return getJson(`/records/${encodeURIComponent(type)}${qs ? `?${qs}` : ""}`, signal);
}

export function getFacets(
  type: string,
  params: URLSearchParams,
  signal?: AbortSignal,
): Promise<FacetsResponse> {
  const qs = params.toString();
  return getJson(`/facets/${encodeURIComponent(type)}${qs ? `?${qs}` : ""}`, signal);
}
