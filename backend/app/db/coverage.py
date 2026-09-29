"""Coverage of the LIVE fund universe, for Data Management's overview.

Counted over schemes still publishing NAVs (summary_table.is_active), not every scheme ever
listed: two-thirds of the scheme master are matured, merged or wound-up schemes that can
never have a current TER or riskometer, and dividing by them made a healthy database read
as mostly "unknown". Its own module, not more of db/queries.py (under a product freeze until
the refactor splits it).
"""

from __future__ import annotations

from typing import Any, Dict

from app.db.connection import get_connection


def live_scheme_coverage() -> Dict[str, Any]:
    """Live schemes, and how many of them carry an official TER, a riskometer, and a plan and
    option AMFI confirms. Cheap (one pass over ~9k live rows), unlike verify_sync, which
    counts all of nav_history; "unknown" plan/option means what it means there."""
    with get_connection() as con:
        row = con.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE m.ter_status = 'official'),
                   count(m.riskometer),
                   max(m.riskometer_as_of),
                   count(*) FILTER (WHERE m.plan_type IS NULL OR m.plan_type = ''),
                   count(*) FILTER (WHERE m.option_type IS NULL OR m.option_type IN ('', 'Other')),
                   (SELECT count(*) FROM schemes)
            FROM summary_table s
            JOIN schemes m ON m.scheme_code = s.scheme_code
            WHERE s.is_active
            """
        ).fetchone()
    live, ter_official, riskometer, riskometer_as_of, unknown_plan, unknown_option, listed = row
    return {
        "live_schemes": int(live or 0),
        "listed_schemes": int(listed or 0),
        "ter_official": int(ter_official or 0),
        "riskometer": int(riskometer or 0),
        "riskometer_as_of": riskometer_as_of.isoformat() if riskometer_as_of else None,
        "unknown_plan": int(unknown_plan or 0),
        "unknown_option": int(unknown_option or 0),
    }
