import React from "react";
import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";

import { QueryStatus } from "./QueryStatus";

// vitest compiles JSX with the classic runtime, and the app's components (written for
// Next's automatic runtime) do not import React themselves.
(globalThis as { React?: typeof React }).React = React;

describe("QueryStatus", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("shows nothing when the query has not failed", () => {
    act(() => root.render(<QueryStatus what="market stats" query={{ error: null, data: { a: 1 }, refetch: () => {} }} />));
    expect(container.innerHTML).toBe("");
  });

  it("says a failed load is a failure, not an empty market, and offers a retry", () => {
    const refetch = vi.fn();
    act(() => root.render(<QueryStatus what="market stats" query={{ error: new ApiError(503, "Database unavailable"), data: undefined, refetch }} />));
    const banner = container.querySelector(".mf-banner-danger");
    expect(banner?.textContent).toContain("Couldn't load market stats: Database unavailable (HTTP 503)");
    act(() => container.querySelector("button")!.click());
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("marks figures still on screen as the last ones that loaded", () => {
    act(() => root.render(<QueryStatus what="leaders" query={{ error: new ApiError(500, "boom"), data: { rows: [] }, refetch: () => {} }} />));
    expect(container.querySelector(".mf-banner-danger")).toBeNull();
    expect(container.querySelector(".mf-banner-warning")?.textContent).toContain("Showing the last leaders that loaded; the refresh failed: boom (HTTP 500)");
  });

  it("disables the retry while one is in flight", () => {
    act(() => root.render(<QueryStatus what="x" query={{ error: new Error("down"), data: undefined, isFetching: true, refetch: () => {} }} />));
    const button = container.querySelector("button")!;
    expect(button.disabled).toBe(true);
    expect(button.textContent).toBe("Retrying…");
  });
});
