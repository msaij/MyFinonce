/**
 * USD -> INR for the Calculator page's rate tile, fetched by the browser (no key needed).
 *
 * Primary: Coinbase's public exchange-rate endpoint, refreshed about every minute -- as
 * close to live as a free, keyless source gets. Fallback: ExchangeRate-API's open endpoint,
 * a once-a-day reference rate. Both allow cross-origin requests. Only the currency pair
 * is sent; nothing the user enters on the page is.
 */

export interface FxQuote {
  /** Rupees per US dollar. */
  rate: number;
  /** Epoch ms the rate applies to: when it was fetched for the live source, the provider's
   *  publish time for the daily one. Null when the provider gave no time. */
  asOf: number | null;
  source: "Coinbase" | "ExchangeRate-API";
  live: boolean;
}

export const COINBASE_URL = "https://api.coinbase.com/v2/exchange-rates?currency=USD";
export const DAILY_URL = "https://open.er-api.com/v6/latest/USD";

const valid = (v: number) => Number.isFinite(v) && v > 1 && v < 1000;

/** Coinbase returns rates as strings: {"data": {"currency": "USD", "rates": {"INR": "96.14"}}}. */
export function parseCoinbase(body: unknown, now: number): FxQuote | null {
  const rate = Number((body as { data?: { rates?: Record<string, string> } })?.data?.rates?.INR);
  return valid(rate) ? { rate, asOf: now, source: "Coinbase", live: true } : null;
}

/** ExchangeRate-API: {"result": "success", "time_last_update_unix": 1790..., "rates": {"INR": 96.37}}. */
export function parseDaily(body: unknown): FxQuote | null {
  const b = body as { result?: string; time_last_update_unix?: number; rates?: Record<string, number> };
  const rate = Number(b?.rates?.INR);
  if (b?.result !== "success" || !valid(rate)) return null;
  // A missing time is unknown, not 1 Jan 1970.
  const t = b.time_last_update_unix;
  return { rate, asOf: typeof t === "number" && t > 0 ? t * 1000 : null, source: "ExchangeRate-API", live: false };
}

/** A rate older than this (by when it was last fetched) is shown as stale, not live. */
export const STALE_AFTER_MS = 180_000;

/** Whether the tile's rate can no longer be called current: the last refresh failed, or
 *  nothing has refreshed it for a while (offline, React Query pauses rather than failing).
 *  `updatedAt` is when the shown rate was fetched (0: nothing fetched yet). */
export function isStale(isError: boolean, updatedAt: number, now: number): boolean {
  return isError || (updatedAt > 0 && now - updatedAt > STALE_AFTER_MS);
}

async function getJson(url: string): Promise<unknown> {
  // A stalled provider would otherwise leave the tile on "Loading" until the OS gives up.
  const res = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchUsdInr(): Promise<FxQuote> {
  try {
    const q = parseCoinbase(await getJson(COINBASE_URL), Date.now());
    if (q) return q;
  } catch (e) {
    console.warn("Coinbase USD-INR rate failed; using the daily rate", e);
  }
  const q = parseDaily(await getJson(DAILY_URL));
  if (!q) throw new Error("No USD-INR rate available");
  return q;
}
