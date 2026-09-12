import { useEffect, useState } from "react";
import { SearchBox } from "../components/SearchBox";
import { getHealth, getSchemas, getStats } from "../api";

type DbStatus = "checking" | "empty" | "ready" | "unknown";

interface TypeCount {
  type: string;
  label: string;
  count: number;
}

/** The `/` route (spec 4.10): a search box, plus a small hint of how many
 * records of each type are loaded (from `/api/schemas` + `/api/stats`).
 * When the database hasn't been built yet -- `/api/health`'s `db: false` --
 * shows an empty state naming the build command instead (acceptance
 * criterion 4), rather than a search box against no data. */
export function HomePage() {
  const [dbStatus, setDbStatus] = useState<DbStatus>("checking");
  const [typeCounts, setTypeCounts] = useState<TypeCount[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then((health) => {
        setDbStatus(health.db ? "ready" : "empty");
      })
      .catch(() => {
        setDbStatus("unknown");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (dbStatus !== "ready") return;
    const controller = new AbortController();
    Promise.all([getSchemas(controller.signal), getStats(controller.signal)])
      .then(([schemas, stats]) => {
        const counts = Object.entries(schemas.types)
          .map(([type, info]) => ({
            type,
            label: info.plural_label,
            count: stats.counts[type] ?? 0,
          }))
          .filter((entry) => entry.count > 0)
          .sort((a, b) => a.label.localeCompare(b.label));
        setTypeCounts(counts);
      })
      .catch(() => {
        setTypeCounts([]);
      });
    return () => controller.abort();
  }, [dbStatus]);

  if (dbStatus === "checking") {
    return <p className="search-status">Loading…</p>;
  }

  if (dbStatus === "empty") {
    return (
      <div className="empty-state">
        <h1>No data yet</h1>
        <p>
          The database hasn&apos;t been built. Run <code>uv run owlsperch build-db</code> and
          reload this page.
        </p>
      </div>
    );
  }

  return (
    <div className="home">
      <h1>owlsperch</h1>
      <SearchBox />
      {typeCounts.length > 0 && (
        <p className="type-counts-hint">
          {typeCounts.map((entry, index) => (
            <span key={entry.type}>
              {index > 0 && ", "}
              {entry.count} {entry.label}
            </span>
          ))}
        </p>
      )}
    </div>
  );
}
