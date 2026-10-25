"""NSE IPO page: live public-issue data from nseindia.com (see app/nse_ipo_client.py)."""

from typing import Callable, Optional

from fastapi import APIRouter, HTTPException

from app import nse_ipo_client as nse
from app.core.serialize import sanitize_floats

router = APIRouter(prefix="/api/nse-ipo", tags=["nse-ipo"])


def _fetch(fn: Callable, *args):
    try:
        return sanitize_floats(fn(*args))
    except nse.NseUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/current")
def current() -> list[dict]:
    return _fetch(nse.current_issues)


@router.get("/upcoming")
def upcoming() -> list[dict]:
    return _fetch(nse.upcoming_issues)


@router.get("/detail")
def detail(symbol: str, series: Optional[str] = None) -> dict:
    return _fetch(nse.issue_detail, symbol, series)
