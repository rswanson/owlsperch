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
  tables: unknown[];
  canonical: boolean;
  variant_of: string | null;
  applied_overrides: unknown[];
  macro_eligible: boolean;
  schema_version: number;
  extraction: Record<string, unknown>;
  variants: string[];
  links: unknown[];
  referenced_by: unknown[];
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
