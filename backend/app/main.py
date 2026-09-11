"""FastAPI backend entrypoint.

Startup event mirrors fetcher/Overview.py's role as the Streamlit app's
bootstrap point (db.init_db() + amfi_sync.ensure_sync_daemon_running()) --
except ensure_sync_daemon_running() is itself gated behind
settings.enable_sync_daemon (default False), so during the migration this
just calls init_db()'s read side effects are harmless no-ops against an
already-initialized DB, and the daemon genuinely does not start. See
app/core/config.py and the migration plan's "DuckDB concurrency decision"
(../../.claude/plans/floofy-petting-mountain.md).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import meta, schemes, screener

app = FastAPI(title="Indian Mutual Funds API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta.router)
app.include_router(schemes.router)
app.include_router(screener.router)


@app.on_event("startup")
def on_startup() -> None:
    from app import amfi_sync
    from app.db import queries as db

    if settings.enable_sync_daemon:
        db.init_db()
    amfi_sync.ensure_sync_daemon_running()


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "enable_sync_daemon": settings.enable_sync_daemon,
    }
