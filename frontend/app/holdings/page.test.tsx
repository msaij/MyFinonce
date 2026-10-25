import React from "react";
import { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { HoldingsSummary, Position, Transaction } from "@/lib/api/holdings";
import { ApiError } from "@/lib/api/client";

(globalThis as { React?: typeof React }).React = React;

const nav = vi.hoisted(() => ({ search: "tab=holdings" }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/holdings",
}));
vi.mock("@/components/layout/AppShell", () => ({ AppShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div> }));
vi.mock("@/components/shared/PlotlyChart", () => ({ PlotlyChart: () => <div data-testid="chart" /> }));

const api = vi.hoisted(() => ({
  listPortfolios: vi.fn(),
  getHoldingsSummary: vi.fn(),
  getRecentChanges: vi.fn(),
  getMonthlyEarnings: vi.fn(),
  listTransactions: vi.fn(),
  getBackupStatus: vi.fn(),
  deleteTransaction: vi.fn(),
  restoreTransaction: vi.fn(),
  downloadBackup: vi.fn(),
  exportCsvUrl: vi.fn(() => "/api/holdings/portfolios/all/export.csv"),
  createPortfolio: vi.fn(),
  updatePortfolio: vi.fn(),
  archivePortfolio: vi.fn(),
  restoreBackup: vi.fn(),
  previewTransaction: vi.fn(),
  addTransaction: vi.fn(),
  editTransaction: vi.fn(),
  getPositionDetail: vi.fn(),
}));
vi.mock("@/lib/api/holdings", () => api);

import HoldingsPage from "./page";

const position = (over: Partial<Position>): Position =>
  ({
    scheme_code: 1001, scheme_name: "Alpha Fund", display_name: "Alpha Fund (Direct - Growth) [1001]", fund_house: "Alpha MF",
    category: "Equity Scheme - Flexi Cap Fund", broad_category: "Equity", plan_type: "Direct", option_type: "Growth",
    expense_ratio: 0.6, ter_status: "official", units: 100, avg_cost_nav: 10, cost_basis: 1000, latest_nav: 12,
    latest_date: "2024-12-31", current_value: 1200, unrealised_gain: 200, unrealised_pct: 20, realised_gain: 0,
    dividend_income: 0, stamp_duty: 0, total_invested: 1000, total_redeemed: 0, day_change: 100, day_base: 50000,
    prev_nav_date: "2024-12-30", xirr_pct: 12, xirr_note: null, xirr_available_on: null, weight_pct: 100, sebi_category: "Flexi Cap Fund",
    asset_class: "Equity", riskometer: null, annual_fee: 7.2, first_date: "2024-01-02", txn_count: 1, portfolio_ids: [1],
    is_closed: false, flags: { stale_nav: false, regular_plan: false, split_adjusted: false }, ...over,
  }) as Position;

const summary = (over: Partial<HoldingsSummary["kpis"]> = {}): HoldingsSummary => ({
  portfolio_ids: [1],
  as_of: "2024-12-31",
  positions: [
    position({}),
    // A full switch-out allotted at the latest NAV: closed, yet held through the day's move.
    position({ scheme_code: 1002, display_name: "Beta Fund [1002]", is_closed: true, units: 0, current_value: 0, cost_basis: 0, day_change: -30, day_base: 20000 }),
  ],
  kpis: {
    current_value: 1200, invested: 1000, unrealised_gain: 200, unrealised_pct: 20, realised_gain: 0, dividend_income: 0, stamp_duty: 0,
    total_gain: 200, total_gain_pct: 20, net_contributed: 1000, xirr_pct: 12, xirr_note: null, xirr_available_on: null,
    first_investment_date: "2024-01-02", avg_days_invested: 300, days_since_first_investment: 364, twr_since_start_pct: 20,
    benchmark_since_start_pct: 18, excess_since_start_pp: 2, benchmark_name: null, day_change: 70, day_change_pct: 0.1,
    day_benchmark_pct: null, weighted_ter_pct: 0.6, annual_fee: 7.2, ter_coverage_pct: 100, open_positions: 1, transactions: 2,
    ledger_errors: [], unpriced_funds: 0, funds_without_day_change: 0, ...over,
  },
});

const buy = { id: 7, portfolio_id: 1, scheme_code: 1001, txn_type: "BUY", trade_date: "2024-01-02", amount: "1000.00", units: "100.000000",
  nav: "10.0000", nav_source: "amfi_auto", stamp_duty: "0.00", switch_group: null, notes: null, deleted_at: null,
  display_name: "Alpha Fund (Direct - Growth) [1001]" } as Transaction;

describe("Holdings page", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    Object.values(api).forEach((f) => f.mockReset());
    api.exportCsvUrl.mockReturnValue("/api/holdings/portfolios/all/export.csv");
    api.listPortfolios.mockResolvedValue([{ id: 1, name: "Self", archived: false }]);
    api.getHoldingsSummary.mockResolvedValue(summary());
    api.getRecentChanges.mockResolvedValue({ empty: true, windows: [] });
    api.getMonthlyEarnings.mockResolvedValue({ empty: true, months: [] });
    api.listTransactions.mockResolvedValue([buy]);
    api.getBackupStatus.mockResolvedValue({ last_backup_at: "2024-12-30T10:00:00+00:00", days_since_backup: 1.2, changed_since_backup: false, backup_due: false, remind_after_days: 7 });
    nav.search = "tab=holdings";
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
          <HoldingsPage />
        </QueryClientProvider>
      );
    });
    for (let i = 0; i < 4; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }
  };
  const button = (label: string) => [...container.querySelectorAll("button")].find((b) => b.textContent === label);

  it("totals the 1D column over every fund, one closed today included, so it equals the tile", async () => {
    await render();
    // The closed row is hidden, yet its -₹30 is in the total: +₹100 − ₹30, as on the tile.
    const footer = container.querySelector("tfoot")?.textContent ?? "";
    expect(footer).toContain("+₹70.00");
    expect(footer).toContain("incl. 1 closed today");
  });

  const card = (title: string) =>
    [...container.querySelectorAll(".metric-card")].find((c) => c.querySelector(".metric-title")?.textContent?.startsWith(title));

  it("shows the newest month's earnings and switches month from the tile's picker", async () => {
    api.getMonthlyEarnings.mockResolvedValue({
      empty: false, year: 2024, as_of: "2024-12-31", benchmark_name: null,
      months: [
        { month: "2024-11", start: "2024-10-31", end: "2024-11-29", nav_days: 21, gain: 1500, change_pct: 0.5, benchmark_change_pct: 0.4, excess_pp: 0.1 },
        { month: "2024-12", start: "2024-11-29", end: "2024-12-31", nav_days: 22, gain: -250.5, change_pct: -0.1, benchmark_change_pct: 0.2, excess_pp: -0.3 },
      ],
    });
    await render();
    const picker = container.querySelector<HTMLSelectElement>('select[aria-label="Month"]')!;
    expect([...picker.options].map((o) => o.textContent)).toEqual(["Nov", "Dec"]);
    expect(picker.value).toBe("2024-12");
    expect(card("Earned in")?.textContent).toContain("−₹250.50");

    await act(async () => {
      picker.value = "2024-11";
      picker.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(card("Earned in")?.textContent).toContain("+₹1,500.00");
    expect(card("Earned in")?.textContent).toContain("peers +0.40%");
  });

  it("keeps the 1-day tile when the recent windows fail to load", async () => {
    api.getRecentChanges.mockRejectedValue(new ApiError(503, "Database unavailable or timed out. Try again shortly."));
    await render();
    expect(card("1-day change")?.textContent).toContain("+₹70.00");
    expect(container.textContent).toContain("Couldn't load the last few days' movement");
  });

  it("says the holdings failed to load, with a retry, instead of an empty page", async () => {
    api.getHoldingsSummary.mockRejectedValue(new ApiError(503, "Database unavailable or timed out. Try again shortly."));
    await render();
    expect(container.textContent).toContain("Couldn't load your holdings");
    expect(button("Retry")).toBeTruthy();
  });

  it("says when rows can't be applied and asks for a backup when one is due", async () => {
    api.getHoldingsSummary.mockResolvedValue(summary({
      ledger_errors: [{ txn_id: 9, scheme_code: 1001, trade_date: "2024-06-01", message: "Redeems 150 units but only 100 were held", portfolio_id: 1 }],
    }));
    api.getBackupStatus.mockResolvedValue({ last_backup_at: null, days_since_backup: null, changed_since_backup: true, backup_due: true, remind_after_days: 7 });
    await render();
    const text = container.textContent ?? "";
    expect(text).toContain("can't be applied as recorded");
    expect(text).toContain("#9");
    expect(text).toContain("You haven't downloaded a backup yet.");
  });

  it("deletes a transaction with an undo that restores the same row", async () => {
    nav.search = "tab=transactions";
    api.deleteTransaction.mockResolvedValue({ deleted_ids: [7] });
    api.restoreTransaction.mockResolvedValue({ restored_ids: [7] });
    await render();
    await act(async () => button("Delete")!.click());
    expect(api.deleteTransaction).toHaveBeenCalledWith(7);
    expect(container.textContent).toContain("Transaction deleted.");
    await act(async () => button("Undo")!.click());
    expect(api.restoreTransaction).toHaveBeenCalledWith(7);
  });

  it("lists deleted rows on request, each with a Restore", async () => {
    nav.search = "tab=transactions";
    api.listTransactions.mockImplementation(async (_pid: unknown, includeDeleted: boolean) =>
      includeDeleted ? [buy, { ...buy, id: 8, deleted_at: "2024-06-01T10:00:00" }] : [buy]);
    api.restoreTransaction.mockResolvedValue({ restored_ids: [8] });
    await render();
    const toggle = [...container.querySelectorAll("label")].find((l) => l.textContent?.includes("Show deleted"))!.querySelector("input")!;
    await act(async () => toggle.click());
    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }
    await act(async () => button("Restore")!.click());
    expect(api.restoreTransaction).toHaveBeenCalledWith(8);
  });
});
