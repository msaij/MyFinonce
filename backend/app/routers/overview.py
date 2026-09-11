import datetime
from typing import Optional

from fastapi import APIRouter

from app.core.serialize import df_to_records, sanitize_floats
from app.db import queries as db
from app.schemas.overview import OverviewKpis, OverviewStats

router = APIRouter(prefix="/api/overview", tags=["overview"])


@router.get("/stats", response_model=OverviewStats)
def stats() -> OverviewStats:
    s = db.get_market_overview_stats()
    return OverviewStats(
        total_schemes=s["total_schemes"],
        total_amcs=s["total_amcs"],
        total_nav_records=s["total_nav_records"],
        min_date=s["min_date"],
        max_date=s["max_date"],
        asset_dist=df_to_records(s["asset_dist"]),
        top_amcs=df_to_records(s["top_amcs"]),
        best_cat=sanitize_floats(s["best_cat"]),
    )


@router.get("/kpis", response_model=OverviewKpis)
def kpis(
    plan_type: str = "All Plans",
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> OverviewKpis:
    k = db.get_kpis(plan_type=plan_type, start_date=start_date, end_date=end_date)
    return OverviewKpis(**sanitize_floats(k))


@router.get("/macro-trend")
def macro_trend(
    start_date: datetime.date,
    end_date: datetime.date,
    plan_type: str = "All Plans",
) -> list[dict]:
    df = db.get_macro_asset_class_trend(start_date, end_date, plan_type=plan_type)
    return df_to_records(df)


@router.get("/category-matrix")
def category_matrix(broad_category: str = "All") -> list[dict]:
    df = db.get_category_performance_matrix(broad_category=broad_category)
    return df_to_records(df)
