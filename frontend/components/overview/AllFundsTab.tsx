"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, keepPreviousData } from "@tanstack/react-query";

import { DataTable, ColumnConfig, SortDirection, sortTableRows } from "@/components/shared/DataTable";
import { Banner } from "@/components/shared/Banner";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { formatSignedPct, formatDate, toneOf } from "@/lib/format";
import { useDebouncedValue } from "@/lib/hooks";
import { formatTer } from "@/lib/holdings";
import { getAllFunds, type FundListRow } from "@/lib/api/overview";

const STATUS_OPTIONS = ["All", "Active", "Inactive"] as const;
type Status = (typeof STATUS_OPTIONS)[number];
const PAGE_SIZES = [50, 100, 250, 500];

const signed = (v: unknown) => {
  const n = v as number | null;
  const tone = toneOf(n);
  return <span className={tone === "pos" ? "mf-pos" : tone === "neg" ? "mf-neg" : ""}>{formatSignedPct(n, 2)}</span>;
};

const COLUMNS: ColumnConfig[] = [
  { key: "fund_house", label: "Fund house" },
  {
    key: "display_name",
    label: "Fund",
    render: (r) => (
      <Link href={`/scheme/${r.scheme_code}`} className="font-semibold" style={{ color: "var(--mf-accent)" }}>
        {String(r.display_name)}
      </Link>
    ),
  },
  { key: "broad_category", label: "Asset class" },
  { key: "category", label: "Category", tooltip: "SEBI category as AMFI files it." },
  {
    key: "is_active",
    label: "Status",
    sortValue: (r) => (r.is_active ? "Active" : "Inactive"),
    render: (r) => (r.is_active ? "Active" : <span style={{ color: "var(--mf-muted)" }}>Inactive</span>),
    tooltip: "Active = published a NAV in the 30 days up to the latest NAV date. Inactive schemes (matured FMPs, merged or wound-up funds) have no trailing returns.",
  },
  { key: "expense_ratio", label: "TER %", format: "number", render: (_r, v) => formatTer(v as number | null), tooltip: "Total expense ratio, as AMFI publishes it." },
  { key: "latest_nav", label: "Latest NAV", format: "number", decimals: 4 },
  { key: "latest_date", label: "NAV date", format: "date" },
  { key: "change_1d_pct", label: "1D", format: "signed_pct", render: (_r, v) => signed(v) },
  { key: "return_30d_pct", label: "30D", format: "signed_pct", render: (_r, v) => signed(v) },
  { key: "return_1y_pct", label: "1Y", format: "signed_pct", render: (_r, v) => signed(v) },
  { key: "return_3y_pct", label: "3Y", format: "signed_pct", render: (_r, v) => signed(v), tooltip: "Cumulative NAV change over 3 years, not annualised." },
  { key: "return_5y_pct", label: "5Y", format: "signed_pct", render: (_r, v) => signed(v), tooltip: "Cumulative NAV change over 5 years, not annualised." },
];

function FilterSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: string[] }) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-lg border px-2 py-1.5 text-sm"
        style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
      >
        {options.map((o) => (
          <option key={o} value={o} className="bg-white text-slate-900">
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}

const distinct = (rows: FundListRow[], key: "fund_house" | "broad_category") =>
  Array.from(new Set(rows.map((r) => r[key]).filter((v): v is string => !!v))).sort((a, b) => a.localeCompare(b));

/** Every mutual fund scheme in one table: the whole list is fetched once and searched,
 *  filtered, sorted and paged in the browser. */
export function AllFundsTab({ planType, optionType }: { planType: string; optionType: string }) {
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 250);
  const [assetClass, setAssetClass] = useState("All asset classes");
  const [amc, setAmc] = useState("All fund houses");
  const [status, setStatus] = useState<Status>("All");
  const [pageSize, setPageSize] = useState(100);
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDirection>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["all-funds", planType, optionType],
    queryFn: () => getAllFunds(planType, optionType),
    placeholderData: keepPreviousData,
    staleTime: 5 * 60_000,
  });
  const all = useMemo(() => data ?? [], [data]);

  const assetClasses = useMemo(() => distinct(all, "broad_category"), [all]);
  const amcs = useMemo(() => distinct(all, "fund_house"), [all]);

  const filtered = useMemo(() => {
    const tokens = debouncedSearch.toLowerCase().match(/[a-z0-9]+/g) ?? [];
    return all.filter((r) => {
      if (assetClass !== "All asset classes" && r.broad_category !== assetClass) return false;
      if (amc !== "All fund houses" && r.fund_house !== amc) return false;
      if (status === "Active" && !r.is_active) return false;
      if (status === "Inactive" && r.is_active) return false;
      if (!tokens.length) return true;
      const hay = `${r.display_name} ${r.fund_house ?? ""} ${r.category ?? ""}`.toLowerCase();
      return tokens.every((t) => hay.includes(t));
    });
  }, [all, debouncedSearch, assetClass, amc, status]);

  // Sorted across the whole filtered list, then paged -- DataTable alone would only sort the visible page.
  const sorted = useMemo(() => sortTableRows(filtered, sortKey, sortDir, COLUMNS) as FundListRow[], [filtered, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(sorted.length / pageSize));
  useEffect(() => setPage(0), [debouncedSearch, assetClass, amc, status, pageSize, sortKey, sortDir, planType, optionType]);
  const current = Math.min(page, pageCount - 1);
  const pageRows = sorted.slice(current * pageSize, (current + 1) * pageSize);

  const activeCount = useMemo(() => all.filter((r) => r.is_active).length, [all]);

  const pagerButton = (label: string, target: number, disabled: boolean) => (
    <button
      type="button"
      disabled={disabled}
      onClick={() => setPage(target)}
      className="rounded-lg border px-3 py-1 text-xs font-semibold disabled:opacity-40"
      style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
    >
      {label}
    </button>
  );

  const pager = (
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
      <span>
        {sorted.length === 0 ? (
          "No funds"
        ) : (
          <>
            Showing <b>{(current * pageSize + 1).toLocaleString()}</b>–<b>{Math.min((current + 1) * pageSize, sorted.length).toLocaleString()}</b> of{" "}
            <b>{sorted.length.toLocaleString()}</b> funds
          </>
        )}
      </span>
      <div className="flex items-center gap-2">
        {pagerButton("« First", 0, current === 0)}
        {pagerButton("‹ Prev", current - 1, current === 0)}
        <span className="text-xs" style={{ color: "var(--mf-muted)" }}>
          Page {current + 1} of {pageCount}
        </span>
        {pagerButton("Next ›", current + 1, current >= pageCount - 1)}
        {pagerButton("Last »", pageCount - 1, current >= pageCount - 1)}
      </div>
    </div>
  );

  return (
    <div className="mt-6 space-y-4">
      <div className="filter-box grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <label className="flex flex-col gap-1 text-xs font-medium sm:col-span-2" style={{ color: "var(--mf-muted)" }}>
          <span className="flex items-center">
            Search
            <FormulaTooltip label="Search" description="Matches fund name, plan, option, AMFI code, fund house and category. Words can be in any order." />
          </span>
          <input
            type="text"
            placeholder="e.g. parag flexi direct, 122639..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="rounded-lg border px-3 py-1.5 text-sm"
            style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
          />
        </label>
        <FilterSelect label="Asset class" value={assetClass} onChange={setAssetClass} options={["All asset classes", ...assetClasses]} />
        <FilterSelect label="Fund house" value={amc} onChange={setAmc} options={["All fund houses", ...amcs]} />
        <div className="grid grid-cols-2 gap-3">
          <FilterSelect label="Status" value={status} onChange={(v) => setStatus(v as Status)} options={[...STATUS_OPTIONS]} />
          <FilterSelect label="Rows per page" value={String(pageSize)} onChange={(v) => setPageSize(Number(v))} options={PAGE_SIZES.map(String)} />
        </div>
      </div>

      <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
        {all.length.toLocaleString()} schemes for Plan: {planType} · Option: {optionType} ({activeCount.toLocaleString()} active,{" "}
        {(all.length - activeCount).toLocaleString()} inactive). Every plan and option is its own row. Click a column header to sort the whole list; click a fund to open it.
      </p>

      {isError ? (
        <Banner level="warning">Could not load the fund list.</Banner>
      ) : isLoading && !data ? (
        <p className="text-sm" style={{ color: "var(--mf-muted)" }}>Loading every fund...</p>
      ) : (
        <>
          {pager}
          <DataTable
            columns={COLUMNS}
            rows={pageRows}
            keyField="scheme_code"
            sortKey={sortKey}
            sortDirection={sortDir}
            onSortChange={(k, d) => {
              setSortKey(k);
              setSortDir(d);
            }}
          />
          {pager}
        </>
      )}
    </div>
  );
}
