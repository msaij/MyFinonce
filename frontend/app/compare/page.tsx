"use client";

import { useState, Suspense } from "react";
import { useQuery, useQueries, keepPreviousData } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/layout/AppShell";
import { CompareTab } from "@/components/compare/CompareTab";
import { SearchCombobox } from "@/components/shared/SearchCombobox";
import { Banner } from "@/components/shared/Banner";
import { useUrlSync } from "@/lib/hooks";
import { useDateRangeStore } from "@/lib/stores/dateRange";
import { useFilterStore } from "@/lib/stores/filters";
import { getNavHistory, getSchemeProfile } from "@/lib/api/schemes";
import { BacktestPanel } from "@/components/compare/BacktestPanel";

const SECTION = "compare_simulate";
const DEFAULT_SCHEME_CODES = [122639, 118989];
// Nothing on the server enforces this -- the backtest endpoint and /api/schemes/nav-history
// both take an unbounded list of codes, and adding 14 funds to a one-year request measured
// at +270ms. The ceiling is legibility, not cost: eight is about as many overlapping NAV
// lines as distinct colours can still identify, and the Simulate tab needs one weight input
// per fund that all have to add to 100%. Past eight, the Screener's table compares funds
// better than overlaid lines do.
const MAX_SCHEMES = 8;
// The Compare tab (tables, charts, TER estimate) lives in components/compare/CompareTab.tsx
// and is computed server-side by GET /api/schemes/compare over one common period.

export default function ComparePage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm">Loading Compare & Backtest...</div>}>
      <CompareContent />
    </Suspense>
  );
}

function CompareContent() {
  const searchParams = useSearchParams();
  const { start, end, planType, optionType } = useDateRangeStore();
  const getFilter = useFilterStore((s) => s.getFilter);
  const setFilter = useFilterStore((s) => s.setFilter);

  const rawCodes = searchParams.get("codes");
  const initialCodes: number[] = rawCodes
    ? rawCodes
        .split(",")
        .map(Number)
        .filter((c, i, all) => Number.isInteger(c) && c > 0 && all.indexOf(c) === i)
        // A shared link is not allowed past the picker's own ceiling.
        .slice(0, MAX_SCHEMES)
    : getFilter(SECTION, "selected_scheme_codes", DEFAULT_SCHEME_CODES).slice(0, MAX_SCHEMES);

  const paramTab = searchParams.get("tab");
  const initialTab: "compare" | "backtest" = paramTab === "backtest" ? "backtest" : "compare";

  const paramAmount = searchParams.get("amount");
  const initialAmount = paramAmount && !Number.isNaN(Number(paramAmount))
    ? Number(paramAmount)
    : getFilter(SECTION, "investment_amount", 10000);

  const [selectedCodes, setSelectedCodesState] = useState<number[]>(() => initialCodes.length > 0 ? initialCodes : DEFAULT_SCHEME_CODES);
  const [investmentAmount, setInvestmentAmountState] = useState<number>(() => initialAmount);
  const [activeTab, setActiveTab] = useState<"compare" | "backtest">(() => initialTab);

  function setSelectedCodes(codes: number[]) {
    setSelectedCodesState(codes);
    setFilter(SECTION, "selected_scheme_codes", codes);
  }
  function setInvestmentAmount(v: number) {
    setInvestmentAmountState(v);
    setFilter(SECTION, "investment_amount", v);
  }
  function addScheme(code: number) {
    if (selectedCodes.includes(code) || selectedCodes.length >= MAX_SCHEMES) return;
    setSelectedCodes([...selectedCodes, code]);
  }
  function removeScheme(code: number) {
    setSelectedCodes(selectedCodes.filter((c) => c !== code));
  }
  function resetSelection() {
    setSelectedCodes(DEFAULT_SCHEME_CODES);
    setInvestmentAmount(10000);
  }

  // Profile only: /api/schemes/{code} also ships the fund's ENTIRE NAV history (thousands of
  // rows per fund), which this page never read -- it only needs the name.
  const profileQueries = useQueries({
    queries: selectedCodes.map((code) => ({
      queryKey: ["scheme-profile", code],
      queryFn: () => getSchemeProfile(code),
    })),
  });
  const profiles = profileQueries.map((q) => q.data).filter((p): p is NonNullable<typeof p> => !!p);
  const schemeNames = new Map(profiles.map((p) => [p.scheme_code, p.display_name ?? p.scheme_name]));

  const { data: navHistory } = useQuery({
    queryKey: ["compare-nav", selectedCodes, start, end],
    queryFn: () => getNavHistory(selectedCodes, start || undefined, end || undefined),
    enabled: selectedCodes.length > 0 && !!start && !!end,
    placeholderData: keepPreviousData,
  });

  // Synchronize state with URL query parameters for direct link sharing & bookmarking.
  // The Portfolio Backtest tab's own inputs (mode, amounts, weights, SIP date, benchmark...)
  // live in components/compare/BacktestPanel.tsx, which syncs its own keys.
  useUrlSync({
    codes: selectedCodes.length > 0 ? selectedCodes.join(",") : undefined,
    tab: activeTab !== "compare" ? activeTab : undefined,
    amount: investmentAmount !== 10000 ? investmentAmount : undefined,
  });

  return (
    <AppShell>
      <h1 className="mf-page-title">Compare & Simulate</h1>
      <p className="mf-page-caption">Compare mutual fund schemes side-by-side, then backtest the same funds together as a weighted portfolio.</p>

      {/* --- Shared fund selector --- */}
      <div className="filter-box mt-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm" style={{ color: "var(--mf-muted)" }}>
            Select up to {MAX_SCHEMES} funds -- shared by both tabs below.
            {selectedCodes.length >= MAX_SCHEMES && (
              <span className="ml-2 text-xs font-semibold" style={{ color: "var(--mf-warning)" }}>
                {MAX_SCHEMES} of {MAX_SCHEMES} selected -- remove one to add another.
              </span>
            )}
            <span className="ml-2 inline-flex items-center gap-1.5 text-xs font-semibold" style={{ color: "var(--mf-accent)" }}>
              • Universe: {planType} / {optionType}
            </span>
          </span>
          <button type="button" onClick={resetSelection} className="rounded-lg border px-3 py-1.5 text-xs font-semibold hover:bg-slate-50 transition-colors" style={{ borderColor: "var(--mf-border)", color: "var(--mf-fg)" }}>
            Reset Selection
          </button>
        </div>
        <SearchCombobox
          placeholder="Search & add a fund (filtered by header Plan/Option)..."
          extraParams={{
            plan_type: planType !== "All Plans" ? planType : undefined,
            option_type: optionType !== "All Options" ? optionType : undefined,
          }}
          onSelect={(s) => addScheme(s.scheme_code)}
        />
        <div className="mt-2 flex flex-wrap gap-2">
          {selectedCodes.map((code) => (
            <span key={code} className="mf-pill mf-pill-neutral">
              {schemeNames.get(code) ?? code}
              <button type="button" onClick={() => removeScheme(code)} className="ml-2 font-bold">
                ×
              </button>
            </span>
          ))}
        </div>
      </div>

      {selectedCodes.length === 0 ? (
        <div className="mt-6">
          <Banner level="info">Please select at least 1 fund from the dropdown above to compare or simulate.</Banner>
        </div>
      ) : !navHistory ? null : navHistory.length === 0 ? (
        <div className="mt-6">
          <Banner level="warning">No historical NAV records for any selected fund in this time window. Widen the time horizon, or pick different funds.</Banner>
        </div>
      ) : (
        <>
          <div className="mt-6 flex gap-1.5 border-b pb-2" style={{ borderColor: "var(--mf-border)" }}>
            <button
              type="button"
              onClick={() => setActiveTab("compare")}
              className="rounded-full px-3 py-1.5 text-xs font-semibold"
              style={{ background: activeTab === "compare" ? "var(--mf-accent-bg)" : "transparent", color: activeTab === "compare" ? "var(--mf-accent)" : "var(--mf-fg)" }}
            >
              Head-to-Head Comparison
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("backtest")}
              className="rounded-full px-3 py-1.5 text-xs font-semibold"
              style={{ background: activeTab === "backtest" ? "var(--mf-accent-bg)" : "transparent", color: activeTab === "backtest" ? "var(--mf-accent)" : "var(--mf-fg)" }}
            >
              Portfolio Backtest
            </button>
          </div>

          {activeTab === "compare" && (
            <CompareTab codes={selectedCodes} start={start} end={end} amount={investmentAmount} onAmountChange={setInvestmentAmount} />
          )}

          {activeTab === "backtest" && (
            <BacktestPanel selectedCodes={selectedCodes} schemeNames={schemeNames} start={start} end={end} />
          )}
        </>
      )}
    </AppShell>
  );
}
