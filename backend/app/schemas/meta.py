import datetime
from typing import Optional

from pydantic import BaseModel


class MetaStatus(BaseModel):
    """One consolidated poll target: data_version + DB bounds/counts + staleness.
    Replaces separately polling get_data_version()/get_database_stats()/
    is_database_stale() from three places -- the frontend's global date-range
    store (see the migration plan) polls this single endpoint every 60s."""

    data_version: int
    schemes_count: int
    amc_count: int
    nav_count: int
    file_size_mb: float
    min_date: Optional[datetime.date]
    max_date: Optional[datetime.date]
    db_path: str
    is_stale: bool
    expected_date: Optional[datetime.date]


class MetaFilters(BaseModel):
    amcs: list[str]
    broad_categories: list[str]
    sub_categories: list[str]
    options: list[str]
    plans: list[str]
