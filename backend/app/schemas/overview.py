import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class NamedReturn(BaseModel):
    name: str
    return_pct: Optional[float] = None


class OverviewStats(BaseModel):
    """Backs GET /api/overview/stats -- db.get_market_overview_stats()."""

    total_schemes: int
    total_amcs: int
    total_nav_records: int
    min_date: Optional[datetime.date]
    max_date: Optional[datetime.date]
    asset_dist: List[Dict[str, Any]]  # broad_category, count, avg_1d/7d/30d/90d/1y, med_30d/90d
    top_amcs: List[Dict[str, Any]]  # fund_house, schemes_count, avg_30d/90d/1y
    best_cat: Optional[NamedReturn]


class OverviewKpis(BaseModel):
    """Backs GET /api/overview/kpis -- db.get_kpis()."""

    total_schemes: int
    latest_date: Optional[datetime.date]
    top_performer: Optional[NamedReturn]
    lag_performer: Optional[NamedReturn]
    advancers: int
    decliners: int
    unchanged: int
    median_return: float
    avg_return: float
    best_cat: Optional[NamedReturn]
