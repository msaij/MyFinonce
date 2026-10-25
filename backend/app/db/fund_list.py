"""Read-only SQL for the Overview's "All Funds" tab: every scheme, one row each.

Its own module, not more of db/queries.py, because that file is under a product freeze
until the maintainability refactor splits it by domain.
The plan/option predicate and the display label are reused from there, so "Unspecified"
and the "Name (Plan - Option) [AMFI code]" format mean the same thing as everywhere else.

The screener endpoint could list every scheme too, but its rows carry TER provenance
strings and a dozen fields this table never shows: ~23 MB for the whole universe.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.core.cache import cached
from app.db.connection import get_connection
from app.db.queries import _build_screener_where, format_scheme_display_name

_COLUMNS = (
    "scheme_code", "scheme_name", "fund_house", "broad_category", "category",
    "plan_type", "option_type", "is_active", "expense_ratio",
    "latest_nav", "latest_date", "change_1d_pct", "return_30d_pct",
    "return_1y_pct", "return_3y_pct", "return_5y_pct",
)
# From the scheme master, not the price summary: the riskometer is AMFI's per-fund label,
# stored on schemes by the nightly refresh (see amfi_sync.resolve_plan_options).
_RISK_COLUMNS = ("riskometer", "riskometer_as_of")


@cached(ttl=600)
def all_funds(plan_type: str = "All Plans", option_type: str = "All Options") -> List[Dict[str, Any]]:
    """Every scheme in summary_table, active or not, narrowed only by the global plan/option
    filter. The tab searches, filters and sorts the full list in the browser."""
    where_sql, params = _build_screener_where(plan_type=plan_type, option_type=option_type)
    sql = f"""
        SELECT {", ".join(f"s.{c}" for c in _COLUMNS)}, {", ".join(f"m.{c}" for c in _RISK_COLUMNS)}
        FROM summary_table s
        LEFT JOIN schemes m ON m.scheme_code = s.scheme_code
        {where_sql}
        ORDER BY s.scheme_name, s.scheme_code
    """
    with get_connection() as con:
        rows = con.execute(sql, params).fetchall()
    out = []
    for r in rows:
        row = dict(zip(_COLUMNS + _RISK_COLUMNS, r))
        row["display_name"] = format_scheme_display_name(
            row["scheme_name"], row["plan_type"], row["option_type"], row["scheme_code"]
        )
        for k in ("latest_date", "riskometer_as_of"):
            row[k] = row[k].isoformat() if row[k] else None
        out.append(row)
    return out
