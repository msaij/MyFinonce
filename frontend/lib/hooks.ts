import { useEffect, useRef, useState } from "react";

export function useDebouncedValue<T>(value: T, delayMs = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

export interface LogEntry {
  time: string;
  level: string;
  logger: string;
  message: string;
}

/**
 * Subscribes to GET /api/admin/logs/stream (Server-Sent Events) -- replaces the original
 * Data Management page's st.fragment(run_every="5s") polling with a genuine push connection.
 * Returned `entries` are OLDEST-FIRST (a plain growing log, capped at `maxEntries`); the
 * caller reverses for display if it wants newest-first, matching the original page's order.
 * Reconnects automatically on transient drops (the browser's native EventSource already
 * does this on its own); `connected` reflects only the current socket's open/closed state.
 *
 * Connects DIRECTLY to the backend (NEXT_PUBLIC_BACKEND_ORIGIN), not through Next's own
 * `/api/*` rewrite proxy like every other call in this app -- `next start` gzip-compresses
 * the proxied response, and gzip fundamentally breaks SSE (its compressor buffers output
 * until it has enough data to emit a block, so a slow-trickle log stream's small `data: ...`
 * frames can sit uncompressed-but-unflushed indefinitely; confirmed directly: curl against
 * either origin gets every event immediately, but a browser EventSource through :3000 got
 * zero messages despite `connected` (the SSE handshake itself) succeeding). See
 * frontend/Dockerfile's matching comment for why this has to be a build-time
 * NEXT_PUBLIC_ var and why bypassing the proxy here specifically is safe (the backend's
 * CORSMiddleware already allows this exact origin).
 */
export function useLogStream(minLevel: string, contains: string, maxEntries = 150): { entries: LogEntry[]; connected: boolean } {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [connected, setConnected] = useState(false);
  const maxEntriesRef = useRef(maxEntries);
  maxEntriesRef.current = maxEntries;

  useEffect(() => {
    setEntries([]);
    setConnected(false);
    const params = new URLSearchParams({ min_level: minLevel, contains });
    const base = process.env.NEXT_PUBLIC_BACKEND_ORIGIN ?? "";
    const es = new EventSource(`${base}/api/admin/logs/stream?${params.toString()}`);

    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (ev) => {
      try {
        const entry = JSON.parse(ev.data) as LogEntry;
        setEntries((prev) => {
          const next = [...prev, entry];
          return next.length > maxEntriesRef.current ? next.slice(next.length - maxEntriesRef.current) : next;
        });
      } catch {
        // malformed frame -- ignore rather than break the whole stream
      }
    };

    return () => es.close();
  }, [minLevel, contains]);

  return { entries, connected };
}

/**
 * Synchronizes key-value state to the browser's URL search parameters
 * so views, filters, tabs, and models can be bookmarked and shared.
 * Uses window.history.replaceState to prevent jarring page flashes.
 * Supports optional debounceMs to prevent browser history rate-limiting on slider drags.
 */
export function useUrlSync(
  params: Record<string, string | number | undefined | null>,
  debounceMs = 0
) {
  const isFirstRun = useRef(true);
  const serialized = JSON.stringify(params);

  useEffect(() => {
    if (typeof window === "undefined") return;

    if (isFirstRun.current) {
      isFirstRun.current = false;
      return;
    }

    const applySync = () => {
      const currentUrl = new URL(window.location.href);
      let changed = false;

      Object.entries(params).forEach(([key, val]) => {
        const existing = currentUrl.searchParams.get(key);
        if (
          val === undefined ||
          val === null ||
          val === "" ||
          val === "All" ||
          val === "All Plans" ||
          val === "All Options" ||
          val === "All Categories" ||
          val === "All Sub-Categories" ||
          val === "All Fund Houses"
        ) {
          if (existing !== null) {
            currentUrl.searchParams.delete(key);
            changed = true;
          }
        } else {
          const valStr = String(val);
          if (existing !== valStr) {
            currentUrl.searchParams.set(key, valStr);
            changed = true;
          }
        }
      });

      if (changed) {
        window.history.replaceState(null, "", currentUrl.toString());
      }
    };

    if (debounceMs > 0) {
      const timer = setTimeout(applySync, debounceMs);
      return () => clearTimeout(timer);
    } else {
      applySync();
    }
  }, [serialized, debounceMs]);
}

