import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.core.serialize import df_to_records, sanitize_floats
from app.db import queries as db

router = APIRouter(prefix="/api/schemes", tags=["schemes"])


@router.get("/search")
def search(
    q: str = Query("", alias="q"),
    amc: Optional[str] = None,
    broad_cat: Optional[str] = None,
    sub_cat: Optional[str] = None,
    plan_type: Optional[str] = None,
    option_type: Optional[str] = None,
    limit: int = 25,
) -> list[dict]:
    """Backs the SearchCombobox component on every page that used st_searchbox
    (Overview, Screener, Compare & Simulate, Leaders & Laggards, Quant Analysis) --
    same underlying query, different filter params per page."""
    return db.get_schemes_for_dropdown(
        amc=amc, broad_cat=broad_cat, sub_cat=sub_cat, plan_type=plan_type,
        option_type=option_type, search_term=q, limit=limit,
    )


@router.get("/nav-history")
def nav_history(
    codes: str = Query(..., description="Comma-separated scheme codes"),
    start: Optional[datetime.date] = None,
    end: Optional[datetime.date] = None,
) -> list[dict]:
    """NOTE: registered before /{scheme_code} deliberately -- FastAPI matches
    path routes in registration order, and both this and /{scheme_code} are
    single-segment paths. If /{scheme_code} came first, a request to
    /api/schemes/nav-history would be captured there instead (scheme_code:int
    would then fail to parse "nav-history" and return a 422 instead of ever
    reaching this endpoint)."""
    scheme_codes = [int(c) for c in codes.split(",") if c.strip()]
    df = db.get_nav_history_dataframe(scheme_codes, start_date=start, end_date=end)
    return df_to_records(df)


@router.get("/{scheme_code}/ter-history")
def ter_history(scheme_code: int) -> list[dict]:
    df = db.get_scheme_ter_history(scheme_code)
    return df_to_records(df)


@router.get("/{scheme_code}")
def get_scheme(scheme_code: int) -> dict:
    profile, nav_history_df = db.get_scheme_profile(scheme_code)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Scheme {scheme_code} not found")
    return {
        "profile": sanitize_floats(profile),
        "nav_history": df_to_records(nav_history_df),
    }
