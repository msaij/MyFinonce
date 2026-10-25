import datetime
from typing import Any, Optional

from pydantic import BaseModel


class SchemeSearchResult(BaseModel):
    """Matches db.get_schemes_for_dropdown()'s already-JSON-shaped dict output --
    kept loose (extra fields pass through) rather than pinned to an exact key
    set, since that function's exact dict shape is a porting detail, not a
    contract this schema should freeze prematurely."""

    model_config = {"extra": "allow"}

    scheme_code: int
    scheme_name: str


class NavPoint(BaseModel):
    scheme_code: int
    nav_date: datetime.date
    nav: float


class TerHistoryPoint(BaseModel):
    model_config = {"extra": "allow"}

    scheme_code: int
    ter_date: datetime.date
    total_ter_pct: Optional[float] = None
