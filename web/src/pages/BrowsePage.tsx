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

/** Query params that aren't filter fields -- excluded from the facet
 * request (which "accepts the same filter params") and from the
 * checkbox-selected-state map. */
const RESERVED_PARAMS = new Set(["sort", "page", "page_size"]);

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

/** The `/browse/:type` route (spec 4.10, batch B9): a facet sidebar
 * (`FacetSidebar`, generated from `GET /facets/{type}`), a sort control, a
 * paginated result list linking to `/r/:type/:slug`, and pagination
 * controls. All of it -- filters, sort, page -- lives in the URL's query
 * string via `useSearchParams`, so reload/back/forward restore the view.
 *
 * Refetching (on any filter/sort/page change) keeps the previous facets and
 * results mounted -- and the checkboxes checked according to the URL,
 * which already reflects the change -- while the new page loads, showing a
 * "Loading…" line alongside rather than tearing the sidebar down and
 * rebuilding it (which briefly drops a just-checked checkbox from the DOM
 * entirely). */
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
    Promise.all([
      browseRecords(type, searchParams, controller.signal),
      getFacets(type, filterParams(searchParams), controller.signal),
    ])
      .then(([browse, facetsResponse]) => {
        setItems(browse.items);
        setTotal(browse.total);
        setPage(browse.page);
        setPageSize(browse.page_size);
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
  }, [type, searchKey]);

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

  const sortValue = searchParams.get("sort") ?? "name";
  const sortableFields = schemaFields.filter((f) => f["x-ui"]?.sortable);

  return (
    <div className="browse-page">
      <h1>{typeLabel}</h1>
      <div className="browse-layout">
        <FacetSidebar facets={facets} selected={selected} onToggle={handleToggle} />
        <div className="browse-main">
          <div className="browse-controls">
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
          </div>

          {loading && <p className="search-status">Loading…</p>}
          {errorMessage && <p className="search-status search-error">{errorMessage}</p>}
          {!loading && !errorMessage && items.length === 0 && (
            <p className="search-status">No results.</p>
          )}

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

          <Pagination page={page} pageSize={pageSize} total={total} onPageChange={handlePageChange} />
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
