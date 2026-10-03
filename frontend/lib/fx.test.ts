import { afterEach, describe, expect, it, vi } from "vitest";

import { COINBASE_URL, DAILY_URL, fetchUsdInr, parseCoinbase, parseDaily } from "./fx";

const json = (body: unknown, ok = true) => ({ ok, status: ok ? 200 : 503, json: async () => body }) as Response;

describe("USD to INR", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads Coinbase's string rate as a live quote", () => {
    expect(parseCoinbase({ data: { currency: "USD", rates: { INR: "96.146973" } } }, 1000)).toEqual({ rate: 96.146973, asOf: 1000, source: "Coinbase", live: true });
    expect(parseCoinbase({ data: { rates: {} } }, 1000)).toBeNull();
    expect(parseCoinbase({ data: { rates: { INR: "0" } } }, 1000)).toBeNull();
  });

  it("reads the daily fallback with its publish time", () => {
    expect(parseDaily({ result: "success", time_last_update_unix: 1790812951, rates: { INR: 96.378852 } })).toEqual({
      rate: 96.378852, asOf: 1790812951000, source: "ExchangeRate-API", live: false,
    });
    expect(parseDaily({ result: "error", rates: { INR: 96 } })).toBeNull();
  });

  it("falls back to the daily rate when the live source fails", async () => {
    const fetchMock = vi.fn(async (url: string) => (url === COINBASE_URL ? json({}, false) : json({ result: "success", time_last_update_unix: 1, rates: { INR: 96.3 } })));
    vi.stubGlobal("fetch", fetchMock);
    const q = await fetchUsdInr();
    expect(q).toMatchObject({ rate: 96.3, live: false });
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([COINBASE_URL, DAILY_URL]);
  });

  it("uses only the live source when it answers", async () => {
    const fetchMock = vi.fn(async () => json({ data: { rates: { INR: "96.15" } } }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await fetchUsdInr()).toMatchObject({ rate: 96.15, live: true, source: "Coinbase" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("says so when neither source has a rate", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({}, false)));
    await expect(fetchUsdInr()).rejects.toThrow();
  });
});
