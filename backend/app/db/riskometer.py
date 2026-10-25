"""SEBI riskometer lookups for pages whose main query does not carry it.

The riskometer is stored per scheme (schemes.riskometer / riskometer_as_of) by the nightly
refresh, from AMFI's fund-performance feed. The Screener's and a fund page's queries read
the price summary, which has no such column, and live in db/queries.py -- under a product
freeze until the refactor splits it -- so the label is attached to their results here.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.db.connection import get_connection


def riskometers(codes: Iterable[Any]) -> Dict[int, Dict[str, Optional[str]]]:
    """scheme_code -> {"riskometer", "riskometer_as_of"} (ISO date), for the codes given."""
    wanted = sorted({int(c) for c in codes if c is not None and c == c})
    if not wanted:
        return {}
    with get_connection() as con:
        rows = con.execute(
            "SELECT scheme_code, riskometer, riskometer_as_of FROM schemes WHERE scheme_code = ANY(%s)",
            (wanted,),
        ).fetchall()
    return {int(code): {"riskometer": level, "riskometer_as_of": as_of.isoformat() if as_of else None}
            for code, level, as_of in rows}


def attach(records: List[Dict[str, Any]], key: str = "scheme_code") -> List[Dict[str, Any]]:
    """Adds riskometer and riskometer_as_of to each record, in place; None where AMFI has none."""
    found = riskometers(r.get(key) for r in records)
    for r in records:
        hit = found.get(int(r[key])) if r.get(key) is not None else None
        r["riskometer"] = hit["riskometer"] if hit else None
        r["riskometer_as_of"] = hit["riskometer_as_of"] if hit else None
    return records
