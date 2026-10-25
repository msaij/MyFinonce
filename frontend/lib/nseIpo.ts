/** Formatting for the NSE IPO page. NSE publishes share counts with Indian digit grouping
 *  (1,97,03,310) and subscription as a multiple; both are shown that way here. */

export const fmtQty = (v: number | null | undefined): string => (v == null ? "-" : v.toLocaleString("en-IN"));

export const fmtTimes = (v: number | null | undefined): string => (v == null ? "-" : v.toFixed(2));
