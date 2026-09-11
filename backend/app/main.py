"""FastAPI backend entrypoint.

Startup event mirrors fetcher/Overview.py's role as the Streamlit app's
bootstrap point: db.init_db() (idempotent -- creates tables if missing) then
amfi_sync.ensure_sync_daemon_running(). Both now run unconditionally at
startup: this backend owns its own SQLite file exclusively (see
app/db/connection.py's module docstring for why DuckDB's shared-file model
was dropped), so there is no second process left to contend with the way
Streamlit's DuckDB file once was -- ENABLE_SYNC_DAEMON stays available as an
explicit off-switch (e.g. a read-only exploration session) but no longer
needs to default to False for safety.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import backtest, leaders, meta, overview, portfolio_advisor, schemes, screener

app = FastAPI(title="Indian Mutual Funds API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router)
app.include_router(overview.router)
app.include_router(schemes.router)
app.include_router(screener.router)
app.include_router(leaders.router)
app.include_router(backtest.router)
app.include_router(portfolio_advisor.router)


@app.on_event("startup")
def on_startup() -> None:
    from app import amfi_sync
    from app.db import queries as db

    db.init_db()
    amfi_sync.ensure_sync_daemon_running()  # itself gated on settings.enable_sync_daemon


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "enable_sync_daemon": settings.enable_sync_daemon,
    }
