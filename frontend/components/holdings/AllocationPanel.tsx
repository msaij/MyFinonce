"use client";

import { useQuery } from "@tanstack/react-query";

import { Banner } from "@/components/shared/Banner";
import { DataTable, type ColumnConfig } from "@/components/shared/DataTable";
import { FormulaTooltip } from "@/components/shared/FormulaTooltip";
import { PlotlyChart } from "@/components/shared/PlotlyChart";
import { StatCard } from "@/components/shared/StatCard";
import { getAllocation, type Allocation, type AllocationBucket, type PortfolioKey } from "@/lib/api/holdings";
import { formatDate, formatInr } from "@/lib/format";
import { AXIS, SERIES_1 } from "@/lib/holdingsChart";

/** A single fund holding more than this share is flagged: one fund's mishap is then a portfolio event. */
const FUND_WARN_PCT = 40;
/** Same idea one level up: every scheme of a fund house shares its operations and risk desk. */
const AMC_WARN_PCT = 50;
const pct1 = (v: number | null | undefined) => (v === null || v === undefined ? "-" : `${v.toFixed(1)}%`);

/** Top N buckets, the rest folded into "Other" (never an extra generated colour). */
function topN(rows: AllocationBucket[], n: number): AllocationBucket[] {
  if (rows.length <= n) return rows;
  const head = rows.slice(0, n);
  const tail = rows.slice(n);
  return [...head, { bucket: `Other (${tail.length})`, value: tail.reduce((a, r) => a + r.value, 0), weight_pct: tail.reduce((a, r) => a + r.weight_pct, 0) }];
}

function barFigure(rows: AllocationBucket[]) {
  const ordered = [...rows].reverse();
  const labels = ordered.map((r) => (r.bucket.length > 44 ? `${r.bucket.slice(0, 43)}…` : r.bucket));
  return {
    data: [
      {
        type: "bar", orientation: "h", name: "Actual", y: labels, x: ordered.map((r) => r.weight_pct),
        marker: { color: SERIES_1 }, text: ordered.map((r) => `${r.weight_pct.toFixed(1)}%`), textposition: "outside", cliponaxis: false,
        customdata: ordered.map((r) => [r.value, r.bucket]), hovertemplate: "%{customdata[1]}<br>%{x:.1f}% · ₹%{customdata[0]:,.0f}<extra></extra>",
      },
    ],
    layout: {
      height: Math.max(160, 50 + ordered.length * 34), margin: { l: 10, r: 60, t: 10 }, bargap: 0.35, showlegend: false,
      yaxis: { automargin: true }, xaxis: { ...AXIS, ticksuffix: "%", rangemode: "tozero" },
    },
  };
}

function SectionTitle({ title, about }: { title: string; about: string }) {
  return (
    <h3 className="flex items-center gap-1 text-base font-bold">
      {title}
      <FormulaTooltip label={title} description={about} />
    </h3>
  );
}

export function AllocationPanel({ pid }: { pid: PortfolioKey }) {
  const { data, isLoading, isError, error } = useQuery({ queryKey: ["holdings", "allocation", pid], queryFn: () => getAllocation(pid) });

  if (isLoading) return <div className="mt-4 text-sm" style={{ color: "var(--mf-muted)" }}>Grouping your holdings…</div>;
  if (isError) return <div className="mt-4"><Banner level="danger">{(error as Error).message}</Banner></div>;
  if (!data) return null;
  if (data.total_value <= 0) return <div className="mt-4 text-sm" style={{ color: "var(--mf-muted)" }}>Nothing is held right now.</div>;

  const c = data.concentration;
  const regular = data.by_plan.find((r) => r.bucket.toLowerCase().includes("regular"));
  const largest = data.holdings.reduce<(typeof data.holdings)[number] | null>((a, h) => (a === null || h.value > a.value ? h : a), null);

  return (
    <div className="mt-4 flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <StatCard
          title="Funds held"
          value={String(c.fund_count)}
          sub={`with ${c.amc_count} fund house${c.amc_count === 1 ? "" : "s"}`}
          tooltip={<FormulaTooltip label="Funds held" description="Funds you hold units in right now. Fully redeemed funds are not counted." />}
        />
        <StatCard
          title="Effective number of funds"
          value={c.effective_funds ? c.effective_funds.toFixed(1) : "-"}
          sub={c.hhi !== null ? `HHI ${c.hhi.toFixed(3)}` : ""}
          tooltip={<FormulaTooltip label="Effective number of funds" formula="1 / Σ wᵢ²   (wᵢ = each fund's weight)" description="How many equal-sized funds your portfolio behaves like. 10 funds where one is 80% count as barely 1.5. HHI (the Herfindahl index) is Σ wᵢ² itself: 1.000 is everything in one fund." />}
        />
        <StatCard
          title="Largest holding"
          value={pct1(c.top1_pct)}
          sub={largest?.scheme_name ?? ""}
          tone={c.top1_pct !== null && c.top1_pct > FUND_WARN_PCT ? "warn" : ""}
          tooltip={<FormulaTooltip label="Largest holding" description={`The single fund with the most money in it, as a share of today's value. Shown in amber above ${FUND_WARN_PCT}%: past that, one fund's bad year is the portfolio's bad year.`} />}
        />
        <StatCard
          title="Top 3 holdings"
          value={pct1(c.top3_pct)}
          sub={c.fund_count > 3 ? `the other ${c.fund_count - 3} hold ${pct1(100 - (c.top3_pct ?? 0))}` : ""}
          tooltip={<FormulaTooltip label="Top 3 holdings" description="How much of the portfolio sits in its three largest funds." />}
        />
        <StatCard
          title="Largest fund house"
          value={pct1(c.top_amc_pct)}
          sub={c.top_amc ?? ""}
          tone={c.top_amc_pct !== null && c.amc_count > 1 && c.top_amc_pct > AMC_WARN_PCT ? "warn" : ""}
          tooltip={<FormulaTooltip label="Largest fund house" description={`Your biggest exposure to one AMC across all its schemes. That is a separate risk from fund concentration. An AMC-level event (Franklin Templeton wound up six debt schemes at once in 2020) hits every scheme it runs, however many of them you spread across. Shown in amber above ${AMC_WARN_PCT}%.`} />}
        />
      </div>
      <p className="-mt-3 text-xs" style={{ color: "var(--mf-muted)" }}>
        Every weight on this tab is by market value today (units × latest NAV{data.as_of ? `, up to ${formatDate(data.as_of)}` : ""}), not by the money paid in. Total {formatInr(data.total_value)}.
      </p>

      {regular && (
        <Banner level="warning">
          {regular.weight_pct.toFixed(1)}% of this portfolio ({formatInr(regular.value)}) is in Regular plans, which carry a distributor commission inside the expense ratio.
          The Insights tab prices the gap fund by fund against each Direct twin&apos;s official TER.
        </Banner>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <section>
          <SectionTitle
            title="Asset allocation"
            about="From each scheme's SEBI category and name. Index funds and ETFs are split into equity or debt by what they track. Gold and silver ETFs are Gold & Commodities, and overseas FoFs are International. Arbitrage funds are Hybrid to SEBI but hedged and market-neutral, so they sit under Cash & Liquid. Every other hybrid stays Hybrid, because splitting one into its equity and debt legs needs monthly portfolio disclosures."
          />
          <PlotlyChart figure={barFigure(data.by_asset_class)} />
        </section>
        <section>
          <SectionTitle
            title="By SEBI category"
            about="SEBI's scheme category, which fixes what a fund may invest in. AMFI still files some funds under their pre-2018 category names ('Income/Debt Oriented Schemes - Liquid Fund'), so the section prefix is dropped and identical categories are counted together. Top 10 shown, the rest folded into Other."
          />
          <PlotlyChart figure={barFigure(topN(data.by_category, 10))} />
        </section>
        <section>
          <SectionTitle title="By fund house" about="Money with each AMC across all its schemes. Top 8 shown, the rest folded into Other." />
          <PlotlyChart figure={barFigure(topN(data.by_amc, 8))} />
        </section>
        <section>
          <SectionTitle
            title="Plan and option"
            about="Direct plans have no distributor commission, so their expense ratio is lower than the same fund's Regular plan. Growth reinvests everything. IDCW pays some out, and each payout is taxed as income in the year you receive it."
          />
          <div className="mt-2 grid grid-cols-2 gap-3">
            {[...data.by_plan, ...data.by_option].map((r) => (
              <StatCard key={r.bucket + r.value} title={r.bucket} value={`${r.weight_pct.toFixed(1)}%`} sub={formatInr(r.value)} />
            ))}
          </div>
        </section>
      </div>

      <HoldingsByClass holdings={data.holdings} />

      {data.by_portfolio && <PortfolioSplitTable rows={data.by_portfolio} />}
    </div>
  );
}

function HoldingsByClass({ holdings }: { holdings: Allocation["holdings"] }) {
  const columns: ColumnConfig[] = [
    { key: "asset_class", label: "Asset class", tooltip: "The class this fund counts toward in the Asset allocation chart. See that chart's ⓘ for how it is decided." },
    { key: "scheme_name", label: "Fund", render: (_r, v) => <span className="font-semibold">{String(v ?? "-")}</span> },
    { key: "category", label: "SEBI category", tooltip: "The fund's SEBI category, with AMFI's section prefix dropped." },
    { key: "fund_house", label: "Fund house" },
    {
      key: "riskometer", label: "Riskometer", render: (_r, v) => (v ? String(v) : "-"),
      tooltip: "SEBI's official risk label for the fund (Low to Very High), from AMFI's fund data. The Risk tab shows where your money sits on the scale.",
    },
    { key: "value", label: "Value", format: "inr" },
    { key: "weight_pct", label: "Weight", format: "pct", decimals: 1, tooltip: "Share of the portfolio's value today." },
  ];
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle
        title="What's in each asset class"
        about="Every fund you hold, grouped by the asset class it is counted in, largest first within each class. Use it to check where a fund landed."
      />
      <DataTable columns={columns} rows={holdings as unknown as Record<string, unknown>[]} keyField="scheme_code" />
    </section>
  );
}

function PortfolioSplitTable({ rows }: { rows: NonNullable<Allocation["by_portfolio"]> }) {
  const classes = Array.from(new Set(rows.flatMap((r) => Object.keys(r.by_asset_class))));
  const columns: ColumnConfig[] = [
    { key: "bucket", label: "Portfolio" },
    { key: "value", label: "Value", format: "inr" },
    { key: "weight_pct", label: "Share", format: "pct", decimals: 1, tooltip: "This portfolio's share of the household's value." },
    { key: "fund_count", label: "Funds" },
    ...classes.map<ColumnConfig>((cls) => ({
      key: `class:${cls}`,
      label: cls,
      tooltip: `Share of this portfolio's own value in ${cls}. Each row adds up to 100%.`,
      sortValue: (r) => (r.by_asset_class as Record<string, number>)[cls] ?? 0,
      render: (r) => pct1((r.by_asset_class as Record<string, number>)[cls] ?? 0),
    })),
  ];
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle
        title="By portfolio"
        about="The household view adds every portfolio together. This splits it back out: whose money is where, and each portfolio's own asset mix."
      />
      <DataTable columns={columns} rows={rows as unknown as Record<string, unknown>[]} keyField="portfolio_id" />
    </section>
  );
}
