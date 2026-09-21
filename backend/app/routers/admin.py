"""Data Management page's backend -- ported from fetcher/pages/5_Data_Management.py.
Every sync/backfill function this calls already existed, ported verbatim
(dialect fixes aside) in Phase 1/7's amfi_sync.py and db/queries.py; this router
is purely the HTTP surface over them, plus the new SSE log stream (app_logging.py).
"""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import amfi_sync, app_logging, auth
from app.core.config import settings
from app.core.serialize import sanitize_floats
from app.db import queries as db
from app.schemas.admin import HistoricalBackfillStartRequest, TerBackfillStartRequest

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(request: Request) -> None:
    auth.require_admin_token(request)


@router.get("/status")
def get_status() -> dict:
    """Everything the original page's 4 tabs need, fetched once -- mirrors the original
    page's own "shared data, fetched once up top" comment, just moved server-side."""
    stats = db.get_database_stats()
    stale, current_max, expected = amfi_sync.is_database_stale()
    return sanitize_floats(
        {
            "stats": stats,
            "staleness": {"is_stale": stale, "current_max_date": current_max, "expected_date": expected},
            "sync_history": amfi_sync.get_sync_history(),
            "ter_sync_history": amfi_sync.get_ter_sync_history(),
            "cost_coverage": db.get_cost_data_coverage(),
            "ter_backfill_status": amfi_sync.get_ter_backfill_status(),
            "backfill_status": amfi_sync.get_backfill_status(),
            # Durable, unlike backfill_status, which only describes the current
            # process's run: this survives a stop, a crash and a restart, and is
            # what tells the user how much of the multi-year range they actually have.
            "backfill_progress": amfi_sync.get_backfill_progress(),
            "ter_portal_url": amfi_sync.TER_PORTAL_PAGE_URL,
            "enable_sync_daemon": settings.enable_sync_daemon,
        }
    )


class FactorProxyUpdate(BaseModel):
    factor_proxy_market: Optional[int] = None
    factor_proxy_market_fallback: Optional[int] = None
    factor_proxy_market_fallback_2: Optional[int] = None
    factor_proxy_momentum: Optional[int] = None


@router.get("/factor-proxies")
def get_factor_proxies() -> dict:
    from app.factor_model import DEFAULT_FACTOR_PROXIES, get_factor_proxies as _load
    return {"proxies": _load(), "defaults": DEFAULT_FACTOR_PROXIES}


@router.post("/factor-proxies")
def set_factor_proxies(req: FactorProxyUpdate, _auth: None = Depends(_require_admin)) -> dict:
    from app.factor_model import DEFAULT_FACTOR_PROXIES, _cached_base_factor_df, _factor_lock
    import app.factor_model as fm
    payload = req.model_dump(exclude_none=True)
    for key, val in payload.items():
        if key not in DEFAULT_FACTOR_PROXIES:
            raise HTTPException(status_code=422, detail=f"Unknown proxy key {key}")
        db.set_sync_meta_value(key, str(int(val)))
    with _factor_lock:
        fm._cached_base_factor_df = None
        fm._factor_cache_time = 0.0
        fm._cached_factor_source = None
    return {"success": True, "proxies": fm.get_factor_proxies()}


# --- Manual actions (all synchronous/blocking, matching the original's st.spinner() UX --
# a page load fires a POST and waits for it to resolve, exactly as the original blocked on
# the button click). ---


@router.post("/sync/daily")
def trigger_daily_sync(_auth: None = Depends(_require_admin)) -> dict:
    success, msg = amfi_sync.sync_daily_nav(_trigger="manual")
    if not success:
        raise HTTPException(status_code=502, detail=f"Sync failed: {msg}")
    return {"success": True, "message": msg}


@router.post("/sync/ter")
def trigger_ter_sync(_auth: None = Depends(_require_admin)) -> dict:
    success, msg = amfi_sync.sync_official_ter(_trigger="manual")
    if not success:
        raise HTTPException(status_code=502, detail=f"TER sync failed: {msg}")
    return {"success": True, "message": msg}


@router.post("/recompute-summary")
def recompute_summary(_auth: None = Depends(_require_admin)) -> dict:
    stats = db.get_database_stats(force_refresh=True)
    db.refresh_summary_table()
    return {"success": True, "message": f"Summary table recomputed and indexed across {stats['schemes_count']:,} schemes."}


# --- TER backfill (background thread; poll /status for progress) ---


@router.post("/backfill/ter/start")
def start_ter_backfill(req: TerBackfillStartRequest, _auth: None = Depends(_require_admin)) -> dict:
    ok = amfi_sync.start_ter_backfill(n_months=req.n_months)
    if not ok:
        raise HTTPException(status_code=409, detail="A TER backfill job is already running.")
    return {"success": True}


@router.post("/backfill/ter/stop")
def stop_ter_backfill(_auth: None = Depends(_require_admin)) -> dict:
    amfi_sync.stop_ter_backfill()
    return {"success": True, "message": "Stop signal sent. If a backfill is active, it will finish the current month and halt."}


# --- Multi-year historical NAV backfill (background thread; poll /status for progress) ---


@router.get("/backfill/chunk-count")
def backfill_chunk_count(start_year: int = Query(2020, ge=2000)) -> dict:
    """Lets the UI show/recompute an accurate chunk estimate for whatever start year the
    user picks -- start_historical_backfill itself already accepts any start_year;
    the Query default here is just this endpoint's own fallback when called with none."""
    return {"start_year": start_year, "total_chunks": len(amfi_sync.generate_backfill_chunks(start_year))}


@router.post("/backfill/historical/start")
def start_historical_backfill(req: HistoricalBackfillStartRequest, _auth: None = Depends(_require_admin)) -> dict:
    ok = amfi_sync.start_historical_backfill(
        start_year=req.start_year, max_chunks=req.max_chunks, resume=req.resume)
    if not ok:
        raise HTTPException(status_code=409, detail="A backfill job is already running.")
    return {"success": True}


@router.post("/backfill/historical/stop")
def stop_historical_backfill(_auth: None = Depends(_require_admin)) -> dict:
    amfi_sync.stop_historical_backfill()
    return {"success": True, "message": "Stop signal sent. Worker will finish current chunk and halt."}


# --- Live activity log ---


@router.get("/logs")
def get_logs(limit: int = 150, min_level: str = "INFO", contains: str = "") -> dict:
    entries = app_logging.get_recent_logs(limit=limit, min_level=min_level, contains=contains)
    return sanitize_floats({"entries": entries, "total_captured": app_logging.buffer_size()})


def _sse_format(entry: dict) -> str:
    payload = {**entry, "time": entry["time"].isoformat()}
    return f"data: {json.dumps(payload)}\n\n"


@router.get("/logs/stream")
async def stream_logs(request: Request, min_level: str = Query("INFO"), contains: str = Query("")) -> StreamingResponse:
    """Server-Sent Events push of new log entries -- replaces the original page's
    st.fragment(run_every="5s") polling. Replays the current buffer (oldest first, so
    a client renders it top-to-bottom like a log) on connect, then pushes each new
    entry the moment it's logged; a periodic comment keeps the connection alive
    through proxies that time out an idle stream."""

    async def event_gen():
        for entry in reversed(app_logging.get_recent_logs(limit=150, min_level=min_level, contains=contains)):
            yield _sse_format(entry)

        queue = await app_logging.subscribe()
        try:
            while True:
                # Explicit disconnect check, not just the finally block below: a client that
                # goes away without a clean TCP close (e.g. the browser tab closed mid-request,
                # or -- observed directly during this app's own development -- the backend
                # container itself got recreated out from under an open connection) doesn't
                # reliably raise here on its own promptly. Checking this each loop iteration is
                # what actually ends an abandoned generator instead of leaving it parked forever.
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if app_logging.entry_matches(entry, min_level, contains):
                    yield _sse_format(entry)
        finally:
            app_logging.unsubscribe(queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
