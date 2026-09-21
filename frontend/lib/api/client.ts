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

export async function apiGet<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }
  }
  const res = await fetch(url.toString());
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

async function extractErrorMessage(res: Response): Promise<string> {
  const body = await res.text().catch(() => "");
  if (body) {
    try {
      const parsed = JSON.parse(body);
      if (typeof parsed?.detail === "string") return parsed.detail;
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

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = path.startsWith("/api/admin/") ? getAdminToken() : null;
  if (token) headers["X-Admin-Token"] = token;
  const res = await fetch(path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ApiError(res.status, await extractErrorMessage(res));
  }
  return res.json();
}
