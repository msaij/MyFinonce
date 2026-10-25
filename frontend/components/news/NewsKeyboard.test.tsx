import React from "react";
import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { NewsResponse } from "./types";

// vitest compiles JSX with the classic runtime, and the app's components (written for
// Next's automatic runtime) do not import React themselves.
(globalThis as { React?: typeof React }).React = React;

const response: NewsResponse = {
  status: "ok",
  total: 1,
  count: 1,
  is_offline: false,
  is_stale: false,
  articles: [
    {
      id: "a1",
      title: "Fed holds rates steady",
      summary: "The FOMC kept the target range unchanged.",
      source: "Federal Reserve",
      url: "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261014a.htm",
      category: "central_banks",
      region: "us",
      published_at: new Date(Date.now() - 3_600_000).toISOString(),
      date_only: false,
      sentiment: "neutral",
      keywords: ["FED"],
      fetched_at: new Date().toISOString(),
      paywall: null,
      official: true,
      related: [],
    },
  ],
  last_updated: null,
  cached_at: null,
  retention_days: 30,
  last_sweep_at: new Date().toISOString(),
  last_sweep_ok_at: new Date().toISOString(),
  feeds_ok: 20,
  feeds_total: 20,
  feeds_stale: 0,
  feeds_failing: 0,
  feeds_pending: 0,
};

vi.mock("@/lib/api/news", () => ({
  useGlobalNews: () => ({ data: response, isLoading: false, isError: false, error: null, refetch: vi.fn(), isPlaceholderData: false }),
  useNewsSources: () => ({ data: undefined, isLoading: false, error: null }),
  refreshGlobalNews: vi.fn(),
}));

import { BasicCalculator } from "@/components/calculator/BasicCalculator";

describe("calculator keys and the news feed beside it", () => {
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

  const expression = () => (container.querySelector('input[aria-label="Calculator expression"]') as HTMLInputElement).value;
  const press = (target: EventTarget, key: string) =>
    act(() => {
      target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
    });
  const historyIsEmpty = () => (container.textContent ?? "").includes("Your calculations appear here");

  it("Enter, digits and Escape aimed at the news UI do not drive the calculator", async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <BasicCalculator />
        </QueryClientProvider>
      );
    });

    for (const key of ["2", "+", "2"]) press(document.body, key);
    expect(expression()).toBe("2+2");

    // Enter on a focused story used to open it AND press "=".
    const card = container.querySelector('button[aria-label^="Preview:"]') as HTMLButtonElement;
    card.focus();
    press(card, "Enter");
    expect(expression()).toBe("2+2");
    expect(historyIsEmpty()).toBe(true);

    act(() => card.click());
    expect(document.querySelector('[role="dialog"]')).not.toBeNull();

    // Typing while the modal preview is open must not edit the hidden calculator.
    press(document.body, "5");
    press(document.body, "Backspace");
    expect(expression()).toBe("2+2");

    // Escape closes the preview and no longer also clears the calculator.
    press(document.activeElement ?? document.body, "Escape");
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(expression()).toBe("2+2");
    expect(historyIsEmpty()).toBe(true);

    // And the calculator still takes keys from the rest of the page.
    press(document.body, "5");
    expect(expression()).toBe("2+25");
  });

  it("the preview takes focus on open and gives it back on close", async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <BasicCalculator />
        </QueryClientProvider>
      );
    });
    const card = container.querySelector('button[aria-label^="Preview:"]') as HTMLButtonElement;
    card.focus();
    act(() => card.click());
    expect(document.activeElement?.getAttribute("aria-label")).toBe("Close article preview");
    press(document.activeElement ?? document.body, "Escape");
    expect(document.activeElement).toBe(card);
  });
});
