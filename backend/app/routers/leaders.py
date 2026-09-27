import datetime
from typing import Optional

from fastapi import APIRouter

from app.core.serialize import df_to_records, sanitize_floats
from app.classification import ASSET_CLASSES
from app.db import queries as db
from app.services import leaders as leaders_service

router = APIRouter(prefix="/api/leaders", tags=["leaders"])


@router.get("/rotation")
def get_rotation(
    start: datetime.date,
    end: datetime.date,
    broad_cat: Optional[str] = None,
    plan_type: Optional[str] = None,
    option_type: Optional[str] = None,
) -> dict:
    return sanitize_floats(db.get_category_rotation(
        start_date=start, end_date=end, plan_type=plan_type or "All Plans",
        option_type=option_type or "All Options", broad_cat=broad_cat or "All Categories"))


@router.get("")
def get_leaders(
    broad_cat: Optional[str] = None,
    sub_cat: Optional[str] = None,
    plan_type: Optional[str] = None,
    option_type: Optional[str] = None,
    search: str = "",
    start: Optional[datetime.date] = None,
    end: Optional[datetime.date] = None,
    vol_lookback: str = "1Y",
) -> dict:
    if start and end and start > end:
        start, end = end, start
    df_all = db.get_advanced_leaders_dataframe(
        broad_cat=broad_cat or "All Categories",
        sub_cat=sub_cat or "All Sub-Categories",
        plan_type=plan_type or "All Plans",
        option_type=option_type or "All Options",
        start_date=start,
        end_date=end,
        vol_lookback=vol_lookback,
    )
    exclusions = dict(df_all.attrs.get("exclusions", {}))
    window = dict(df_all.attrs.get("window", {}))
    # Filter choices from every fund ranked in this window (not just the filtered ones), so
    # the dropdowns offer exactly what exists: asset class -> its categories.
    categories_by_class: dict = dict(df_all.attrs.get("categories_by_class", {}))
    official = db.get_official_returns_by_scheme()
    if not df_all.empty and not official.empty:
        df_all = df_all.merge(official, on="scheme_code", how="left")
    result = leaders_service.build_leaders_dataset(df_all, search_query=search)
    return {
        "rows": df_to_records(result["rows"]),
        "excluded_thin_data": result["excluded_thin_data"],
        "excluded_partial_window": exclusions.get("partial_window", 0),
        "excluded_idcw": exclusions.get("idcw", 0),
        "window": sanitize_floats({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in window.items()}),
        "asset_classes": [c for c in ASSET_CLASSES if c in categories_by_class],
        "categories_by_class": {k: sorted(v) for k, v in categories_by_class.items()},
        "total_funds": result["total_funds"],
        "advancers": result["advancers"],
        "decliners": result["decliners"],
        "market_median_return": sanitize_floats(result["market_median_return"]),
        "top_alpha": sanitize_floats(result["top_alpha"]),
        "leading_category": sanitize_floats(result["leading_category"]),
        "lagging_category": sanitize_floats(result["lagging_category"]),
        "med_vol": sanitize_floats(result["med_vol"]),
        "med_ret": sanitize_floats(result["med_ret"]),
        "quadrant_excluded": result["quadrant_excluded"],
        "vol_lookback": vol_lookback,
        "vol_methodology": "Daily sample standard deviation, annualised by the square root of how often "
                           "the fund actually publishes a NAV (about 250 a year for equity, 365 for liquid funds)",
    }
