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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import product_flags, settings
from app.routers import admin, backtest, holdings, leaders, meta, overview, portfolio_advisor, quant, schemes, screener

app = FastAPI(title="Indian Mutual Funds API", version="0.1.0")
_access_log = logging.getLogger("access")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000.0
    try:
        from app.db.connection import get_data_version
        dv = get_data_version()
    except Exception:
        dv = None
    _access_log.info(json.dumps({
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "ms": round(ms, 1),
        "data_version": dv,
    }))
    return response

app.include_router(meta.router)
app.include_router(overview.router)
app.include_router(schemes.router)
app.include_router(screener.router)
app.include_router(leaders.router)
app.include_router(backtest.router)
app.include_router(portfolio_advisor.router)
app.include_router(quant.router)
app.include_router(admin.router)
app.include_router(holdings.router)


@app.on_event("startup")
async def on_startup() -> None:
    from app import amfi_sync, app_logging
    from app.db import queries as db

    db.init_db()
    app_logging.ensure_log_capture_installed()
    app_logging.set_main_loop(asyncio.get_running_loop())  # lets log emits from any thread reach SSE subscribers
    amfi_sync.ensure_sync_daemon_running()  # itself gated on settings.enable_sync_daemon
    _check_factor_proxy_freshness()


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
    return extra
