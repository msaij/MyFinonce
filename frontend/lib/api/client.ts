/**
 * Thin typed fetch wrapper. All calls go through Next.js's /api/* rewrite
 * (see next.config.js) to the FastAPI backend, so the browser only ever
 * talks to one origin.
 */

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

/** `signal`: react-query's AbortSignal (`queryFn: ({ signal }) => ...`). With it, a request
 *  the page no longer wants -- the filter changed, the search box got another letter, the
 *  tab closed -- is cancelled, instead of still holding one of the browser's six connections
 *  to the server while the request that matters waits behind it. */
export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
  signal?: AbortSignal,
): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }
  }
  const res = signal ? await fetch(url.toString(), { signal }) : await fetch(url.toString());
  if (!res.ok) {
    throw new ApiError(res.status, await extractErrorMessage(res));
  }
  return res.json();
}

/** FastAPI's HTTPException(detail=...) serializes as {"detail": "..."} -- extract that
 * for a clean, human-readable ApiError.message instead of the raw JSON text. Falls back
 * to the raw body (then statusText) for non-FastAPI-shaped error responses. */
const ADMIN_TOKEN_KEY = "mf_admin_token";

export function getAdminToken(): string | null {
  try {
    return sessionStorage.getItem(ADMIN_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAdminToken(token: string): void {
  sessionStorage.setItem(ADMIN_TOKEN_KEY, token);
}

export async function extractErrorMessage(res: Response): Promise<string> {
  const body = await res.text().catch(() => "");
  if (body) {
    try {
      const parsed = JSON.parse(body);
      if (typeof parsed?.detail === "string") return parsed.detail;
      // FastAPI's 422: [{loc: ["query", "vol_lookback"], msg: "Input should be ..."}, ...].
      // The first problem, named by its parameter, instead of the raw JSON.
      if (Array.isArray(parsed?.detail) && typeof parsed.detail[0]?.msg === "string") {
        const first = parsed.detail[0];
        const loc = Array.isArray(first.loc) && first.loc.length ? first.loc[first.loc.length - 1] : null;
        return loc !== null && loc !== undefined ? `${loc}: ${first.msg}` : first.msg;
      }
      if (parsed?.detail && typeof parsed.detail === "object") {
        if (typeof parsed.detail.code === "string") return parsed.detail.code;
        if (typeof parsed.detail.message === "string") return parsed.detail.message;
      }
    } catch {
      // not JSON -- fall through to the raw body text
    }
  }
  return body || res.statusText;
}

/** What to show for a failed query: the API's own reason and its status, so a 503
 *  ("Database unavailable ...") reads differently from a 422 or a network failure. */
export function errorText(e: unknown): string {
  if (e instanceof ApiError) return `${e.message || "Request failed"} (HTTP ${e.status})`;
  if (e instanceof Error) return e.message;
  return String(e);
}

/** react-query `retry`: once, as before, but never a 4xx -- the same request is refused
 *  the same way again, and retrying only delays the error the page shows. */
export function retryUnlessClientError(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 1;
}

async function apiSend<T>(method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = path.startsWith("/api/admin/") ? getAdminToken() : null;
  if (token) headers["X-Admin-Token"] = token;
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    ...(signal ? { signal } : {}),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await extractErrorMessage(res));
  }
  return res.json();
}

/** `signal` only for read-like POSTs (a preview); a write is never cancelled half-sent. */
export const apiPost = <T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> => apiSend<T>("POST", path, body, signal);
export const apiPut = <T>(path: string, body: unknown): Promise<T> => apiSend<T>("PUT", path, body);
export const apiPatch = <T>(path: string, body: unknown): Promise<T> => apiSend<T>("PATCH", path, body);
export const apiDelete = <T>(path: string): Promise<T> => apiSend<T>("DELETE", path);
