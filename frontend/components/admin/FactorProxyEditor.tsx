"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Banner } from "@/components/shared/Banner";
import { getFactorProxies, setFactorProxies } from "@/lib/api/admin";
import { ApiError } from "@/lib/api/client";
import { formatDate } from "@/lib/format";

/** The four proxy slots factor_model.DEFAULT_FACTOR_PROXIES defines, in the order it
 *  falls back through them. Labels say what each one is FOR, since an AMFI code alone
 *  tells the reader nothing. */
const SLOTS: { key: string; label: string; help: string; navKey?: "factor_market_last_nav" | "factor_momentum_last_nav" }[] = [
  { key: "factor_proxy_market", label: "Market", help: "Broad-market index fund used as the market factor", navKey: "factor_market_last_nav" },
  { key: "factor_proxy_market_fallback", label: "Market fallback", help: "Used when the primary has no NAV for the window" },
  { key: "factor_proxy_market_fallback_2", label: "Market fallback 2", help: "Second fallback" },
  { key: "factor_proxy_momentum", label: "Momentum", help: "Momentum-strategy fund used as the momentum factor", navKey: "factor_momentum_last_nav" },
];

/** Editor for the funds that stand in for the market and momentum factors in Quant's
 *  factor attribution and Holdings' risk tab. Both of those tell the user to come here
 *  by name when a proxy has no NAV data for the window they asked for. */
export function FactorProxyEditor({ lastNavs }: { lastNavs?: Record<string, string | null> }) {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({ queryKey: ["factor-proxies"], queryFn: getFactorProxies });
  const [draft, setDraft] = useState<Record<string, string>>({});

  // Seed the inputs once the current values arrive, and again after a save.
  useEffect(() => {
    if (data?.proxies) setDraft(Object.fromEntries(Object.entries(data.proxies).map(([k, v]) => [k, String(v)])));
  }, [data?.proxies]);

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, number> = {};
      for (const { key } of SLOTS) {
        const n = Number(draft[key]);
        if (Number.isInteger(n) && n > 0 && n !== data?.proxies[key]) body[key] = n;
      }
      return setFactorProxies(body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["factor-proxies"] });
      queryClient.invalidateQueries({ queryKey: ["data-quality"] });
    },
  });

  const invalid = SLOTS.some(({ key }) => {
    const n = Number(draft[key]);
    return draft[key] !== undefined && (!Number.isInteger(n) || n <= 0);
  });
  const changed = SLOTS.some(({ key }) => draft[key] !== undefined && Number(draft[key]) !== data?.proxies[key]);
  const isDefault = data && SLOTS.every(({ key }) => data.proxies[key] === data.defaults[key]);

  if (isLoading) return <p className="mt-2 text-sm" style={{ color: "var(--mf-muted)" }}>Loading factor proxies…</p>;
  if (isError) return <div className="mt-2"><Banner level="danger">{(error as Error).message}</Banner></div>;

  return (
    <div className="mt-2">
      <p className="text-xs" style={{ color: "var(--mf-muted)" }}>
        AMFI codes of the funds that stand in for each factor in Quant&apos;s factor attribution and Holdings&apos; risk tab. A proxy with no NAVs
        across the window being analysed is exactly what makes those sections report &quot;factor proxy funds lack NAV data&quot;.
        {isDefault ? " Currently the built-in defaults." : " Changed from the built-in defaults."}
      </p>
      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        {SLOTS.map(({ key, label, help, navKey }) => {
          const lastNav = navKey ? lastNavs?.[navKey] : undefined;
          return (
            <label key={key} className="flex flex-col gap-1 text-xs font-medium" style={{ color: "var(--mf-muted)" }}>
              {label}
              <input
                type="number"
                inputMode="numeric"
                value={draft[key] ?? ""}
                onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
                className="rounded-lg border px-2 py-1.5 text-sm"
                style={{ borderColor: "var(--mf-border)", background: "var(--mf-card-bg)", color: "var(--mf-fg)" }}
              />
              <span className="text-[0.7rem]">
                {help}. Default {data?.defaults[key]}.{lastNav ? ` Last NAV ${formatDate(lastNav)}.` : ""}
              </span>
            </label>
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={!changed || invalid || save.isPending}
          onClick={() => save.mutate()}
          className="rounded-lg px-4 py-2 text-sm font-semibold disabled:opacity-50"
          style={{ background: "var(--mf-accent)", color: "white" }}
        >
          {save.isPending ? "Saving…" : "Save proxies"}
        </button>
        {data && !isDefault && (
          <button
            type="button"
            className="rounded-lg px-3 py-1.5 text-xs font-semibold"
            style={{ background: "var(--mf-card-bg)", color: "var(--mf-fg)", border: "1px solid var(--mf-border)" }}
            onClick={() => setDraft(Object.fromEntries(Object.entries(data.defaults).map(([k, v]) => [k, String(v)])))}
          >
            Restore defaults
          </button>
        )}
        {invalid && <span className="text-xs" style={{ color: "var(--mf-danger)" }}>AMFI codes are positive whole numbers.</span>}
        {save.isSuccess && !changed && <span className="text-xs" style={{ color: "var(--mf-success)" }}>Saved. Factor caches cleared.</span>}
        {save.isError && <span className="text-xs" style={{ color: "var(--mf-danger)" }}>{(save.error as ApiError).message}</span>}
      </div>
    </div>
  );
}
