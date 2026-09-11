"use client";

import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { StatCard } from "@/components/shared/StatCard";
import { getMetaStatus } from "@/lib/api/meta";

// Phase 2 scaffold: proves AppShell + Sidebar + DateRangePicker + StatCard +
// react-query all work together end-to-end. The real Overview page (KPIs,
// asset-class chart, macro trend, AMC panel, category matrix) is Phase 3.
export default function Home() {
  const { data: status, isLoading } = useQuery({
    queryKey: ["meta-status"],
    queryFn: getMetaStatus,
    refetchInterval: 60_000,
  });

  return (
    <AppShell>
      <h1 className="mf-page-title">Indian Mutual Funds</h1>
      <p className="mf-page-caption">
        Phase 2: shared component library + global date-range store are wired up. The real Overview page lands in Phase 3.
      </p>
      <div className="grid grid-cols-4 gap-4">
        <StatCard title="Tracked Schemes" value={isLoading ? "..." : (status?.schemes_count ?? 0).toLocaleString()} />
        <StatCard title="Fund Houses" value={isLoading ? "..." : (status?.amc_count ?? 0).toLocaleString()} />
        <StatCard
          title="Latest NAV"
          value={isLoading ? "..." : status?.max_date ?? "-"}
          tone={status?.is_stale ? "warn" : "pos"}
          sub={status?.is_stale ? "Sync pending" : "Live"}
          subTone={status?.is_stale ? "neg" : "pos"}
        />
        <StatCard title="DB Size" value={isLoading ? "..." : `${status?.file_size_mb ?? 0} MB`} />
      </div>
    </AppShell>
  );
}
