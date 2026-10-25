"""Checks for the query parameters the market pages share: the Plan/Option picker and the
date window.

Every distinct value is its own cache key, and a miss on the leaders or pulse routes is a
multi-second scan. Free-form strings and dates let any loop of requests (a page firing
`<img src=…&plan_type=x1>`, `x2`, …) fill the 256-entry caches and keep the database busy;
a typo'd bookmark got an empty page instead of an error. Unknown values are refused (422)
instead.
"""

import datetime
from typing import Optional

from fastapi import HTTPException

from app.db import queries as db

# What the page's own pickers send, accepted even when the live list can't be read (the
# lookup falls back to just the "All" entry), so a database hiccup never locks the page out.
_KNOWN_PLANS = {"All Plans", "Direct", "Regular", db.UNSPECIFIED_FILTER}
_KNOWN_OPTIONS = {"All Options", "Growth", "IDCW", db.UNSPECIFIED_FILTER}

#: No AMFI NAV predates this; anything earlier is a typo, not a window.
EARLIEST_DATE = datetime.date(1990, 1, 1)


def plan_type(value: Optional[str]) -> str:
    """The plan filter, or 422 for a value the data doesn't hold."""
    value = value or "All Plans"
    if value not in _KNOWN_PLANS and value not in db.get_plans_list():
        raise HTTPException(status_code=422, detail=f"Unknown plan_type {value!r}")
    return value


def option_type(value: Optional[str]) -> str:
    """The option filter, or 422 for a value the data doesn't hold."""
    value = value or "All Options"
    if value not in _KNOWN_OPTIONS and value not in db.get_options_list():
        raise HTTPException(status_code=422, detail=f"Unknown option_type {value!r}")
    return value


def window_date(value: Optional[datetime.date], name: str) -> Optional[datetime.date]:
    """A window bound between EARLIEST_DATE and tomorrow (AMFI's day can run ahead of a
    browser's clock near midnight), or 422. An inverted window is fine: the queries
    normalise it."""
    if value is None:
        return None
    latest = datetime.date.today() + datetime.timedelta(days=1)
    if not EARLIEST_DATE <= value <= latest:
        raise HTTPException(status_code=422, detail=f"{name} {value.isoformat()} is outside {EARLIEST_DATE.isoformat()}..{latest.isoformat()}")
    return value
