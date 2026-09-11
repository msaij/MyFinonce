"""Correctness tests for the SQLite rewrite of app/db/queries.py and
app/db/connection.py -- this is the one part of the DuckDB -> SQLite
migration with no prior test coverage at all (the existing test files only
cover quant_analytics/portfolio_sim/costs_data, none of which touch the DB),
and the rewrite touched every query's SQL dialect, so it gets its own,
real, hand-computed dataset rather than just "does it crash" smoke tests.

Dataset (built fresh per test via the `db` fixture): three synthetic
schemes over a 74-consecutive-calendar-day window 2025-01-01..2025-03-15 --
consecutive dates (not real trading-day gaps) specifically so "N days ago"
in the SQL lands on an exact, unambiguous row and expected values can be
hand-computed directly instead of needing nearest-match tolerance reasoning.

  - 111 "Test Equity Fund" (Test AMC, Equity Scheme - Large Cap Fund):
    nav = 100, 101, 102, ... 173 (linear, +1/day). Latest date is the global
    max date, so this scheme is_active.
  - 222 "Test Liquid Fund" (Second AMC, Debt Scheme - Liquid Fund): nav flat
    at 50.0 every day -- zero return, exercises the "unchanged" branch and
    gives MEDIAN/STDDEV a second, distinct data point to combine with 111's.
  - 333 "Stale Fund" (Test AMC, Equity Scheme - Large Cap Fund): only has
    data up to 2025-01-15 (60 days before the global max date) -- exercises
    the is_active=False / NULL-returns path.
"""

import datetime

import pytest

from app.db import connection
from app.db import queries as db


DATES = [datetime.date(2025, 1, 1) + datetime.timedelta(days=i) for i in range(74)]  # ..2025-03-15
GLOBAL_MAX = DATES[-1]  # 2025-03-15


@pytest.fixture()
def db_con(tmp_path, monkeypatch):
    """Points connection.DB_PATH at a fresh temp file, resets the module-global
    singleton connection, initializes the schema, and inserts the synthetic
    dataset described in the module docstring. Yields nothing; tests call
    db.* functions directly, matching how routers use them."""
    monkeypatch.setattr(connection, "DB_PATH", str(tmp_path / "test.sqlite3"))
    connection._THREAD_LOCAL.con = None

    db.init_db()

    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, isin, expense_ratio) VALUES "
        "(111, 'Test Equity Fund', 'Test AMC', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth', 'INF000001', 1.5),"
        "(222, 'Test Liquid Fund', 'Second AMC', 'Debt Scheme - Liquid Fund', 'Direct', 'Growth', 'INF000002', 0.2),"
        "(333, 'Stale Fund', 'Test AMC', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth', 'INF000003', 2.0)"
    )
    rows = []
    for i, d in enumerate(DATES):
        rows.append((111, d.isoformat(), 100.0 + i))
        rows.append((222, d.isoformat(), 50.0))
        if d <= datetime.date(2025, 1, 15):  # scheme 333 stops early -> stale
            rows.append((333, d.isoformat(), 200.0 + i))
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (?, ?, ?)", rows)
    con.close()

    db.refresh_summary_table()
    yield
    connection._THREAD_LOCAL.con = None


def test_init_db_creates_expected_tables(db_con):
    con = connection.get_connection()
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    con.close()
    assert {"schemes", "nav_history", "ter_history", "summary_table", "sync_meta"} <= tables


def test_refresh_summary_table_computes_correct_returns(db_con):
    con = connection.get_connection()
    row = con.execute(
        "SELECT latest_date, latest_nav, is_active, change_1d_pct, return_7d_pct, return_30d_pct, "
        "return_90d_pct, return_1y_pct, high_52w, low_52w, dist_from_52w_high_pct "
        "FROM summary_table WHERE scheme_code = 111"
    ).fetchone()
    con.close()
    latest_date, latest_nav, is_active, chg_1d, ret_7d, ret_30d, ret_90d, ret_1y, high, low, dist = row

    assert latest_date == GLOBAL_MAX
    assert latest_nav == pytest.approx(173.0)
    assert is_active in (1, True)  # SQLite boolean expressions come back as 0/1
    # nav 1/7/30 days ago land on exact rows since dates are consecutive: 172, 166, 143.
    assert chg_1d == pytest.approx(round((173 - 172) / 172 * 100, 4))
    assert ret_7d == pytest.approx(round((173 - 166) / 166 * 100, 4))
    assert ret_30d == pytest.approx(round((173 - 143) / 143 * 100, 4))
    # 90 days back falls before the dataset's start (2025-01-01) -- must be NULL, not a wrong number.
    assert ret_90d is None
    assert ret_1y is None
    assert high == pytest.approx(173.0)
    assert low == pytest.approx(100.0)
    assert dist == pytest.approx(0.0)


def test_refresh_summary_table_flags_stale_scheme_inactive(db_con):
    con = connection.get_connection()
    row = con.execute(
        "SELECT is_active, change_1d_pct, return_30d_pct FROM summary_table WHERE scheme_code = 333"
    ).fetchone()
    con.close()
    is_active, chg_1d, ret_30d = row
    assert is_active in (0, False)
    assert chg_1d is None
    assert ret_30d is None


def test_refresh_summary_table_flat_nav_is_zero_return(db_con):
    con = connection.get_connection()
    row = con.execute("SELECT change_1d_pct, return_30d_pct FROM summary_table WHERE scheme_code = 222").fetchone()
    con.close()
    assert row[0] == pytest.approx(0.0)
    assert row[1] == pytest.approx(0.0)


def test_get_amcs_and_categories(db_con):
    assert db.get_amcs() == ["Second AMC", "Test AMC"]
    assert db.get_broad_categories() == ["Debt", "Equity"]


def test_get_screener_dataframe_period_return(db_con):
    # Explicit date range exercises the p_start/p_end CTEs, a different code path
    # from the summary_table's own precomputed return_Nd_pct columns.
    start = datetime.date(2025, 1, 10)  # index 9 -> nav 109
    end = datetime.date(2025, 2, 10)  # index 40 -> nav 140
    df = db.get_screener_dataframe(scheme_code=111, start_date=start, end_date=end)
    assert len(df) == 1
    expected = round((140 - 109) / 109 * 100, 4)
    assert df.iloc[0]["period_return_pct"] == pytest.approx(expected)
    assert isinstance(df.iloc[0]["latest_date"], datetime.date)  # DATE columns round-trip as date objects


def test_get_kpis_no_date_range_uses_return_30d(db_con):
    # Only active schemes (111, 222) have a non-null return_30d_pct; 333 is stale/NULL
    # and must be excluded from top/lag/median -- this also exercises the MEDIAN and
    # STDDEV custom aggregates and the ARG_MAX/ARG_MIN -> ORDER BY...LIMIT 1 rewrite.
    kpis = db.get_kpis()
    assert kpis["top_performer"]["name"] == "Test Equity Fund"
    assert kpis["lag_performer"]["name"] == "Test Liquid Fund"
    assert kpis["advancers"] == 1  # only scheme 111 has a positive 30D return
    assert kpis["decliners"] == 0
    assert kpis["unchanged"] == 1  # scheme 222's flat NAV


def test_get_market_overview_stats_median_aggregate(db_con):
    stats = db.get_market_overview_stats()
    assert stats["total_schemes"] == 3
    assert stats["total_amcs"] == 2
    # med_30d for the Equity bucket (scheme 111 only) must equal that scheme's own value.
    equity_row = stats["asset_dist"][stats["asset_dist"]["broad_category"] == "Equity"].iloc[0]
    assert equity_row["med_30d"] == pytest.approx(round((173 - 143) / 143 * 100, 4))


def test_get_advanced_leaders_dataframe_volatility(db_con):
    # Exercises STDDEV + the custom SQRT function in the annualized-volatility calc.
    # Scheme 111's NAV increases by a constant absolute amount (not constant pct), so
    # daily returns aren't literally identical -- just assert it's a small positive
    # number rather than hand-deriving the exact stddev of 73 unequal daily returns.
    df = db.get_advanced_leaders_dataframe(
        start_date=datetime.date(2025, 1, 1), end_date=GLOBAL_MAX
    )
    row111 = df[df["scheme_code"] == 111].iloc[0]
    assert row111["annualized_vol_pct"] is not None
    assert row111["annualized_vol_pct"] > 0
    # Scheme 222's NAV never moves -> zero daily returns -> zero volatility, not NULL/NaN.
    row222 = df[df["scheme_code"] == 222].iloc[0]
    assert row222["annualized_vol_pct"] == pytest.approx(0.0)


def test_upsert_ter_history_staging_table_roundtrip(db_con):
    import pandas as pd

    records = pd.DataFrame([
        {
            "scheme_code": 111,
            "ter_date": datetime.date(2025, 2, 1),
            "base_expense_ratio_pct": 1.2,
            "brokerage_cost_pct": 0.1,
            "transaction_cost_pct": 0.05,
            "statutory_levies_pct": 0.05,
            "total_ter_pct": 1.4,
            "source_url": "https://example.com/ter",
        }
    ])
    n = db.upsert_ter_history(records)
    assert n == 1
    hist = db.get_scheme_ter_history(111)
    assert len(hist) == 1
    assert hist.iloc[0]["total_ter_pct"] == pytest.approx(1.4)
    assert hist.iloc[0]["ter_date"] == datetime.date(2025, 2, 1)

    # Re-upserting the same (scheme_code, ter_date) must replace, not duplicate --
    # this is exactly what the DELETE...WHERE EXISTS rewrite (of DuckDB's DELETE...USING)
    # needs to get right.
    records2 = records.copy()
    records2.loc[0, "total_ter_pct"] = 1.55
    db.upsert_ter_history(records2)
    # upsert_ter_history() doesn't call bump_data_version() -- a pre-existing gap in
    # fetcher/db.py too (identical code there), not introduced by this rewrite; out of
    # scope to fix during a dialect port. Clear the cache directly so this test verifies
    # the DELETE+INSERT SQL itself, not that unrelated gap.
    from app.core.cache import clear_all_caches
    clear_all_caches()
    hist2 = db.get_scheme_ter_history(111)
    assert len(hist2) == 1
    assert hist2.iloc[0]["total_ter_pct"] == pytest.approx(1.55)


def test_apply_latest_official_ter_updates_schemes_table(db_con):
    import pandas as pd

    records = pd.DataFrame([
        {
            "scheme_code": 222,
            "ter_date": datetime.date(2025, 3, 1),
            "base_expense_ratio_pct": 0.15,
            "brokerage_cost_pct": 0.02,
            "transaction_cost_pct": 0.02,
            "statutory_levies_pct": 0.01,
            "total_ter_pct": 0.2,
            "ter_source": "AMFI Total Expense Ratio Disclosure",
            "source_url": "https://example.com/ter2",
        }
    ])
    result = db.apply_latest_official_ter(records)
    assert result["updated"] == 1

    con = connection.get_connection()
    row = con.execute("SELECT expense_ratio, ter_status, ter_as_of_date FROM schemes WHERE scheme_code = 222").fetchone()
    con.close()
    assert row[0] == pytest.approx(0.2)
    assert row[1] == "official"
    assert row[2] == datetime.date(2025, 3, 1)
