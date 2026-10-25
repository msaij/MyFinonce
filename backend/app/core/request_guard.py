"""Refuses requests that did not come from this app: the only thing between a web page the
owner happens to have open and the unauthenticated holdings ledger.

Two rules, checked before any route runs:

1. **Host.** The `Host` hostname, and every hop in `X-Forwarded-Host`, must be one of
   `settings.allowed_hosts` (localhost, 127.0.0.1 and the compose name `backend`). The
   compose name is the frontend proxy's alone: it is accepted only as `Host`, and only with
   an `X-Forwarded-Host` naming the browser's side. A browser never addresses `backend`
   itself -- and on Windows that single-label name falls through to LLMNR/NetBIOS, which
   any machine on the LAN can answer, so a page at http://backend:3000 rebound to
   127.0.0.1 must not pass as the proxy.
   That is the DNS-rebinding defence: a page on `x.attacker.tld:3000` that has re-pointed its
   own name at 127.0.0.1 counts as same-origin to the browser, so CORS never stops it -- but
   its requests still carry `Host: x.attacker.tld`. Starlette's TrustedHostMiddleware checks
   `Host` alone, and behind the frontend that is always `backend:8000`: Next's rewrite proxy
   (next/dist/server/lib/router-utils/proxy-request.js) uses `changeOrigin: true`. The same
   proxy sets `x-forwarded-host` to the browser's own Host, after the client's headers, so a
   client cannot spoof it through :3000. If a Next upgrade ever stops setting it, the :3000
   side of this check goes quiet -- re-run the attacker-Host curl through :3000 after one.

2. **Cross-site.** CORS only decides whether a cross-origin page may READ a response; a
   cross-site `<form>`, `<img>` or `fetch(..., {mode: "no-cors"})` still runs. Routes whose
   inputs are all in the query string or path (e.g. POST .../sip-mandates/{id}/generate)
   would write to the ledger from any site. So: unless the Origin is the app's own, refuse
   a request the browser marks `Sec-Fetch-Site: cross-site`, and any non-GET/HEAD/OPTIONS
   request that carries a foreign Origin. A request with neither header (curl, the compose
   healthcheck, scripts\\dev.ps1, TestClient) is not a browser and passes.

Escape hatches if this ever locks the owner out: widen ALLOWED_HOSTS, or remove the one
`app.add_middleware(RequestGuardMiddleware)` line in main.py (the backend is bind-mounted).
"""

import functools
import logging
from typing import Optional, Tuple

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings

#: The frontend, by both spellings. Also what CORS allows (main.py).
APP_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")
#: The API's own pages (/docs), which call the API from :8000 itself.
_SELF_ORIGINS = ("http://localhost:8000", "http://127.0.0.1:8000")
_TRUSTED_ORIGINS = frozenset(APP_ORIGINS + _SELF_ORIGINS)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: Names only the frontend's proxy uses (docker-compose service names), never a browser.
_PROXY_HOSTS = frozenset({"backend"})

_log = logging.getLogger("security")


@functools.lru_cache(maxsize=8)
def _allowed(raw: str) -> frozenset:
    # Keyed on the raw string, so a change to settings.allowed_hosts (conftest adds
    # "testserver") takes effect without rebuilding the middleware.
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def _hostname(value: str) -> str:
    """`Host`-style value -> lowercase hostname without the port; IPv6 keeps its brackets."""
    v = value.strip().lower()
    if v.startswith("["):
        end = v.find("]")
        return v[: end + 1] if end != -1 else v
    return v.split(":", 1)[0]


def refusal(method: str, headers: Headers) -> Optional[Tuple[int, str, str]]:
    """(status, detail, reason for the log) when the request must be refused, else None."""
    allowed = _allowed(settings.allowed_hosts)
    host = headers.get("host")
    if not host or _hostname(host) not in allowed:
        return 400, "Invalid host header", f"host={host!r}"
    forwarded = headers.get("x-forwarded-host")
    if forwarded is None and _hostname(host) in _PROXY_HOSTS:
        return 400, "Invalid host header", f"host={host!r} without x-forwarded-host"
    if forwarded is not None:
        for hop in forwarded.split(","):
            if _hostname(hop) not in allowed or _hostname(hop) in _PROXY_HOSTS:
                return 400, "Invalid host header", f"x-forwarded-host={forwarded!r}"

    origin = headers.get("origin")
    trusted = origin is not None and origin.strip().rstrip("/").lower() in _TRUSTED_ORIGINS
    if not trusted:
        cross_site = headers.get("sec-fetch-site", "").strip().lower() == "cross-site"
        unsafe_with_origin = origin is not None and method.upper() not in _SAFE_METHODS
        if cross_site or unsafe_with_origin:
            return 403, "Cross-site request refused", f"origin={origin!r} sec-fetch-site={headers.get('sec-fetch-site')!r}"
    return None


class RequestGuardMiddleware:
    """Pure ASGI (no BaseHTTPMiddleware), and HTTP only: SSE included, lifespan untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        refused = refusal(scope["method"], Headers(scope=scope))
        if refused is None:
            await self.app(scope, receive, send)
            return
        status, detail, reason = refused
        _log.warning("Refused %s %s (%d %s): %s", scope["method"], scope.get("path", ""), status, detail, reason)
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
