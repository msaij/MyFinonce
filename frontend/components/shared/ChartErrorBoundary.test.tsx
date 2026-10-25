import React from "react";
import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChartErrorBoundary } from "./ChartErrorBoundary";

// vitest compiles JSX with the classic runtime, and the app's components (written for
// Next's automatic runtime) do not import React themselves.
(globalThis as { React?: typeof React }).React = React;

function FailedChart(): never {
  throw new Error("ChunkLoadError: Loading chunk 812 failed.");
}

describe("ChartErrorBoundary", () => {
  let container: HTMLDivElement;
  let root: Root;
  // React 18 in development re-raises a caught render error on window; keep it out of the run.
  const swallow = (e: ErrorEvent) => e.preventDefault();

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    window.addEventListener("error", swallow);
    vi.spyOn(console, "error").mockImplementation(() => {});
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    window.removeEventListener("error", swallow);
    vi.restoreAllMocks();
  });

  it("shows a reload note in place of a chart that fails, and keeps the rest of the page", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    act(() =>
      root.render(
        <div>
          <span>EMI tiles</span>
          <ChartErrorBoundary>
            <FailedChart />
          </ChartErrorBoundary>
        </div>
      )
    );
    expect(container.textContent).toContain("EMI tiles");
    expect(container.textContent).toContain("Chart couldn't load.");
    expect(container.querySelector("button")?.textContent).toBe("Reload");
    expect(warn).toHaveBeenCalled();
  });

  it("renders the chart untouched when nothing fails", () => {
    act(() =>
      root.render(
        <ChartErrorBoundary>
          <span>chart</span>
        </ChartErrorBoundary>
      )
    );
    expect(container.textContent).toBe("chart");
  });
});
