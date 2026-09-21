"""Correctness tests for app/db/queries.py and app/db/connection.py --
originally written for the DuckDB -> SQLite rewrite, which had no prior
coverage at all, and carried forward through the SQLite -> PostgreSQL port
(where they caught the boolean/integer is_active mismatch and the
ROUND(double precision, int) gap) (the existing test files only
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
import pandas as pd

import pytest

from app.db import connection
from app.db import queries as db


DATES = [datetime.date(2025, 1, 1) + datetime.timedelta(days=i) for i in range(74)]  # ..2025-03-15
GLOBAL_MAX = DATES[-1]  # 2025-03-15


@pytest.fixture()
def db_con(pg_db):
    """Initializes the schema in the per-test database from conftest's pg_db
    fixture, and inserts the synthetic dataset described in the module docstring. Yields nothing; tests call
    db.* functions directly, matching how routers use them."""

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
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
    con.close()

    db.refresh_summary_table()
    yield


def test_init_db_creates_expected_tables(db_con):
    con = connection.get_connection()
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'").fetchall()}
    con.close()
    assert {"schemes", "nav_history", "ter_history", "summary_table", "sync_meta", "sync_checkpoint"} <= tables


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
    assert is_active is True  # BOOLEAN column in Postgres, not SQLite's 0/1 integer
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
    assert is_active is False
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


def test_get_category_performance_matrix(db_con):
    # Test All categories
    df_all = db.get_category_performance_matrix("All")
    assert not df_all.empty
    assert "Asset Class" in df_all.columns
    assert "Category" in df_all.columns
    assert "Schemes" in df_all.columns
    assert "Avg TER %" in df_all.columns
    assert "Avg 30D %" in df_all.columns

    # Test filtering by specific broad category
    df_equity = db.get_category_performance_matrix("Equity")
    assert not df_equity.empty
    assert (df_equity["Asset Class"] == "Equity").all()


def test_get_macro_trend_with_plan_and_option_type(db_con):
    df = db.get_macro_asset_class_trend(
        start_date=datetime.date(2025, 1, 1),
        end_date=datetime.date(2025, 1, 15),
        plan_type="Direct",
        option_type="Growth",
    )
    assert isinstance(df, pd.DataFrame)
    assert "nav_date" in df.columns
    assert "Asset Class" in df.columns
    assert "Indexed Performance" in df.columns


def test_get_advanced_leaders_dataframe_with_string_dates_and_lookback(db_con):
    # Pass dates as strings to verify date normalization does not crash timedelta math
    df = db.get_advanced_leaders_dataframe(
        start_date="2025-01-01",
        end_date="2025-02-15",
        vol_lookback="6M",
    )
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert "annualized_vol_pct" in df.columns
    assert "period_return_pct" in df.columns
    assert "cat_alpha_pct" in df.columns


def test_screener_all_available_date_range_short_history(db_con):
    """Verifies that selecting an 'All Available' window (e.g. 2008 to present, >= 3200 days)
    computes valid non-null period_return_pct for schemes with short histories (e.g. 74 days)
    rather than artificially returning NULL."""
    start = datetime.date(2008, 1, 1)
    end = GLOBAL_MAX  # 2025-03-15 (window span: >6000 days >= 3200 days)

    df = db.get_screener_dataframe(start_date=start, end_date=end)
    assert len(df) >= 3

    # Scheme 111: nav 100 on 2025-01-01 -> 173 on 2025-03-15 (+73.0%)
    row111 = df[df["scheme_code"] == 111].iloc[0]
    assert row111["period_return_pct"] is not None
    assert row111["period_return_pct"] == pytest.approx(73.0)

    # Scheme 222: nav 50 flat (0.0%)
    row222 = df[df["scheme_code"] == 222].iloc[0]
    assert row222["period_return_pct"] is not None
    assert row222["period_return_pct"] == pytest.approx(0.0)

    # Scheme 333: nav 200 on 2025-01-01 -> 214 on 2025-01-15 (+7.0%)
    row333 = df[df["scheme_code"] == 333].iloc[0]
    assert row333["period_return_pct"] is not None
    assert row333["period_return_pct"] == pytest.approx(round((214 - 200) / 200 * 100, 4))


def test_screener_past_10_years_and_custom_date_ranges(db_con):
    """Verifies that Past 10 Years and arbitrary custom date ranges calculate accurate returns."""
    # Past 10 years (3650 days >= 3200 days)
    start_10y = GLOBAL_MAX - datetime.timedelta(days=3650)
    df_10y = db.get_screener_dataframe(start_date=start_10y, end_date=GLOBAL_MAX)
    row111_10y = df_10y[df_10y["scheme_code"] == 111].iloc[0]
    assert row111_10y["period_return_pct"] == pytest.approx(73.0)

    # Custom short window: 2025-01-10 (nav=109) to 2025-01-20 (nav=119)
    start_custom = datetime.date(2025, 1, 10)
    end_custom = datetime.date(2025, 1, 20)
    df_custom = db.get_screener_dataframe(start_date=start_custom, end_date=end_custom)
    row111_c = df_custom[df_custom["scheme_code"] == 111].iloc[0]
    assert row111_c["period_return_pct"] == pytest.approx(round((119 - 109) / 109 * 100, 4))


def test_kpis_all_available_full_universe_coverage(db_con):
    """Verifies get_kpis covers all active schemes with data in 'All Available' window."""
    start = datetime.date(2008, 1, 1)
    end = GLOBAL_MAX
    kpis = db.get_kpis(start_date=start, end_date=end)

    # Advancers: 111 (+73%) and 333 (+7%) = 2
    assert kpis["advancers"] == 2
    # Unchanged: 222 (0%) = 1
    assert kpis["unchanged"] == 1
    # Decliners: 0
    assert kpis["decliners"] == 0
    # Top performer: Test Equity Fund
    assert kpis["top_performer"]["name"] == "Test Equity Fund"
    assert kpis["top_performer"]["return_pct"] == pytest.approx(73.0)
    # Lag performer: Test Liquid Fund
    assert kpis["lag_performer"]["name"] == "Test Liquid Fund"
    assert kpis["lag_performer"]["return_pct"] == pytest.approx(0.0)
    # Median return: median of [0.0, 7.0, 73.0] = 7.0
    assert kpis["median_return"] == pytest.approx(7.0)


def test_gainers_losers_all_available_window(db_con):
    """Verifies get_gainers_losers does not exclude schemes based on age in large date windows."""
    start = datetime.date(2008, 1, 1)
    end = GLOBAL_MAX
    gainers, losers = db.get_gainers_losers(start_date=start, end_date=end, top_n=5)
    assert not gainers.empty
    assert not losers.empty
    assert 111 in gainers["scheme_code"].values
    assert gainers[gainers["scheme_code"] == 111].iloc[0]["return_pct"] == pytest.approx(73.0)
    assert 222 in losers["scheme_code"].values
    assert losers[losers["scheme_code"] == 222].iloc[0]["return_pct"] == pytest.approx(0.0)


def test_leaders_laggards_all_available_window(db_con):
    """Verifies get_advanced_leaders_dataframe computes accurately for any date window."""
    start = datetime.date(2008, 1, 1)
    end = GLOBAL_MAX
    df = db.get_advanced_leaders_dataframe(start_date=start, end_date=end)
    assert not df.empty
    assert 111 in df["scheme_code"].values
    assert 222 in df["scheme_code"].values
    assert 333 in df["scheme_code"].values
    assert df["period_return_pct"].isna().sum() == 0


def test_zero_nav_or_non_positive_nav_cleanly_evaluates_null(db_con):
    """Verifies schemes with zero NAV records in window or non-positive NAV cleanly evaluate to NULL."""
    from app.core.cache import clear_all_caches
    clear_all_caches()

    con = connection.get_connection()
    # Insert scheme 444 with no NAV history at all
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, isin, expense_ratio) VALUES "
        "(444, 'Zero NAV Fund', 'Test AMC', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth', 'INF000004', 1.0)"
    )
    # Insert scheme 555 with NAV <= 0
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, isin, expense_ratio) VALUES "
        "(555, 'Zero Price Fund', 'Test AMC', 'Debt Scheme - Liquid Fund', 'Direct', 'Growth', 'INF000005', 0.5)"
    )
    con.execute("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (555, '2025-01-01', 0.0), (555, '2025-01-02', 10.0)")
    con.close()
    db.refresh_summary_table()
    clear_all_caches()

    # Query screener for scheme 444 (no nav) and 555 (start nav <= 0)
    df = db.get_screener_dataframe(scheme_code=444, start_date=datetime.date(2025, 1, 1), end_date=datetime.date(2025, 1, 10))
    assert len(df) == 0 or pd.isna(df.iloc[0]["period_return_pct"])

    df555 = db.get_screener_dataframe(scheme_code=555, start_date=datetime.date(2025, 1, 1), end_date=datetime.date(2025, 1, 10))
    assert len(df555) == 1
    assert pd.isna(df555.iloc[0]["period_return_pct"])

    # Discontinued scheme outside window (scheme 333 stopped on 2025-01-15, query window 2025-02-01..2025-02-15)
    g, l = db.get_gainers_losers(start_date=datetime.date(2025, 2, 1), end_date=datetime.date(2025, 2, 15))
    assert 333 not in g["scheme_code"].values
    assert 333 not in l["scheme_code"].values
    assert 444 not in g["scheme_code"].values
    assert 555 not in g["scheme_code"].values


def test_inverted_date_range_normalization(db_con):
    """Verifies that start_date > end_date is gracefully auto-swapped across all queries."""
    start = datetime.date(2025, 1, 20)
    end = datetime.date(2025, 1, 10)

    # Screener
    df_inv = db.get_screener_dataframe(start_date=start, end_date=end)
    df_normal = db.get_screener_dataframe(start_date=end, end_date=start)
    row_inv = df_inv[df_inv["scheme_code"] == 111].iloc[0]
    row_norm = df_normal[df_normal["scheme_code"] == 111].iloc[0]
    assert row_inv["period_return_pct"] == pytest.approx(row_norm["period_return_pct"])

    # KPIs
    kpi_inv = db.get_kpis(start_date=start, end_date=end)
    kpi_norm = db.get_kpis(start_date=end, end_date=start)
    assert kpi_inv["advancers"] == kpi_norm["advancers"]
    assert kpi_inv["median_return"] == pytest.approx(kpi_norm["median_return"])

    # Gainers / Losers
    g_inv, l_inv = db.get_gainers_losers(start_date=start, end_date=end)
    assert not g_inv.empty
    assert 111 in g_inv["scheme_code"].values

    # Leaders
    leaders_inv = db.get_advanced_leaders_dataframe(start_date=start, end_date=end)
    assert not leaders_inv.empty
    assert 111 in leaders_inv["scheme_code"].values


def test_single_data_point_window_computes_zero_return(db_con):
    """Verifies a 1-day/single-point window (start == end) computes 0.0% period_return without division by zero."""
    same_day = datetime.date(2025, 1, 10)
    df = db.get_screener_dataframe(start_date=same_day, end_date=same_day)
    row111 = df[df["scheme_code"] == 111].iloc[0]
    assert row111["period_return_pct"] == pytest.approx(0.0)


def test_query_aliases_exist():
    """Verifies get_screener_data and get_leaders_laggards exist as aliases."""
    assert db.get_screener_data is db.get_screener_dataframe
    assert db.get_leaders_laggards is db.get_advanced_leaders_dataframe


def test_screener_api_endpoints_all_available_window(db_con):
    """Verifies /api/screener and /api/screener/kpis endpoints handle 'All Available' window cleanly."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    # 1. /api/screener
    resp = client.get("/api/screener?start=2008-01-01&end=2025-03-15")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 3
    row111 = next(r for r in rows if r["scheme_code"] == 111)
    assert row111["period_return_pct"] is not None
    assert row111["period_return_pct"] == pytest.approx(73.0)

    # 2. /api/screener/kpis
    resp_kpis = client.get("/api/screener/kpis?start=2008-01-01&end=2025-03-15")
    assert resp_kpis.status_code == 200
    kpi_data = resp_kpis.json()
    assert kpi_data["advancers"] == 2
    assert kpi_data["unchanged"] == 1
    assert kpi_data["decliners"] == 0
    assert kpi_data["top_performer"]["name"] == "Test Equity Fund"
    assert kpi_data["top_performer"]["return_pct"] == pytest.approx(73.0)
    assert kpi_data["median_return"] == pytest.approx(7.0)

    # 3. Inverted date query to API returns valid results
    resp_inv = client.get("/api/screener?start=2025-03-15&end=2008-01-01")
    assert resp_inv.status_code == 200
    rows_inv = resp_inv.json()
    row111_inv = next(r for r in rows_inv if r["scheme_code"] == 111)
    assert row111_inv["period_return_pct"] == pytest.approx(73.0)


def test_normalize_date_range_flexible_formats():
    """Verifies _normalize_date_range parses alternative string formats, timestamps, and invalid input safely."""
    # Slash dates
    s, e = db._normalize_date_range("2025/01/01", "2025/02/01")
    assert s == datetime.date(2025, 1, 1) and e == datetime.date(2025, 2, 1)

    # Inverted slash dates
    s_inv, e_inv = db._normalize_date_range("2025/02/01", "2025/01/01")
    assert s_inv == datetime.date(2025, 1, 1) and e_inv == datetime.date(2025, 2, 1)

    # Non-zero-padded ISO dates
    s_np, e_np = db._normalize_date_range("2025-1-5", "2025-2-10")
    assert s_np == datetime.date(2025, 1, 5) and e_np == datetime.date(2025, 2, 10)

    # Pandas Timestamps
    s_ts, e_ts = db._normalize_date_range(pd.Timestamp("2025-01-01"), pd.Timestamp("2025-02-01"))
    assert s_ts == datetime.date(2025, 1, 1) and e_ts == datetime.date(2025, 2, 1)

    # Open-ended / single-sided date inputs
    s_only, e_none = db._normalize_date_range("2025-01-01", None)
    assert s_only == datetime.date(2025, 1, 1) and e_none is None

    s_none, e_only = db._normalize_date_range(None, "2025-02-01")
    assert s_none is None and e_only == datetime.date(2025, 2, 1)

    # Invalid / unparseable strings return None for that component rather than crashing
    s_bad, e_valid = db._normalize_date_range("not-a-date", "2025-01-01")
    assert s_bad is None and e_valid == datetime.date(2025, 1, 1)

    s_bad2, e_bad2 = db._normalize_date_range("not-a-date", "also-invalid")
    assert s_bad2 is None and e_bad2 is None


def test_nav_history_single_sided_date_ranges(db_con):
    """Verifies get_nav_history_dataframe filters correctly when only start_date or only end_date is given."""
    df_start_only = db.get_nav_history_dataframe([111], start_date="2025-01-10")
    assert not df_start_only.empty
    assert df_start_only["nav_date"].min() == datetime.date(2025, 1, 10)
    assert len(df_start_only) == 65

    df_end_only = db.get_nav_history_dataframe([111], end_date="2025-01-10")
    assert not df_end_only.empty
    assert df_end_only["nav_date"].max() == datetime.date(2025, 1, 10)
    assert len(df_end_only) == 10


def test_nav_history_api_single_sided_dates(db_con):
    """Verifies /api/schemes/nav-history endpoint supports open-ended single-sided date queries."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/schemes/nav-history?codes=111&start=2025-01-10")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 65
    assert all(r["nav_date"] >= "2025-01-10" for r in rows)

    resp2 = client.get("/api/schemes/nav-history?codes=111&end=2025-01-10")
    assert resp2.status_code == 200
    rows2 = resp2.json()
    assert len(rows2) == 10
    assert all(r["nav_date"] <= "2025-01-10" for r in rows2)


def test_macro_trend_and_nav_history_inverted_date_ranges(db_con):
    """Verifies get_macro_asset_class_trend and get_nav_history_dataframe normalize inverted date ranges."""
    start = datetime.date(2025, 1, 1)
    end = datetime.date(2025, 2, 15)

    # Macro trend
    df_norm = db.get_macro_asset_class_trend(start, end)
    df_inv = db.get_macro_asset_class_trend(end, start)
    assert not df_norm.empty
    assert len(df_norm) == len(df_inv)

    # NAV history
    hist_norm = db.get_nav_history_dataframe([111], start, end)
    hist_inv = db.get_nav_history_dataframe([111], end, start)
    assert not hist_norm.empty
    assert len(hist_norm) == len(hist_inv)


def test_cat_median_sql_plan_type_filtered_branch(db_con):
    """Verifies get_advanced_leaders_dataframe exercises cat_median_sql when plan_type is filtered."""
    df_direct = db.get_advanced_leaders_dataframe(
        plan_type="Direct",
        start_date=datetime.date(2025, 1, 1),
        end_date=GLOBAL_MAX,
    )
    assert not df_direct.empty
    assert df_direct["cat_median_return"].isna().sum() == 0
    assert df_direct["cat_alpha_pct"].isna().sum() == 0
    assert 111 in df_direct["scheme_code"].values


def test_overview_macro_trend_api_inverted_dates(db_con):
    """Verifies /api/overview/macro-trend API endpoint handles inverted dates gracefully."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/overview/macro-trend?start_date=2025-02-15&end_date=2025-01-01")
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) > 0





