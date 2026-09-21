"""Real regression test for the DuckDB->SQLite staging-table rewrite in
amfi_sync.py's write paths (con.register/con.unregister -> write_staging_table,
UPDATE...FROM -> correlated subqueries, DELETE...USING -> WHERE EXISTS).

This is the single most important write path in the whole backend: it's the
actual mechanism the user runs to populate the (currently empty) database at
all. A previous, unfixed version of this exact SQL pattern crashed with
`sqlite3.OperationalError: near "USING": syntax error` / similar -- caught
only by this test actually exercising the real sync function end-to-end
against a real SQLite connection, not by any of the db/queries.py-focused
tests (amfi_sync.py has its own direct SQL, entirely separate from
queries.py). Only the network layer (AmfiClient.download_daily_report) is
mocked; parsing (parse_amfi_nav_lines) and every SQL statement run for real.
"""

import datetime

import pytest

from app import amfi_sync
from app.amfi_client import AmfiClient
from app.db import connection
from app.db import queries as db


# Minimal, realistic-shaped AMFI NAVAll.txt excerpt: one category header, one AMC
# name line, one column-header line, two data rows (semicolon-delimited).
SAMPLE_NAV_TEXT_V1 = """Open Ended Schemes ( Equity Scheme - Large Cap Fund )

Test Asset Management Company Ltd

Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Repurchase Price;Sale Price;Date
111111;INF000001;-;Test Large Cap Fund - Direct Plan - Growth;125.4321;0;0;01-Feb-2025
222222;INF000002;-;Test Large Cap Fund - Regular Plan - Growth;120.1234;0;0;01-Feb-2025
"""

# Same two schemes, later date, changed NAVs -- exercises the "replace matching
# nav_history records" DELETE+INSERT path (a re-sync for a date already present
# would otherwise violate the (scheme_code, nav_date) primary key without it).
SAMPLE_NAV_TEXT_V2 = """Open Ended Schemes ( Equity Scheme - Large Cap Fund )

Test Asset Management Company Ltd

Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Repurchase Price;Sale Price;Date
111111;INF000001;-;Test Large Cap Fund - Direct Plan - Growth;126.0000;0;0;01-Feb-2025
222222;INF000002;-;Test Large Cap Fund - Regular Plan - Growth;121.0000;0;0;01-Feb-2025
"""


@pytest.fixture()
def db_con(pg_db, monkeypatch):
    db.init_db()
    yield


def test_sync_daily_nav_inserts_new_schemes_and_navs(db_con, monkeypatch):
    monkeypatch.setattr(AmfiClient, "download_daily_report", lambda self: SAMPLE_NAV_TEXT_V1)

    success, msg = amfi_sync.sync_daily_nav("test")
    assert success, msg

    con = connection.get_connection()
    schemes = con.execute("SELECT scheme_code, scheme_name, plan_type FROM schemes ORDER BY scheme_code").fetchall()
    navs = con.execute("SELECT scheme_code, nav_date, nav FROM nav_history ORDER BY scheme_code").fetchall()
    con.close()

    assert len(schemes) == 2
    assert schemes[0][0] == 111111
    assert schemes[0][2] == "Direct"
    assert len(navs) == 2
    assert navs[0] == (111111, datetime.date(2025, 2, 1), 125.4321)


def test_sync_daily_nav_replaces_navs_on_the_same_date_not_duplicates(db_con, monkeypatch):
    """The exact path that needed DELETE...WHERE EXISTS (replacing DuckDB's
    DELETE...USING) to work at all -- re-syncing the same date with revised
    NAVs must update in place, not raise a PRIMARY KEY violation or leave
    stale duplicate rows."""
    monkeypatch.setattr(AmfiClient, "download_daily_report", lambda self: SAMPLE_NAV_TEXT_V1)
    success1, msg1 = amfi_sync.sync_daily_nav("test")
    assert success1, msg1

    monkeypatch.setattr(AmfiClient, "download_daily_report", lambda self: SAMPLE_NAV_TEXT_V2)
    success2, msg2 = amfi_sync.sync_daily_nav("test")
    assert success2, msg2

    con = connection.get_connection()
    navs = con.execute(
        "SELECT scheme_code, nav_date, nav FROM nav_history WHERE scheme_code = 111111"
    ).fetchall()
    con.close()

    assert len(navs) == 1  # replaced, not duplicated
    assert navs[0][2] == pytest.approx(126.0)


def test_sync_daily_nav_updates_existing_scheme_metadata(db_con, monkeypatch):
    """The UPDATE...FROM -> correlated-subquery rewrite: re-syncing must update
    an existing scheme's metadata (e.g. a renamed fund), not just skip it."""
    monkeypatch.setattr(AmfiClient, "download_daily_report", lambda self: SAMPLE_NAV_TEXT_V1)
    amfi_sync.sync_daily_nav("test")

    renamed_v1 = SAMPLE_NAV_TEXT_V1.replace("Test Large Cap Fund - Direct Plan - Growth", "Renamed Large Cap Fund - Direct Plan - Growth")
    monkeypatch.setattr(AmfiClient, "download_daily_report", lambda self: renamed_v1)
    success, msg = amfi_sync.sync_daily_nav("test")
    assert success, msg

    con = connection.get_connection()
    row = con.execute("SELECT scheme_name FROM schemes WHERE scheme_code = 111111").fetchone()
    con.close()
    assert row[0] == "Renamed Large Cap Fund - Direct Plan - Growth"


def test_sync_daily_nav_refreshes_summary_table(db_con, monkeypatch):
    """A successful sync must leave summary_table queryable (refresh_summary_table()
    is called at the end of the sync) -- this is what every read endpoint actually
    queries, not nav_history/schemes directly."""
    monkeypatch.setattr(AmfiClient, "download_daily_report", lambda self: SAMPLE_NAV_TEXT_V1)
    success, msg = amfi_sync.sync_daily_nav("test")
    assert success, msg

    stats = db.get_market_overview_stats()
    assert stats["total_schemes"] == 2
    assert stats["total_nav_records"] == 2
