"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { useDateRangeStore } from "./stores/dateRange";
import { useFilterStore } from "./stores/filters";

/**
 * Gates mounting `children` (the whole app -- Sidebar, DateRangePicker, every page) until both
 * persisted stores have rehydrated from localStorage. This is NOT just a hydration-mismatch
 * fix (skipHydration:true on both stores already guarantees the server's render and the
 * client's first render agree, since neither has real data yet) -- it's also required for
 * correctness: several pages seed local useState from getFilter() via a lazy initializer
 * (`useState(() => getFilter(SECTION, key, fallback))`), which runs exactly once, on that
 * component's first mount. If children mounted immediately and rehydrate() completed only
 * afterward (in an effect), every one of those useState initializers would have already
 * captured the plain fallback permanently -- the store's later update wouldn't be re-read,
 * since nothing about it is subscribed reactively. Delaying the children's first mount until
 * rehydrate() resolves means that first (and, for those useState calls, only-relevant) render
 * already sees the real persisted values. The cost is one brief loading flash on a hard page
 * load / fresh tab (never on a client-side route change, which doesn't remount this provider);
 * localStorage reads are effectively instant, so in practice this is well under one frame.
 */
export function AppProviders({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            retry: 1,
          },
        },
      })
  );
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    Promise.all([Promise.resolve(useFilterStore.persist.rehydrate()), Promise.resolve(useDateRangeStore.persist.rehydrate())]).finally(() =>
      setHydrated(true)
    );
  }, []);

  if (!hydrated) return null;

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
