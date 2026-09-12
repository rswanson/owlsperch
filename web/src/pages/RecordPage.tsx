import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, getRecord, getSchemas, type RecordDetail, type SchemaField } from "../api";
import { FieldGroups } from "../components/FieldGroups";
import { NotFoundPage } from "./NotFoundPage";

type LoadState =
  | { status: "loading" }
  | { status: "not-found" }
  | { status: "error"; message: string }
  | { status: "ok"; record: RecordDetail; schemaFields: SchemaField[]; typeLabel: string };

/** The `/r/:type/:slug` route (spec 4.10): fetches the record and the type
 * registry, then renders name, citation, type badge, `text_md` as Markdown
 * (react-markdown + remark-gfm, no raw HTML), and field groups ordered by
 * the schema's `x-ui` hints. */
export function RecordPage() {
  const { type, slug } = useParams<{ type: string; slug: string }>();
  const [state, setState] = useState<LoadState>({ status: "loading" });

  useEffect(() => {
    if (!type || !slug) return;
    const controller = new AbortController();
    setState({ status: "loading" });

    Promise.all([getRecord(type, slug, controller.signal), getSchemas(controller.signal)])
      .then(([record, schemas]) => {
        const typeInfo = schemas.types[type];
        setState({
          status: "ok",
          record,
          schemaFields: typeInfo?.fields ?? [],
          typeLabel: typeInfo?.label ?? type,
        });
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError && err.status === 404) {
          setState({ status: "not-found" });
        } else {
          setState({
            status: "error",
            message: err instanceof Error ? err.message : "Failed to load record.",
          });
        }
      });

    return () => controller.abort();
  }, [type, slug]);

  if (state.status === "loading") {
    return <p className="search-status">Loading…</p>;
  }
  if (state.status === "not-found") {
    return <NotFoundPage />;
  }
  if (state.status === "error") {
    return <p className="search-status search-error">{state.message}</p>;
  }

  const { record, schemaFields, typeLabel } = state;

  return (
    <article className="record-page">
      <Link to="/" className="back-link">
        ← Back to search
      </Link>
      <div className="record-heading">
        <span className="type-badge">{typeLabel}</span>
        <h1>{record.name}</h1>
        {record.citation && <p className="citation">{record.citation}</p>}
      </div>
      <FieldGroups fields={record.fields} schemaFields={schemaFields} />
      <div className="text-md">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{record.text_md}</ReactMarkdown>
      </div>
    </article>
  );
}
