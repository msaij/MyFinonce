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
   *  publish time for the daily one. */
  asOf: number;
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
  return { rate, asOf: (b.time_last_update_unix ?? 0) * 1000, source: "ExchangeRate-API", live: false };
}

async function getJson(url: string): Promise<unknown> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchUsdInr(): Promise<FxQuote> {
  try {
    const q = parseCoinbase(await getJson(COINBASE_URL), Date.now());
    if (q) return q;
  } catch {
    // fall through to the daily rate
  }
  const q = parseDaily(await getJson(DAILY_URL));
  if (!q) throw new Error("No USD-INR rate available");
  return q;
}
