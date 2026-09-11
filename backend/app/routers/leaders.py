import datetime
from typing import Optional

from fastapi import APIRouter

from app.core.serialize import df_to_records, sanitize_floats
from app.db import queries as db
from app.services import leaders as leaders_service

router = APIRouter(prefix="/api/leaders", tags=["leaders"])


@router.get("")
def get_leaders(
    broad_cat: Optional[str] = None,
    sub_cat: Optional[str] = None,
    plan_type: Optional[str] = None,
    option_type: Optional[str] = None,
    search: str = "",
    start: Optional[datetime.date] = None,
    end: Optional[datetime.date] = None,
) -> dict:
    df_all = db.get_advanced_leaders_dataframe(
        broad_cat=broad_cat or "All Categories",
        sub_cat=sub_cat or "All Sub-Categories",
        plan_type=plan_type or "All Plans",
        option_type=option_type or "All Options",
        start_date=start,
        end_date=end,
    )
    result = leaders_service.build_leaders_dataset(df_all, search_query=search)
    return {
        "rows": df_to_records(result["rows"]),
        "excluded_thin_data": result["excluded_thin_data"],
        "total_funds": result["total_funds"],
        "advancers": result["advancers"],
        "decliners": result["decliners"],
        "market_median_return": sanitize_floats(result["market_median_return"]),
        "top_alpha": sanitize_floats(result["top_alpha"]),
        "leading_category": sanitize_floats(result["leading_category"]),
        "med_vol": sanitize_floats(result["med_vol"]),
        "med_ret": sanitize_floats(result["med_ret"]),
        "quadrant_excluded": result["quadrant_excluded"],
    }
