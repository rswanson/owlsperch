import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import "@testing-library/jest-dom/vitest";

// We don't enable vitest's `globals` option (tests import describe/it/expect
// explicitly), so @testing-library/react's automatic per-test cleanup --
// which only self-registers when it detects Jest-style globals -- needs to
// be wired up by hand here instead.
afterEach(() => {
  cleanup();
});
