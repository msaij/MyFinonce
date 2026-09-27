import datetime
from typing import Optional

from fastapi import APIRouter

from app.core.serialize import df_to_records, sanitize_floats
from app.db import fund_list
from app.db import queries as db
from app.schemas.overview import OverviewKpis, OverviewStats, PulseKpis
from app.services import leaders as leaders_service

router = APIRouter(prefix="/api/overview", tags=["overview"])


@router.get("/stats", response_model=OverviewStats)
def stats(plan_type: str = "All Plans", option_type: str = "All Options") -> OverviewStats:
    s = db.get_market_overview_stats(plan_type=plan_type, option_type=option_type)
    return OverviewStats(
        total_schemes=s["total_schemes"],
        active_schemes=s["active_schemes"],
        total_amcs=s["total_amcs"],
        total_nav_records=s["total_nav_records"],
        min_date=s["min_date"],
        max_date=s["max_date"],
        returns_pool=s["returns_pool"],
        idcw_excluded=s["idcw_excluded"],
        asset_classes=s["asset_classes"],
        asset_dist=df_to_records(s["asset_dist"]),
        amc_aum=df_to_records(s["amc_aum"]),
        amc_scorecard=df_to_records(s["amc_scorecard"]),
        aum_total_cr=s["aum_total_cr"],
        aum_as_of=s["aum_as_of"],
        best_cat=sanitize_floats(s["best_cat"]),
    )


@router.get("/kpis", response_model=OverviewKpis)
def kpis(
    plan_type: str = "All Plans",
    option_type: str = "All Options",
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> OverviewKpis:
    k = db.get_kpis(plan_type=plan_type, option_type=option_type, start_date=start_date, end_date=end_date)
    return OverviewKpis(**sanitize_floats(k))


@router.get("/pulse-kpis", response_model=PulseKpis)
def pulse_kpis(
    start_date: datetime.date,
    end_date: datetime.date,
    plan_type: str = "All Plans",
    option_type: str = "All Options",
) -> PulseKpis:
    """The headline tiles, over the whole market for the window -- deliberately independent
    of the Leaders tab's own asset-class and category filters, which used to move them."""
    df = db.get_advanced_leaders_dataframe(plan_type=plan_type, option_type=option_type,
                                           start_date=start_date, end_date=end_date)
    exclusions = dict(df.attrs.get("exclusions", {}))
    window = dict(df.attrs.get("window", {}))
    res = leaders_service.build_leaders_dataset(df)
    rows = res["rows"]
    returns = rows["period_return_pct"] if not rows.empty else None
    return PulseKpis(
        ranked_funds=res["total_funds"],
        advancers=res["advancers"],
        decliners=res["decliners"],
        unchanged=int((returns == 0).sum()) if returns is not None else 0,
        median_return=sanitize_floats(res["market_median_return"]),
        mean_return=sanitize_floats(float(returns.mean())) if returns is not None and len(returns) else None,
        top_alpha=sanitize_floats(res["top_alpha"]),
        leading_category=sanitize_floats(res["leading_category"]),
        lagging_category=sanitize_floats(res["lagging_category"]),
        excluded_partial_window=exclusions.get("partial_window", 0),
        excluded_idcw=exclusions.get("idcw", 0),
        first_nav_date=window.get("first_nav_date"),
        last_nav_date=window.get("last_nav_date"),
    )


@router.get("/macro-trend")
def macro_trend(
    start_date: datetime.date,
    end_date: datetime.date,
    plan_type: str = "All Plans",
    option_type: str = "All Options",
) -> list[dict]:
    df = db.get_macro_asset_class_trend(start_date, end_date, plan_type=plan_type, option_type=option_type)
    return df_to_records(df)


@router.get("/all-funds")
def all_funds(plan_type: str = "All Plans", option_type: str = "All Options") -> list[dict]:
    return sanitize_floats(fund_list.all_funds(plan_type=plan_type, option_type=option_type))


@router.get("/category-matrix")
def category_matrix(broad_category: str = "All", plan_type: str = "All Plans",
                    option_type: str = "All Options") -> list[dict]:
    df = db.get_category_performance_matrix(broad_category=broad_category, plan_type=plan_type,
                                            option_type=option_type)
    return df_to_records(df)
