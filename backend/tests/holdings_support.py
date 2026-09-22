"""Shared scaffolding for the holdings test suites (seeding and API helpers).

Every helper here runs against whatever database `pg_db` points the pool at --
the isolated test database, never the live one.
"""

import datetime
from typing import Dict, Iterable, Iterator, List, Sequence, Tuple

from app.db import connection
from app.db import queries as db


def business_days(start: datetime.date, end: datetime.date) -> Iterator[datetime.date]:
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            yield cur
        cur += datetime.timedelta(days=1)


def seed(schemes: Sequence[Tuple], navs: Dict[int, Iterable[Tuple[datetime.date, float]]]) -> None:
    """Insert schemes (scheme_code, scheme_name, fund_house, category, plan_type,
    option_type[, expense_ratio, ter_status]) and their NAV points, then build
    summary_table so the app sees them exactly as it would after a sync."""
    con = connection.get_connection()
    try:
        for s in schemes:
            cols = ["scheme_code", "scheme_name", "fund_house", "category", "plan_type", "option_type",
                    "expense_ratio", "ter_status"][: len(s)]
            con.execute(f"INSERT INTO schemes ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(s))})", list(s))
        rows: List[Tuple] = [(code, d, round(float(v), 4)) for code, pts in navs.items() for d, v in pts]
        con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
    finally:
        con.close()
    db.refresh_summary_table()


def nav_on(code: int, day: datetime.date) -> float:
    con = connection.get_connection()
    try:
        return float(con.execute("SELECT nav FROM nav_history WHERE scheme_code=%s AND nav_date=%s", (code, day)).fetchone()[0])
    finally:
        con.close()


def new_portfolio(client, name: str = "Self", benchmark: int = None) -> int:
    r = client.post("/api/holdings/portfolios", json={"name": name})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    if benchmark:
        client.patch(f"/api/holdings/portfolios/{pid}", json={"benchmark_scheme_code": benchmark})
    return pid


def add_txn(client, **draft):
    """POST a transaction draft exactly as given; returns the response."""
    return client.post("/api/holdings/transactions", json=draft)


def add_ok(client, **draft) -> dict:
    """POST a transaction draft that must succeed; returns its JSON body."""
    r = add_txn(client, **draft)
    assert r.status_code == 200, r.text
    return r.json()
