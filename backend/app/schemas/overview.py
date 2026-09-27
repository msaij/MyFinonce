import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class NamedReturn(BaseModel):
    name: str
    return_pct: Optional[float] = None


class OverviewStats(BaseModel):
    """Backs GET /api/overview/stats -- db.get_market_overview_stats()."""

    #: Every scheme ever listed, including matured and wound-up ones.
    total_schemes: int
    #: Those still publishing NAVs -- what "the market" means on this page today.
    active_schemes: int
    total_amcs: int
    total_nav_records: int
    min_date: Optional[datetime.date]
    max_date: Optional[datetime.date]
    #: Schemes whose returns are averaged (Growth-type plans), and the IDCW plans left out.
    returns_pool: int
    idcw_excluded: int
    asset_classes: List[str]
    asset_dist: List[Dict[str, Any]]  # asset_class, count, avg_/med_/n_ per horizon, aum_cr, aum_share_pct
    amc_aum: List[Dict[str, Any]]  # fund_house, aum_cr, share_pct, funds -- AMFI's reported assets
    amc_scorecard: List[Dict[str, Any]]  # fund_house, ranked_schemes, median_alpha_1y, beat_peers_pct, ...
    aum_total_cr: Optional[float]
    aum_as_of: Optional[datetime.date]
    best_cat: Optional[NamedReturn]


class PulseKpis(BaseModel):
    """Backs GET /api/overview/pulse-kpis: the page-wide headline tiles for the date window,
    over the same fair pool the Leaders tab ranks (whole window, no IDCW payouts)."""

    ranked_funds: int
    advancers: int
    decliners: int
    unchanged: int
    median_return: Optional[float]
    mean_return: Optional[float]
    top_alpha: Optional[Dict[str, Any]]
    leading_category: Optional[Dict[str, Any]]
    lagging_category: Optional[Dict[str, Any]]
    excluded_partial_window: int
    excluded_idcw: int
    first_nav_date: Optional[datetime.date]
    last_nav_date: Optional[datetime.date]


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
