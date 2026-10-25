"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { fetchUsdInr, isStale } from "@/lib/fx";

const time = (ms: number) => new Date(ms).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
const day = (ms: number) => new Date(ms).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });

/** A compact USD -> INR rate for the Calculator page header, refreshed every minute while
 *  the page is open (see lib/fx.ts for the sources). */
export function UsdInrTile() {
  const { data, dataUpdatedAt, isError, isFetching, refetch } = useQuery({
    queryKey: ["fx", "USDINR"],
    queryFn: fetchUsdInr,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: 1,
  });
  // Re-checks the rate's age on its own: offline, the query pauses and nothing else re-renders.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);
  // A rate kept from before a failed or paused refresh is no longer "Live".
  const stale = !!data && isStale(isError, dataUpdatedAt, Math.max(now, dataUpdatedAt));

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
          stale ? (
            <>
              <span style={{ color: "var(--mf-warning)" }}>Stale</span>
              <br />
              last {time(dataUpdatedAt)}
            </>
          ) : data.live ? (
            <>
              <span style={{ color: "var(--mf-success)" }}>● Live</span>
              <br />
              {time(data.asOf ?? dataUpdatedAt)}
            </>
          ) : (
            <>
              Daily rate
              <br />
              {data.asOf != null ? day(data.asOf) : "date unknown"}
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
