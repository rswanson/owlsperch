import type { Facet } from "../api";

interface FacetSidebarProps {
  facets: Facet[];
  /** Currently-selected values per facet field, read from the URL query
   * string (repeated params -- OR within a field). */
  selected: Record<string, string[]>;
  onToggle: (field: string, value: string) => void;
}

/** The `/browse/:type` facet sidebar (spec 4.10, batch B9): one checkbox
 * group per facet returned by `GET /facets/{type}`, each option showing its
 * distinct value, count, and (for `source`) the book's title instead of its
 * raw `book_id`. Checked state and toggling are fully controlled by the
 * parent (`BrowsePage`), which keeps it all in the URL query string. */
export function FacetSidebar({ facets, selected, onToggle }: FacetSidebarProps) {
  if (facets.length === 0) return null;

  return (
    <aside className="facet-sidebar" aria-label="Filters">
      {facets.map((facet) => (
        <fieldset className="facet-group" key={facet.field}>
          <legend>{facet.label}</legend>
          {facet.values.length === 0 && <p className="facet-empty">No values.</p>}
          {facet.values.map((facetValue) => {
            const checked = (selected[facet.field] ?? []).includes(facetValue.value);
            const displayLabel = facetValue.label ?? facetValue.value;
            const inputId = `facet-${facet.field}-${facetValue.value}`;
            return (
              <div className="facet-option" key={facetValue.value}>
                <input
                  type="checkbox"
                  id={inputId}
                  checked={checked}
                  onChange={() => {
                    onToggle(facet.field, facetValue.value);
                  }}
                />
                <label htmlFor={inputId}>
                  {displayLabel} <span className="facet-count">({facetValue.count})</span>
                </label>
              </div>
            );
          })}
        </fieldset>
      ))}
    </aside>
  );
}
