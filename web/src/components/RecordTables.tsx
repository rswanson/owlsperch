import type { RecordDetail, RecordTable } from "../api";

interface RecordTablesProps {
  tables: RecordTable[];
}

/** Builds the `RecordTable` a `type === "table"` record's OWN grid renders
 * as (spec 4.10, batch B10): a table record's grid lives in its own
 * `fields.caption`/`fields.columns`/`fields.rows`, not in a `tables` id
 * list pointing at some other record, so `RecordPage` prepends this to
 * whatever `record.tables` already resolved. A pure function (not inlined
 * in the page) so it's covered by a plain unit test without rendering. */
export function buildOwnTable(record: RecordDetail): RecordTable {
  const fields = record.fields as {
    caption?: unknown;
    columns?: unknown;
    rows?: unknown;
  };
  const columns = Array.isArray(fields.columns) ? (fields.columns as string[]) : [];
  const rows = Array.isArray(fields.rows) ? (fields.rows as string[][]) : [];
  const caption = typeof fields.caption === "string" ? fields.caption : null;

  return {
    id: record.id,
    pending: false,
    name: record.name,
    slug: record.slug,
    caption,
    columns,
    rows,
    citation: record.citation,
    book_id: record.book_id,
  };
}

/** Renders every table a record owns (spec 4.6 `tables`, resolved by `GET
 * /records/{type}/{slug}`) as an HTML table below the record's text: a
 * `<caption>` (the table's own caption, falling back to its name), a
 * `<thead>` of its column headers, and a `<tbody>` of its rows. A `pending:
 * true` entry (spec edge case: "Record references a table that failed")
 * renders a "Table pending" marker naming the id instead of an empty grid.
 * Renders nothing at all when `tables` is empty. */
export function RecordTables({ tables }: RecordTablesProps) {
  if (tables.length === 0) return null;

  return (
    <div className="record-tables">
      {tables.map((table) => (
        <div className="record-table" key={table.id}>
          {table.pending ? (
            <p className="table-pending">Table pending ({table.id})</p>
          ) : (
            <div className="record-table-scroll">
              <table>
                <caption>{table.caption ?? table.name}</caption>
                <thead>
                  <tr>
                    {table.columns.map((column, i) => (
                      <th key={i}>{column}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
