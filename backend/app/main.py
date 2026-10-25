"""FastAPI backend entrypoint.

Startup event: db.init_db() (idempotent -- creates tables if missing),
app_logging's capture install + SSE event-loop registration, then
amfi_sync.ensure_sync_daemon_running() (itself gated on
settings.enable_sync_daemon, which defaults to True -- see core/config.py's
docstring for the history of why that default changed over the course of
the migration off the original Streamlit app). This backend is the sole
writer of its own database, so ENABLE_SYNC_DAEMON has only ever been about
avoiding disruption during active development, never cross-process safety.
"""

import asyncio
import json
import logging
import time

import psycopg
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import product_flags, settings
from app.core.request_guard import APP_ORIGINS, RequestGuardMiddleware
from app.core.serialize import sanitize_floats
from app.routers import admin, backtest, holdings, leaders, meta, news, nse_ipo, overview, quant, schemes, screener

app = FastAPI(title="Indian Mutual Funds API", version="0.1.0")
_access_log = logging.getLogger("access")
_error_log = logging.getLogger("errors")

app.add_middleware(
    CORSMiddleware,
    # Both spellings of the same local frontend: the browser's Origin is whatever the
    # user typed, and only the Data Management page's SSE log stream is cross-origin
    # at all (it bypasses Next's proxy, which gzips and so breaks SSE -- see
    # frontend/lib/hooks.ts). With 127.0.0.1 missing, opening the app at
    # http://127.0.0.1:3000 left that tab stuck on "Connecting..." with no entries,
    # while http://localhost:3000 worked -- the same app, a different URL.
    allow_origins=list(APP_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Host and cross-site checks (see core/request_guard.py). Added after CORS, so it runs
# before it -- and inside the access log below, so its refusals are logged too. Removing
# this one line is the escape hatch if it ever locks the owner out.
app.add_middleware(RequestGuardMiddleware)


def _write_access_line(request: Request, status: int, t0: float) -> None:
    ms = (time.perf_counter() - t0) * 1000.0
    try:
        from app.db.connection import get_data_version
        dv = get_data_version()
    except Exception:
        dv = None
    _access_log.info(json.dumps({
        "method": request.method,
        "path": request.url.path,
        "status": status,
        "ms": round(ms, 1),
        "data_version": dv,
    }))


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Unhandled: Starlette's ServerErrorMiddleware, outside this one, turns it into the
        # 500 -- so without this the failed requests were the ones missing from this log.
        _write_access_line(request, 500, t0)
        _error_log.exception("Unhandled error on %s %s", request.method, request.url.path)
        raise
    _write_access_line(request, response.status_code, t0)
    return response


@app.middleware("http")
async def no_store_holdings(request: Request, call_next):
    # The owner's ledger -- every summary, the CSV export, the full backup -- is personal
    # data: nothing under /api/holdings belongs in the browser's disk cache.
    response = await call_next(request)
    if request.url.path.startswith("/api/holdings"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(request: Request, exc: psycopg.OperationalError) -> JSONResponse:
    # Postgres down, restarting or overloaded: a pool timeout (psycopg_pool.PoolTimeout is a
    # subclass), a dropped connection or a statement timeout. A 503 with a reason the page
    # can show, rather than a generic 500 that reads like a bug.
    _error_log.warning("Database unavailable on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": "Database unavailable or timed out. Try again shortly."})


# Constraint names the owner can hit from a page, said in the page's words.
_CONSTRAINT_MESSAGES = {
    "uq_portfolios_active_name": "A portfolio with that name is already active. Rename one of them first.",
    "uq_htxn_mandate_instalment": "That SIP instalment is already recorded.",
}


@app.exception_handler(psycopg.IntegrityError)
async def constraint_conflict(request: Request, exc: psycopg.IntegrityError) -> JSONResponse:
    # A write that breaks a uniqueness, foreign-key or check rule conflicts with what is
    # stored -- two requests racing to the same name, an instalment already written. A 409
    # that says so, rather than a 500 with the reason only in the server log.
    diag = getattr(exc, "diag", None)
    name = getattr(diag, "constraint_name", None)
    detail = _CONSTRAINT_MESSAGES.get(name) or getattr(diag, "message_primary", None) or "The change conflicts with stored data."
    _error_log.warning("Constraint conflict on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=409, content={"detail": detail})


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's own handler, but JSON-safe: it echoes each bad input back, and a JSON NaN or
    # Infinity (which Python's parser accepts) made the 422 itself fail to encode -- a 500.
    return JSONResponse(status_code=422, content={"detail": sanitize_floats(jsonable_encoder(exc.errors()))})


@app.exception_handler(psycopg.DataError)
async def value_out_of_range(request: Request, exc: psycopg.DataError) -> JSONResponse:
    # A value the column can't hold (a number too large for its NUMERIC precision, say).
    diag = getattr(exc, "diag", None)
    _error_log.warning("Rejected value on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=422, content={"detail": getattr(diag, "message_primary", None) or "A value is out of range."})

app.include_router(meta.router)
app.include_router(overview.router)
app.include_router(schemes.router)
app.include_router(screener.router)
app.include_router(leaders.router)
app.include_router(backtest.router)
app.include_router(quant.router)
app.include_router(admin.router)
app.include_router(holdings.router)
app.include_router(nse_ipo.router)
app.include_router(news.router)


@app.on_event("startup")
async def on_startup() -> None:
    from app import amfi_sync, app_logging
    from app.db import queries as db
    from app.services import news_service

    db.init_db()
    app_logging.ensure_log_capture_installed()
    app_logging.set_main_loop(asyncio.get_running_loop())  # lets log emits from any thread reach SSE subscribers
    amfi_sync.ensure_sync_daemon_running()  # itself gated on settings.enable_sync_daemon
    news_service.ensure_news_daemon_running()  # itself gated on settings.enable_news_daemon
    _check_factor_proxy_freshness()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    # The news daemon is a daemon=True thread, so nothing else would stop it: a
    # TestClient exit or a --reload would leave it sweeping against a closed pool.
    from app.services import news_service

    news_service.stop_news_daemon()


def _check_factor_proxy_freshness() -> None:
    try:
        from app.factor_model import get_factor_proxies
        from app.db.connection import get_connection
        import datetime
        proxies = get_factor_proxies()
        cutoff = datetime.date.today() - datetime.timedelta(days=7)
        con = get_connection()
        try:
            for key, code in proxies.items():
                row = con.execute(
                    "SELECT max(nav_date) FROM nav_history WHERE scheme_code = %s", (int(code),)
                ).fetchone()
                last = row[0] if row else None
                if last is None or last < cutoff:
                    logging.getLogger("factor_model").warning(
                        "Factor proxy %s (scheme %s) last NAV %s is older than 7 sessions",
                        key, code, last,
                    )
        finally:
            con.close()
    except Exception:
        pass


@app.get("/api/health")
def health() -> dict:
    extra: dict = {
        "status": "ok",
        "enable_sync_daemon": settings.enable_sync_daemon,
        "flags": product_flags(),
    }
    try:
        from app.db.connection import get_pool_stats
        extra.update(get_pool_stats())
    except Exception:
        extra["pool_checked_out"] = None
    try:
        from app.db import queries as db
        stats = db.get_database_stats()
        extra["nav_max_date"] = str(stats.get("max_date") or "") or None
        extra["ter_official_schemes"] = stats.get("ter_official_schemes", 0)
        q = db.get_data_quality()
        extra["factor_market_last_nav"] = q.get("factor_market_last_nav")
        extra["factor_momentum_last_nav"] = q.get("factor_momentum_last_nav")
        extra["factor_proxy_codes"] = q.get("factor_proxy_codes") or {}
    except Exception:
        extra["nav_max_date"] = None
        extra["factor_proxy_codes"] = {}
    try:
        from app import amfi_sync
        ter_hist = amfi_sync.get_ter_sync_history()
        extra["last_ter_success_at"] = ter_hist.get("last_success_at")
        extra["last_ter_success_msg"] = ter_hist.get("last_success_msg")
    except Exception:
        extra["last_ter_success_at"] = None
    try:
        from app import metrics as app_metrics
        extra["counters"] = app_metrics.get_all()
    except Exception:
        pass
    try:
        from app.services import news_service
        extra["news"] = news_service.get_health_summary()
    except Exception:
        extra["news"] = None
    return extra
