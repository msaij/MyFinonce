"""Read-only SQL for the Compare & Simulate backtest.

Its own module, not more of db/queries.py, because that file is under a product freeze
until the maintainability refactor splits it by domain.
NAV history and scheme identity are still read through the existing helpers
(queries.get_nav_history_dataframe, holdings.scheme_meta); only what they do not offer
lives here.
"""

from __future__ import annotations

from typing import Any, Dict, Sequence

from app.db.connection import get_connection
from app.db.queries import format_scheme_display_name


def growth_alternatives(codes: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    """For each IDCW scheme in `codes`, the Growth option of the same fund and plan.

    AMFI stores the base fund name once per variant, so the Growth sibling is the scheme
    with the same `scheme_name` and `plan_type` whose option is Growth (and whose name does
    not itself say IDCW -- AMFI labels a few IDCW schemes "Growth"). When several match,
    the one with the most recent NAV wins, so a wound-up duplicate is never suggested."""
    codes = sorted({int(c) for c in codes})
    if not codes:
        return {}
    sql = """
        SELECT DISTINCT ON (i.scheme_code)
               i.scheme_code, g.scheme_code, g.scheme_name, g.plan_type, g.option_type, l.nav_date
        FROM schemes i
        JOIN schemes g
          ON g.scheme_name = i.scheme_name
         AND g.plan_type IS NOT DISTINCT FROM i.plan_type
         AND g.option_type = 'Growth'
         AND g.scheme_code <> i.scheme_code
         AND g.scheme_name !~* 'idcw|dividend(?!\\s*yield)'
        LEFT JOIN LATERAL (
            SELECT nav_date FROM nav_history
            WHERE scheme_code = g.scheme_code ORDER BY nav_date DESC LIMIT 1
        ) l ON TRUE
        WHERE i.scheme_code = ANY(%s)
        ORDER BY i.scheme_code, l.nav_date DESC NULLS LAST, g.scheme_code
    """
    with get_connection() as con:
        rows = con.execute(sql, (codes,)).fetchall()
    return {
        int(r[0]): {
            "scheme_code": int(r[1]),
            "display_name": format_scheme_display_name(r[2] or "", r[3] or "", r[4] or "", int(r[1])),
            "latest_nav_date": r[5],
        }
        for r in rows
    }
