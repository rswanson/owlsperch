import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, search, type SearchGroup, type SearchHit } from "../api";
import { optionId, ResultGroup } from "./ResultGroup";

export const SEARCH_DEBOUNCE_MS = 150;
export const MIN_QUERY_LENGTH = 2;

type Status = "idle" | "loading" | "ok" | "error";

/** The `/` route's search box (spec 4.10): autofocused, queries `/api/search`
 * after `MIN_QUERY_LENGTH` characters with a `SEARCH_DEBOUNCE_MS` debounce
 * and in-flight request cancellation, groups hits by type, and supports
 * ArrowUp/ArrowDown/Enter/Escape keyboard navigation. */
export function SearchBox() {
  const [query, setQuery] = useState("");
  const [groups, setGroups] = useState<SearchGroup[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  // The (trimmed) query string `groups` was fetched for -- used to detect
  // Enter pressed against stale results from a previous, since-superseded
  // debounce (finding 7): if the current input no longer matches this, the
  // rendered hits don't belong to what the user is looking at right now.
  const [resultsQuery, setResultsQuery] = useState("");

  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const navigate = useNavigate();

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const flatHits = useMemo<SearchHit[]>(() => groups.flatMap((group) => group.hits), [groups]);

  useEffect(() => {
    if (timerRef.current !== undefined) clearTimeout(timerRef.current);
    abortRef.current?.abort();

    const trimmed = query.trim();
    if (trimmed.length < MIN_QUERY_LENGTH) {
      setGroups([]);
      setStatus("idle");
      setActiveIndex(-1);
      setResultsQuery("");
      return;
    }

    timerRef.current = setTimeout(() => {
      const controller = new AbortController();
      abortRef.current = controller;
      setStatus("loading");
      search(query, controller.signal)
        .then((response) => {
          setGroups(response.groups);
          setStatus("ok");
          setActiveIndex(-1);
          setResultsQuery(trimmed);
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
          setGroups([]);
          setStatus("error");
          setErrorMessage(err instanceof ApiError ? err.message : "Search failed.");
        });
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      if (timerRef.current !== undefined) clearTimeout(timerRef.current);
    };
  }, [query]);

  function navigateToHit(hit: SearchHit) {
    navigate(`/r/${hit.type}/${hit.slug}`);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (flatHits.length > 0) setActiveIndex((i) => Math.min(i + 1, flatHits.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      if (flatHits.length > 0) setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const trimmed = query.trim();
      if (trimmed.length < MIN_QUERY_LENGTH) return;

      if (trimmed === resultsQuery) {
        // The rendered results are fresh -- honor the highlighted hit (or
        // the first one, if the user hasn't arrowed).
        const hit = flatHits[activeIndex] ?? flatHits[0];
        if (hit) navigateToHit(hit);
        return;
      }

      // The rendered results (if any) belong to a previous, superseded
      // query -- don't navigate from them. Cancel the pending debounce and
      // any in-flight request, fetch for the current input right away, and
      // navigate to that response's first hit once it lands.
      if (timerRef.current !== undefined) clearTimeout(timerRef.current);
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setStatus("loading");
      search(query, controller.signal)
        .then((response) => {
          setGroups(response.groups);
          setStatus("ok");
          setActiveIndex(-1);
          setResultsQuery(trimmed);
          const hit = response.groups.flatMap((group) => group.hits)[0];
          if (hit) navigateToHit(hit);
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
          setGroups([]);
          setStatus("error");
          setErrorMessage(err instanceof ApiError ? err.message : "Search failed.");
        });
    } else if (event.key === "Escape") {
      event.preventDefault();
      setQuery("");
      setGroups([]);
      setStatus("idle");
      setActiveIndex(-1);
      setResultsQuery("");
    }
  }

  const activeHit = flatHits[activeIndex] ?? null;

  return (
    <div className="search-box">
      <input
        ref={inputRef}
        type="text"
        className="search-input"
        placeholder="Search spells…"
        aria-label="Search"
        autoFocus
        autoComplete="off"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
        }}
        onKeyDown={handleKeyDown}
        role="combobox"
        aria-expanded={groups.length > 0}
        aria-controls="search-results"
        aria-activedescendant={activeHit ? optionId(activeHit) : undefined}
      />
      <div aria-live="polite">
        {status === "loading" && <p className="search-status">Searching…</p>}
        {status === "error" && <p className="search-status search-error">{errorMessage}</p>}
        {status === "ok" && groups.length === 0 && <p className="search-status">No matches.</p>}
      </div>
      {groups.length > 0 && (
        <ul className="results" id="search-results" role="listbox">
          {groups.map((group) => (
            <ResultGroup
              key={group.type}
              group={group}
              activeHit={activeHit}
              onHover={(hit) => {
                setActiveIndex(flatHits.findIndex((h) => h.id === hit.id));
              }}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
