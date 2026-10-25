import React from "react";
import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";

(globalThis as { React?: typeof React }).React = React;

vi.mock("@/components/shared/PlotlyChart", () => ({ PlotlyChart: () => <div data-testid="chart" /> }));
vi.mock("@/components/shared/SearchCombobox", () => ({ SearchCombobox: () => <input aria-label="fund search" /> }));

const api = vi.hoisted(() => ({
  listSipMandates: vi.fn(),
  listGoals: vi.fn(),
  getGoalStatus: vi.fn(),
  createSipMandate: vi.fn(),
  updateSipMandate: vi.fn(),
  generateInstalments: vi.fn(),
  saveGoal: vi.fn(),
  deleteGoal: vi.fn(),
}));
vi.mock("@/lib/api/holdings", () => api);

import { PlanningPanel } from "./PlanningPanel";

describe("PlanningPanel", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    Object.values(api).forEach((f) => f.mockReset());
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const render = async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <PlanningPanel pid={1} portfolios={[{ id: 1, name: "Self", archived: false }] as never[]} />
        </QueryClientProvider>
      );
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  };

  it("says a failed load failed, never 'No SIPs recorded' -- recording the SIP again would offer its months twice", async () => {
    api.listSipMandates.mockRejectedValue(new ApiError(503, "Database unavailable or timed out. Try again shortly."));
    api.listGoals.mockRejectedValue(new ApiError(503, "Database unavailable or timed out. Try again shortly."));
    await render();
    const text = container.textContent ?? "";
    expect(text).toContain("Couldn't load your SIPs");
    expect(text).toContain("Couldn't load your goals");
    expect(text).not.toContain("No SIPs recorded");
    expect(text).not.toContain("No goals yet");
    expect([...container.querySelectorAll("button")].some((b) => b.textContent === "Retry")).toBe(true);
  });

  it("shows the empty states only once the lists have loaded empty", async () => {
    api.listSipMandates.mockResolvedValue([]);
    api.listGoals.mockResolvedValue([]);
    await render();
    expect(container.textContent).toContain("No SIPs recorded for this view.");
    expect(container.textContent).toContain("No goals yet");
  });

  it("says when pausing a SIP failed instead of doing nothing", async () => {
    api.listSipMandates.mockResolvedValue([{
      id: 5, portfolio_id: 1, scheme_code: 1001, amount: "5000.00", day_of_month: 6, start_date: "2024-01-01", end_date: null,
      step_up_pct: "0", active: true, notes: null, scheme_name: "Alpha Fund", current_amount: 5000, instalments_recorded: 3,
      instalments_pending: 0, upcoming: [], paused_since: null,
    }]);
    api.listGoals.mockResolvedValue([]);
    api.updateSipMandate.mockRejectedValue(new ApiError(503, "Database unavailable or timed out. Try again shortly."));
    await render();
    const pause = [...container.querySelectorAll("button")].find((b) => b.textContent === "Pause")!;
    await act(async () => pause.click());
    expect(container.textContent).toContain("Couldn't pause the SIP");
  });
});
