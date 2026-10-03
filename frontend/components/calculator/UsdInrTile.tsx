"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchUsdInr } from "@/lib/fx";

const time = (ms: number) => new Date(ms).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
const day = (ms: number) => new Date(ms).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });

/** A compact USD -> INR rate for the Calculator page header, refreshed every minute while
 *  the page is open (see lib/fx.ts for the sources). */
export function UsdInrTile() {
  const { data, isError, isFetching, refetch } = useQuery({
    queryKey: ["fx", "USDINR"],
    queryFn: fetchUsdInr,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: 1,
  });

  return (
    <div
      className="flex items-center gap-3 rounded-xl border px-3 py-1.5"
      style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)" }}
      title={data ? `1 USD = ₹${data.rate.toFixed(4)} · ₹1 = $${(1 / data.rate).toFixed(6)} · source: ${data.source}` : undefined}
    >
      <div className="leading-tight">
        <div className="text-[0.65rem] font-bold uppercase tracking-wide" style={{ color: "var(--mf-muted)" }}>
          USD → INR
        </div>
        <div className="text-base font-bold tabular-nums">{data ? `₹${data.rate.toFixed(2)}` : isError ? "—" : "…"}</div>
      </div>
      <div className="text-[0.7rem] leading-tight" style={{ color: "var(--mf-muted)" }}>
        {data ? (
          data.live ? (
            <>
              <span style={{ color: "var(--mf-success)" }}>● Live</span>
              <br />
              {time(data.asOf)}
            </>
          ) : (
            <>
              Daily rate
              <br />
              {day(data.asOf)}
            </>
          )
        ) : isError ? (
          "Rate unavailable"
        ) : (
          "Loading"
        )}
      </div>
      <button
        type="button"
        onClick={() => refetch()}
        disabled={isFetching}
        className="text-sm disabled:opacity-40"
        style={{ color: "var(--mf-muted)" }}
        aria-label="Refresh the USD to INR rate"
        title="Refresh"
      >
        ↻
      </button>
    </div>
  );
}
