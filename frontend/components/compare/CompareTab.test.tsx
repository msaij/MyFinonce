import React from "react";
import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CompareResult } from "@/lib/api/compare";

// vitest compiles JSX with the classic runtime, and the app's components (written for
// Next's automatic runtime) do not import React themselves.
(globalThis as { React?: typeof React }).React = React;

vi.mock("@/components/shared/PlotlyChart", () => ({ PlotlyChart: () => <div data-testid="chart" /> }));

const fixture: CompareResult = {
  requested: { start: "2026-06-26", end: "2026-09-24", days: 90 },
  common: { start: "2026-06-26", end: "2026-09-23", days: 89, coverage_pct: 98.89, limited_start_by: [], limited_end_by: [122639], fund_count: 2 },
  rf_pct: 6.5,
  funds: [
    {
      scheme_code: 122639,
      display_name: "Parag Parikh Flexi Cap Fund (Direct - Growth) [122639]",
      status: "ok",
      expense_ratio: 0.69,
      ter_status: "official",
      is_idcw: false,
      series: [["2026-06-25", 89.7127], ["2026-09-23", 90.3403]],
      common: { start_date: "2026-06-25", start_nav: 89.7127, end_date: "2026-09-23", end_nav: 90.3403, return_pct: 0.6996, cagr_pct: null, days: 90 },
      risk: { vol_ann_pct: 7.8198, obs_per_year: 250, max_drawdown_pct: -4.5872, max_drawdown_peak_date: "2026-08-03", max_drawdown_trough_date: "2026-09-15", sharpe: -0.4075, sortino: -0.6532, n_returns: 62, short_sample: false },
      cost: { fee_pct_of_initial: 0.1726, avg_ter_pct: 0.6912, history_coverage_pct: 100, basis: "history" },
      peer: { peer_group: "Flexi Cap Fund - Direct", n_1y: 42, median_1y_pct: 1.1461, rank_1y: 36, n_3y: 38, median_3y_cagr_pct: 11.5793, rank_3y: 16, n_ter: 48, median_ter_pct: 0.915, rank_note: null },
    },
    {
      scheme_code: 153964,
      display_name: "Parag Parikh Flexi Cap Fund (Direct - IDCW) [153964]",
      status: "ok",
      expense_ratio: 0.7,
      ter_status: "official",
      is_idcw: true,
      series: [["2026-06-25", 20.1], ["2026-09-23", 20.5]],
      common: { start_date: "2026-06-25", start_nav: 20.1, end_date: "2026-09-23", end_nav: 20.5, return_pct: 1.99, cagr_pct: null, days: 90 },
      peer: { peer_group: "Flexi Cap Fund - Direct", n_1y: 42, median_1y_pct: 1.1461, rank_1y: null, n_3y: 38, median_3y_cagr_pct: 11.5793, rank_3y: null, n_ter: 48, median_ter_pct: 0.915, rank_note: "IDCW plan" },
    },
  ],
  correlation: { codes: [122639, 153964], matrix: [[1, 0.9], [0.9, 1]], n_obs: [[62, 62], [62, 62]], min_obs: 20 },
  limits: { max_funds: 8, anchor_max_gap_days: 10, stale_after_days: 30, short_sample_returns: 60, rolling_days: 365 },
};

vi.mock("@/lib/api/compare", () => ({ getCompare: vi.fn(async () => fixture) }));

import { CompareTab } from "./CompareTab";

describe("CompareTab", () => {
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

  it("renders the common period, full fund names, 4-decimal NAV and unsigned 4-decimal TER, and the IDCW warning", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <CompareTab codes={[122639, 153964]} start="2026-06-26" end="2026-09-24" amount={10000} onAmountChange={() => {}} />
        </QueryClientProvider>
      );
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    const text = container.textContent ?? "";
    expect(text).toMatch(/26 Jun 2026 to 23 Sept? 2026/);
    expect(text).toContain("+0.6996%");
    expect(text).toContain("-4.5872%");
    expect(text).toContain("Parag Parikh Flexi Cap Fund (Direct - Growth) [122639]");
    expect(text).toContain("89.7127");
    expect(text).toContain("0.6900%");
    expect(text).not.toContain("+0.6900%"); // a TER is a cost: never signed
    expect(text).toContain("is an IDCW plan");
    expect(text).toContain("n/a (IDCW)");
    expect(text).toContain("36 of 42");
    expect(container.querySelector("[data-testid=chart]")).not.toBeNull();
  });
});
