/** Everyday percentage sums for the calculator's quick panel. */

export const percentOf = (pct: number, of: number) => (pct / 100) * of;

/** From `from` to `to`, as a % of `from`; null when `from` is zero. */
export const percentChange = (from: number, to: number) => (from === 0 ? null : ((to - from) / from) * 100);

/** `part` as a % of `whole`; null when `whole` is zero. */
export const percentShare = (part: number, whole: number) => (whole === 0 ? null : (part / whole) * 100);

/** GST on a price. "add": the price excludes GST. "remove": the price already includes it. */
export function gst(amount: number, ratePct: number, mode: "add" | "remove"): { net: number; tax: number; gross: number } {
  if (mode === "add") {
    const tax = (amount * ratePct) / 100;
    return { net: amount, tax, gross: amount + tax };
  }
  const net = amount / (1 + ratePct / 100);
  return { net, tax: amount - net, gross: amount };
}
