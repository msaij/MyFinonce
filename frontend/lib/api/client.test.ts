import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiGet, apiPost, errorText, extractErrorMessage, retryUnlessClientError } from "./client";

describe("abandoned requests", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("hands react-query's signal to fetch, so a superseded request is cancelled", async () => {
    const fetchMock = vi.fn(async () => new Response("[]", { status: 200 }));
    vi.stubGlobal("window", { location: { origin: "http://localhost:3000" } });
    vi.stubGlobal("fetch", fetchMock);
    const ctrl = new AbortController();
    await apiGet("/api/leaders", { search: "hdfc" }, ctrl.signal);
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:3000/api/leaders?search=hdfc", { signal: ctrl.signal });
    await apiPost("/api/holdings/transactions/preview", { a: 1 }, ctrl.signal);
    expect(fetchMock).toHaveBeenLastCalledWith("/api/holdings/transactions/preview", expect.objectContaining({ signal: ctrl.signal }));
    // Without one, fetch is called exactly as before.
    await apiGet("/api/meta/status");
    expect(fetchMock).toHaveBeenLastCalledWith("http://localhost:3000/api/meta/status");
  });
});

const json = (body: unknown, status: number) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("extractErrorMessage", () => {
  it("reads FastAPI's string detail", async () => {
    expect(await extractErrorMessage(json({ detail: "Database unavailable or timed out. Try again shortly." }, 503))).toBe(
      "Database unavailable or timed out. Try again shortly."
    );
  });

  it("names the parameter a 422 is about, instead of printing the raw JSON", async () => {
    const res = json(
      { detail: [{ type: "literal_error", loc: ["query", "vol_lookback"], msg: "Input should be '1Y', '6M', '3Y' or 'Full Window'", input: "2Y" }] },
      422
    );
    expect(await extractErrorMessage(res)).toBe("vol_lookback: Input should be '1Y', '6M', '3Y' or 'Full Window'");
    expect(await extractErrorMessage(json({ detail: [{ msg: "Field required" }] }, 422))).toBe("Field required");
  });

  it("falls back to the body, then the status text", async () => {
    expect(await extractErrorMessage(new Response("Internal Server Error", { status: 500 }))).toBe("Internal Server Error");
    expect(await extractErrorMessage(new Response("", { status: 502, statusText: "Bad Gateway" }))).toBe("Bad Gateway");
  });
});

describe("errorText", () => {
  it("says what failed and with which status", () => {
    expect(errorText(new ApiError(503, "Database unavailable"))).toBe("Database unavailable (HTTP 503)");
    expect(errorText(new TypeError("Failed to fetch"))).toBe("Failed to fetch");
    expect(errorText("x")).toBe("x");
  });
});

describe("retryUnlessClientError", () => {
  it("retries a server or network failure once, as before", () => {
    expect(retryUnlessClientError(0, new ApiError(503, ""))).toBe(true);
    expect(retryUnlessClientError(0, new TypeError("Failed to fetch"))).toBe(true);
    expect(retryUnlessClientError(1, new ApiError(500, ""))).toBe(false);
  });

  it("never retries a 4xx: the same request is refused the same way", () => {
    expect(retryUnlessClientError(0, new ApiError(422, ""))).toBe(false);
    expect(retryUnlessClientError(0, new ApiError(404, ""))).toBe(false);
    expect(retryUnlessClientError(0, new ApiError(403, ""))).toBe(false);
  });
});
