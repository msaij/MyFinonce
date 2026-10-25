"""FastAPI router for the global financial news feed.

GET  /api/news          articles, one card per story, newest first, plus feed freshness
GET  /api/news/sources  per-feed health: what answered, what is stale or failing, and why
POST /api/news/refresh  asks for a sweep now (single-flight, at most one a minute)
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Response
from pydantic import BaseModel

from app.services import news_service

router = APIRouter(prefix="/api/news", tags=["news"])

# The slugs the UI sends. A label ("Central Banks") or a typo is a 422, not a silent
# empty list. test_news.py pins these to news_service.CATEGORY_SLUGS / REGION_SLUGS.
CategorySlug = Literal["central_banks", "macro", "equities", "commodities_forex"]
RegionSlug = Literal["global", "india", "us", "uk", "europe", "asia"]

# Sent by the frontend's refresh call. A custom header makes the browser preflight a
# cross-site request, which the CORS policy refuses, so another site cannot make the
# owner's browser trigger sweeps with a plain form POST.
REFRESH_HEADER_VALUE = "myfinonce"


class RelatedItem(BaseModel):
    id: str
    source: str
    url: str
    title: str
    published_at: Optional[str] = None


class NewsArticleItem(BaseModel):
    id: str
    title: str
    summary: str = ""
    source: str
    url: str
    category: str
    region: str
    published_at: Optional[str] = None
    date_only: bool = False
    sentiment: str = "neutral"
    keywords: list[str] = []
    fetched_at: Optional[str] = None
    paywall: Optional[str] = None
    official: bool = False
    related: list[RelatedItem] = []


class NewsResponse(BaseModel):
    status: str
    total: int  # stories matching the filters
    count: int  # stories in this page
    is_offline: bool = False  # the database was unreachable; articles are the last ones loaded
    is_stale: bool = False  # no successful sweep within the last hour
    articles: list[NewsArticleItem] = []
    last_updated: Optional[str] = None
    cached_at: Optional[str] = None
    last_sweep_at: Optional[str] = None
    last_sweep_ok_at: Optional[str] = None
    feeds_ok: int = 0
    feeds_total: int = 0
    feeds_stale: int = 0
    feeds_failing: int = 0
    feeds_pending: int = 0
    retention_days: int = news_service.NEWS_RETENTION_DAYS


class FeedHealth(BaseModel):
    name: str
    source: str
    url: str
    category: str
    region: str
    official: bool
    paywall: Optional[str] = None
    state: str
    last_attempt_at: Optional[str] = None
    last_ok_at: Optional[str] = None
    http_status: Optional[int] = None
    items_parsed: int = 0
    items_kept: int = 0
    items_new: int = 0
    newest_item_at: Optional[str] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    backoff_until: Optional[str] = None
    dropped: dict[str, int] = {}
    stored: int = 0
    stored_newest_at: Optional[str] = None
    stale_after_days: int


class SourcesResponse(BaseModel):
    feeds: list[FeedHealth]
    feeds_ok: int
    feeds_total: int
    feeds_stale: int
    feeds_failing: int
    feeds_pending: int
    last_sweep_at: Optional[str] = None
    last_sweep_ok_at: Optional[str] = None
    is_stale: bool
    next_sweep_at: Optional[str] = None
    daemon_running: bool
    retention_days: int


class RefreshResponse(BaseModel):
    status: str  # ok | failed | recent | busy
    message: str
    details: dict[str, Any] = {}


@router.get("", response_model=NewsResponse)
def get_news(
    category: Optional[CategorySlug] = Query(None, description="Category slug; omit for all"),
    region: Optional[RegionSlug] = Query(None, description="Region slug; omit for all"),
    limit: int = Query(50, ge=1, le=200, description="Max stories to return"),
    search: Optional[str] = Query(None, max_length=100, description="Matches title, summary or source"),
) -> dict:
    return news_service.get_cached_news(category=category, region=region, limit=limit, search=search)


@router.get("/sources", response_model=SourcesResponse)
def get_sources() -> dict:
    return news_service.get_sources_status()


@router.post("/refresh", response_model=RefreshResponse)
def refresh_news(
    response: Response,
    x_requested_with: Optional[str] = Header(None),
) -> dict:
    if x_requested_with != REFRESH_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="Refresh must come from the app (X-Requested-With header missing).")
    res = news_service.request_manual_refresh()
    status = res.get("status", "ok")
    if status == "busy":
        response.status_code = 202
        message = "A sweep is already running; its results appear when it finishes."
    elif status == "recent":
        message = f"Feeds were swept {res.get('seconds_since', 0):.0f}s ago; showing that sweep."
    elif status == "failed":
        message = (f"Sweep finished, but only {res.get('feeds_ok', 0)} of {res.get('feeds_total', 0)} "
                   "feeds answered or the database write failed.")
    else:
        message = f"News feeds refreshed: {res.get('inserted', 0)} new articles."
    return {"status": status, "message": message, "details": res}
