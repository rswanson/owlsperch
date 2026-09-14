import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  ApiError,
  browseRecords,
  getFacets,
  getSchemas,
  type BrowseItem,
  type Facet,
  type SchemaField,
} from "../api";
import { FacetSidebar } from "../components/FacetSidebar";
import { RecordTree } from "../components/RecordTree";

/** Query params that aren't filter fields -- excluded from the facet
 * request (which "accepts the same filter params") and from the
 * checkbox-selected-state map. `view` (batch B10b, D13) picks list vs. tree
 * rendering; it must never be forwarded to `/records/{type}` or
 * `/facets/{type}` -- the server answers 400 "unknown query parameter" for
 * an unrecognized one. */
const RESERVED_PARAMS = new Set(["sort", "page", "page_size", "view"]);

/** Tree mode (batch B10b, D13) needs every matching record to group into
 * category/chapter/section, so it fetches sequential pages at the server's
 * `MAX_PAGE_SIZE` instead of the user-facing page size, capped so a huge
 * result set can't hang the page or the browser. */
const TREE_PAGE_SIZE = 200;
const MAX_TREE_ITEMS = 2000;

/** Batch B10c (acceptance criterion 6): the `classes` tree category is
 * populated from `class`/`prestige_class` records, not `rules_section`
 * fragments -- superseding makes the old in-span fragments non-canonical,
 * so `/browse/rules_section?view=tree` must also pull these two types in
 * to keep that branch populated. Fetched unfiltered (the rules_section
 * page's filter params are rules_section-specific fields the class
 * endpoints don't recognize) and merged into the one tree, which groups
 * purely off each item's own `toc.category` -- so a `class` item lands in
 * the `classes` branch alongside whatever `rules_section` items remain
 * there. */
const TREE_MERGE_TYPES: Record<string, string[]> = {
  rules_section: ["class", "prestige_class"],
};

/** A `kind` discriminant lets `BrowsePage`'s fetch effect branch on the
 * result's actual shape (list results carry `page`/`page_size`; tree
 * results don't paginate) without TypeScript widening a plain `"page" in
 * result` check into an ambiguous intersection type. */
interface ListFetchResult {
  kind: "list";
  items: BrowseItem[];
  total: number;
  page: number;
  pageSize: number;
}

interface TreeFetchResult {
  kind: "tree";
  items: BrowseItem[];
  total: number;
}

async function fetchList(
  type: string,
  params: URLSearchParams,
  signal: AbortSignal,
): Promise<ListFetchResult> {
  const response = await browseRecords(type, params, signal);
  return {
    kind: "list",
    items: response.items,
    total: response.total,
    page: response.page,
    pageSize: response.page_size,
  };
}

async function fetchAllPages(
  type: string,
  filters: URLSearchParams,
  signal: AbortSignal,
): Promise<{ items: BrowseItem[]; total: number }> {
  const items: BrowseItem[] = [];
  let total = 0;
  let page = 1;
  for (;;) {
    const params = new URLSearchParams(filters);
    params.set("page", String(page));
    params.set("page_size", String(TREE_PAGE_SIZE));
    const response = await browseRecords(type, params, signal);
    total = response.total;
    items.push(...response.items);
    if (response.items.length === 0 || items.length >= total || items.length >= MAX_TREE_ITEMS) {
      break;
    }
    page += 1;
  }
  return { items, total };
}

async function fetchAllForTree(
  type: string,
  filters: URLSearchParams,
  signal: AbortSignal,
): Promise<TreeFetchResult> {
  const primary = await fetchAllPages(type, filters, signal);
  const mergeTypes = TREE_MERGE_TYPES[type] ?? [];
  const merged = await Promise.all(
    mergeTypes.map((mergeType) => fetchAllPages(mergeType, new URLSearchParams(), signal)),
  );
  const items = [...primary.items, ...merged.flatMap((m) => m.items)];
  const total = primary.total + merged.reduce((sum, m) => sum + m.total, 0);
  return { kind: "tree", items, total };
}

function filterParams(searchParams: URLSearchParams): URLSearchParams {
  const out = new URLSearchParams();
  for (const [key, value] of searchParams.entries()) {
    if (!RESERVED_PARAMS.has(key)) out.append(key, value);
  }
  return out;
}

function selectedFromParams(searchParams: URLSearchParams): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const key of new Set(searchParams.keys())) {
    if (RESERVED_PARAMS.has(key)) continue;
    out[key] = searchParams.getAll(key);
  }
  return out;
}

/** The `/browse/:type` route (spec 4.10, batch B9; tree view added in
 * batch B10b): a facet sidebar (`FacetSidebar`, generated from `GET
 * /facets/{type}`), a sort control, and either a paginated flat result list
 * (`?view=list`, the default for every type except `rules_section`) or a
 * category -> chapter -> section tree (`?view=tree`, the default for
 * `rules_section`, and available for any type). All of it -- filters,
 * sort, page, view -- lives in the URL's query string via
 * `useSearchParams`, so reload/back/forward restore the view.
 *
 * Refetching (on any filter/sort/page/view change) keeps the previous
 * facets and results mounted -- and the checkboxes checked according to the
 * URL, which already reflects the change -- while the new page loads,
 * showing a "Loading…" line alongside rather than tearing the sidebar down
 * and rebuilding it (which briefly drops a just-checked checkbox from the
 * DOM entirely). */
export function BrowsePage() {
  const { type } = useParams<{ type: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [schemaFields, setSchemaFields] = useState<SchemaField[]>([]);
  const [typeLabel, setTypeLabel] = useState<string>(type ?? "");

  const [items, setItems] = useState<BrowseItem[]>([]);
  const [facets, setFacets] = useState<Facet[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const view = searchParams.get("view") ?? (type === "rules_section" ? "tree" : "list");

  useEffect(() => {
    if (!type) return;
    const controller = new AbortController();
    getSchemas(controller.signal)
      .then((schemas) => {
        const info = schemas.types[type];
        setSchemaFields(info?.fields ?? []);
        setTypeLabel(info?.plural_label ?? type);
      })
      .catch(() => {
        // Sort options fall back to "Name" only; not fatal to the page.
      });
    return () => controller.abort();
  }, [type]);

  const searchKey = searchParams.toString();

  useEffect(() => {
    if (!type) return;
    const controller = new AbortController();
    setLoading(true);
    setErrorMessage(null);

    const listOrTree =
      view === "tree"
        ? fetchAllForTree(type, filterParams(searchParams), controller.signal)
        : fetchList(type, searchParams, controller.signal);

    Promise.all([listOrTree, getFacets(type, filterParams(searchParams), controller.signal)])
      .then(([browse, facetsResponse]) => {
        setItems(browse.items);
        setTotal(browse.total);
        if (browse.kind === "list") {
          setPage(browse.page);
          setPageSize(browse.pageSize);
        }
        setFacets(facetsResponse.facets);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setErrorMessage(err instanceof ApiError ? err.message : "Failed to load results.");
        setLoading(false);
      });
    return () => controller.abort();
    // `searchParams` is re-derived from `searchKey` each render; depending on
    // the string form (rather than the object) avoids re-fetching on every
    // render when the params haven't actually changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, searchKey, view]);

  const selected = useMemo(() => selectedFromParams(searchParams), [searchKey]); // eslint-disable-line react-hooks/exhaustive-deps

  function updateParams(mutate: (params: URLSearchParams) => void) {
    const next = new URLSearchParams(searchParams);
    mutate(next);
    setSearchParams(next);
  }

  function handleToggle(field: string, value: string) {
    updateParams((next) => {
      const current = next.getAll(field);
      next.delete(field);
      const values = current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value];
      for (const v of values) next.append(field, v);
      next.delete("page");
    });
  }

  function handleSortChange(sort: string) {
    updateParams((next) => {
      next.set("sort", sort);
      next.delete("page");
    });
  }

  function handlePageChange(nextPage: number) {
    updateParams((next) => {
      next.set("page", String(nextPage));
    });
  }

  function handleViewChange(nextView: "list" | "tree") {
    updateParams((next) => {
      next.set("view", nextView);
      next.delete("page");
    });
  }

  const sortValue = searchParams.get("sort") ?? "name";
  const sortableFields = schemaFields.filter((f) => f["x-ui"]?.sortable);

  const categoryFacet = facets.find((f) => f.field === "category");
  const selectedCategories = searchParams.getAll("category");
  const autoExpandCategory = selectedCategories.length === 1 ? selectedCategories[0] : null;
  const truncated = view === "tree" && items.length < total;

  return (
    <div className="browse-page">
      <h1>{typeLabel}</h1>
      <div className="browse-layout">
        <FacetSidebar facets={facets} selected={selected} onToggle={handleToggle} />
        <div className="browse-main">
          <div className="browse-controls">
            <div className="view-toggle" role="group" aria-label="View">
              <button
                type="button"
                className={view === "list" ? "view-toggle-active" : ""}
                onClick={() => {
                  handleViewChange("list");
                }}
              >
                List
              </button>
              <button
                type="button"
                className={view === "tree" ? "view-toggle-active" : ""}
                onClick={() => {
                  handleViewChange("tree");
                }}
              >
                Tree
              </button>
            </div>
            {view === "list" && (
              <label className="sort-control">
                Sort by{" "}
                <select
                  value={sortValue}
                  onChange={(event) => {
                    handleSortChange(event.target.value);
                  }}
                >
                  <option value="name">Name (A-Z)</option>
                  <option value="-name">Name (Z-A)</option>
                  {sortableFields.flatMap((field) => {
                    const label = field["x-ui"].label ?? field.name;
                    return [
                      <option key={field.name} value={field.name}>
                        {label} (ascending)
                      </option>,
                      <option key={`-${field.name}`} value={`-${field.name}`}>
                        {label} (descending)
                      </option>,
                    ];
                  })}
                </select>
              </label>
            )}
          </div>

          {/* aria-live="polite" so assistive tech is told when the list
           * finishes loading or the result count changes, without having to
           * poll focus/DOM changes itself. */}
          <div className="browse-status" aria-live="polite" role="status">
            {loading && <p className="search-status">Loading…</p>}
            {errorMessage && <p className="search-status search-error">{errorMessage}</p>}
            {!loading && !errorMessage && (
              <p className="search-status">
                {items.length === 0
                  ? "No results."
                  : truncated
                    ? `Showing the first ${items.length} of ${total} ${typeLabel.toLowerCase()}`
                    : `${total} ${typeLabel.toLowerCase()}`}
              </p>
            )}
          </div>

          {view === "tree" ? (
            <RecordTree
              items={items}
              categoryOrder={categoryFacet?.values ?? []}
              autoExpandCategory={autoExpandCategory}
            />
          ) : (
            <>
              {items.length > 0 && (
                <ul className="browse-results">
                  {items.map((item) => (
                    <li className="browse-result" key={item.id}>
                      <Link to={`/r/${item.type}/${item.slug}`} className="browse-result-link">
                        <span className="browse-result-name">{item.name}</span>
                        {item.citation && (
                          <span className="browse-result-citation">{item.citation}</span>
                        )}
                      </Link>
                      <div className="browse-result-facets">
                        {Object.entries(item.facets).map(([field, values]) => (
                          <span className="browse-result-facet" key={field}>
                            {values.join(", ")}
                          </span>
                        ))}
                      </div>
                    </li>
                  ))}
                </ul>
              )}

              <Pagination
                page={page}
                pageSize={pageSize}
                total={total}
                onPageChange={handlePageChange}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}

function Pagination({ page, pageSize, total, onPageChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <nav className="pagination" aria-label="Pagination">
      <button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
        Previous
      </button>
      <span className="pagination-status">
        Page {page} of {totalPages} ({total} total)
      </span>
      <button type="button" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>
        Next
      </button>
    </nav>
  );
}
