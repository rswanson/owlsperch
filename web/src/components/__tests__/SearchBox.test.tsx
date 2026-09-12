import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { SearchBox } from "../SearchBox";

function renderSearchBox() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<SearchBox />} />
        <Route path="/r/:type/:slug" element={<div>RECORD PAGE</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Like `renderSearchBox`, but the `/r/:type/:slug` route also surfaces the
 * navigated-to path via a `data-testid="location"` element, so a test can
 * assert *which* record was navigated to, not just that navigation happened. */
function renderSearchBoxWithLocation() {
  function LocationProbe() {
    const location = useLocation();
    return <div data-testid="location">{location.pathname}</div>;
  }
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<SearchBox />} />
        <Route path="/r/:type/:slug" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Advances fake timers *and* flushes the resulting promise microtasks and
 * React state updates within `act`, so assertions right after this resolve
 * can check the post-update DOM directly (no `waitFor` -- which polls using
 * `setTimeout` itself, and deadlocks against fake timers that nothing else
 * is advancing). */
async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const GROUPS_RESPONSE: api.SearchResponse = {
  groups: [
    {
      type: "spell",
      label: "Spells",
      hits: [
        {
          id: "spell:phb1:fireball",
          type: "spell",
          name: "Fireball",
          slug: "fireball",
          citation: "PHB p. 172",
          book_id: "phb1",
        },
        {
          id: "spell:phb1:fire-storm",
          type: "spell",
          name: "Fire Storm",
          slug: "fire-storm",
          citation: "PHB p. 173",
          book_id: "phb1",
        },
      ],
    },
  ],
};

describe("SearchBox", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("does not query before 2 characters", async () => {
    const searchSpy = vi.spyOn(api, "search").mockResolvedValue({ groups: [] });
    renderSearchBox();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "f" } });
    await tick(200);
    expect(searchSpy).not.toHaveBeenCalled();
  });

  it("debounces: rapid keystrokes within 150ms only trigger one request", async () => {
    const searchSpy = vi.spyOn(api, "search").mockResolvedValue(GROUPS_RESPONSE);
    renderSearchBox();
    const input = screen.getByRole("combobox");

    fireEvent.change(input, { target: { value: "fi" } });
    await tick(60);
    fireEvent.change(input, { target: { value: "fir" } });
    await tick(60);
    fireEvent.change(input, { target: { value: "fireb" } });

    // Only 60ms elapsed since the last keystroke so far -- no request yet.
    await tick(60);
    expect(searchSpy).not.toHaveBeenCalled();

    // Now past the full 150ms debounce window since the last keystroke.
    await tick(100);
    expect(searchSpy).toHaveBeenCalledTimes(1);
    expect(searchSpy).toHaveBeenCalledWith("fireb", expect.anything());
  });

  it("cancels an in-flight request when the query changes again", async () => {
    let firstSignal: AbortSignal | undefined;
    vi.spyOn(api, "search").mockImplementation((_query, signal) => {
      if (!firstSignal) firstSignal = signal;
      return new Promise(() => {
        /* never resolves */
      });
    });
    renderSearchBox();
    const input = screen.getByRole("combobox");

    fireEvent.change(input, { target: { value: "fireb" } });
    await tick(150);

    fireEvent.change(input, { target: { value: "fireba" } });
    await tick(150);

    expect(firstSignal?.aborted).toBe(true);
  });

  it("groups results by type with a type badge and citation", async () => {
    vi.spyOn(api, "search").mockResolvedValue(GROUPS_RESPONSE);
    renderSearchBox();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "fireb" } });
    await tick(150);

    expect(screen.getByText("Fireball")).toBeInTheDocument();
    expect(screen.getByText("Fire Storm")).toBeInTheDocument();
    expect(screen.getByText("Spells")).toBeInTheDocument();
    expect(screen.getByText("PHB p. 172")).toBeInTheDocument();
    expect(screen.getAllByText("spell").length).toBeGreaterThan(0);
  });

  it("shows a 'no matches' state when the search returns no groups", async () => {
    vi.spyOn(api, "search").mockResolvedValue({ groups: [] });
    renderSearchBox();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "zzzz" } });
    await tick(150);

    expect(screen.getByText("No matches.")).toBeInTheDocument();
  });

  it("shows an error state when the search request fails", async () => {
    vi.spyOn(api, "search").mockRejectedValue(new api.ApiError(500, "boom"));
    renderSearchBox();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "fireb" } });
    await tick(150);

    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("supports ArrowDown/ArrowUp/Enter to navigate to a hit", async () => {
    vi.spyOn(api, "search").mockResolvedValue(GROUPS_RESPONSE);
    renderSearchBox();
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "fireb" } });
    await tick(150);
    expect(screen.getByText("Fireball")).toBeInTheDocument();

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowUp" });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(screen.getByText("RECORD PAGE")).toBeInTheDocument();
  });

  it("Enter with no arrow key navigation defaults to the first hit", async () => {
    vi.spyOn(api, "search").mockResolvedValue(GROUPS_RESPONSE);
    renderSearchBox();
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "fireb" } });
    await tick(150);
    expect(screen.getByText("Fireball")).toBeInTheDocument();

    fireEvent.keyDown(input, { key: "Enter" });

    expect(screen.getByText("RECORD PAGE")).toBeInTheDocument();
  });

  it("sets aria-activedescendant to the active option's id, matching ResultGroup's option ids", async () => {
    vi.spyOn(api, "search").mockResolvedValue(GROUPS_RESPONSE);
    renderSearchBox();
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "fireb" } });
    await tick(150);

    expect(input).not.toHaveAttribute("aria-activedescendant");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    const firstOption = screen.getByRole("option", { name: /Fireball/ });
    expect(firstOption).toHaveAttribute("id", "opt-spell-fireball");
    expect(input).toHaveAttribute("aria-activedescendant", "opt-spell-fireball");
    expect(firstOption).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    const secondOption = screen.getByRole("option", { name: /Fire Storm/ });
    expect(input).toHaveAttribute("aria-activedescendant", "opt-spell-fire-storm");
    expect(secondOption).toHaveAttribute("aria-selected", "true");
    expect(firstOption).toHaveAttribute("aria-selected", "false");
  });

  it("Enter pressed inside the debounce window navigates using fresh results, not the previous query's", async () => {
    const FI_RESPONSE: api.SearchResponse = {
      groups: [
        {
          type: "spell",
          label: "Spells",
          hits: [
            {
              id: "spell:phb1:fire-shield",
              type: "spell",
              name: "Fire Shield",
              slug: "fire-shield",
              citation: "PHB p. 175",
              book_id: "phb1",
            },
          ],
        },
      ],
    };

    vi.spyOn(api, "search").mockImplementation((q: string) => {
      if (q === "fi") return Promise.resolve(FI_RESPONSE);
      if (q === "fireb") return Promise.resolve(GROUPS_RESPONSE);
      throw new Error(`unexpected query ${q}`);
    });

    renderSearchBoxWithLocation();
    const input = screen.getByRole("combobox");

    fireEvent.change(input, { target: { value: "fi" } });
    await tick(150);
    expect(screen.getByText("Fire Shield")).toBeInTheDocument();

    // Type more, then press Enter *before* the new debounce fires -- the
    // rendered results (Fire Shield, from "fi") are now stale for "fireb".
    fireEvent.change(input, { target: { value: "fireb" } });
    fireEvent.keyDown(input, { key: "Enter" });

    // Enter's immediate (non-debounced) fetch is a plain resolved promise,
    // not scheduled via a fake timer -- flush its microtask chain directly
    // rather than advancing fake time.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByTestId("location")).toHaveTextContent("/r/spell/fireball");
  });

  it("Escape clears the query and results", async () => {
    vi.spyOn(api, "search").mockResolvedValue(GROUPS_RESPONSE);
    renderSearchBox();
    const input = screen.getByRole("combobox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "fireb" } });
    await tick(150);
    expect(screen.getByText("Fireball")).toBeInTheDocument();

    fireEvent.keyDown(input, { key: "Escape" });
    expect(input.value).toBe("");
    expect(screen.queryByText("Fireball")).not.toBeInTheDocument();
  });
});
