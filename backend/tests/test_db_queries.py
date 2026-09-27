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

from app.core import cache
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


def _insert_unlabelled_scheme():
    """A scheme AMFI never gave a plan or option for -- about 40% of its daily file."""
    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) "
        "VALUES (444, 'Unlabelled Fund', 'Test AMC', 'Debt Scheme - Liquid Fund', NULL, NULL)"
    )
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)",
                    [(444, d.isoformat(), 10.0 + i * 0.01) for i, d in enumerate(DATES)])
    con.close()
    db.refresh_summary_table()


def test_the_label_survives_a_plan_pandas_reports_as_nan():
    """Values arrive here straight from pandas, where a NULL plan is float NaN -- truthy,
    so it used to reach .strip() and raise. AMFI leaves the plan blank on ~40% of rows."""
    assert db.format_scheme_display_name("Some Fund", float("nan"), float("nan"), 444) == "Some Fund [444]"
    assert db.format_scheme_display_name("Some Fund", None, None, None) == "Some Fund"
    assert db.format_scheme_display_name("Some Fund", "Direct", "Growth", 1) == "Some Fund (Direct - Growth) [1]"


def test_filter_lists_come_from_the_data_not_a_hardcoded_list(db_con):
    _insert_unlabelled_scheme()
    # These lists are cached for 10 minutes; this test changes the data underneath them.
    cache.clear_all_caches()
    plans, options = db.get_plans_list(), db.get_options_list()

    assert plans[0] == "All Plans" and options[0] == "All Options"
    assert "Unspecified" in plans, "schemes AMFI never labelled must be nameable in the filter"
    assert "Unspecified" in options


def test_every_scheme_is_reachable_by_some_plan_filter(db_con):
    """Direct and Regular between them used to return fewer schemes than 'All Plans', with
    nothing naming the remainder -- they simply vanished from every filtered view."""
    _insert_unlabelled_scheme()
    total = len(db.get_screener_dataframe(start_date=DATES[0], end_date=GLOBAL_MAX, limit=None))
    parts = sum(
        len(db.get_screener_dataframe(plan_type=p, start_date=DATES[0], end_date=GLOBAL_MAX, limit=None))
        for p in ("Direct", "Regular", "Unspecified")
    )
    assert parts == total


def test_kpi_scheme_count_honours_the_selected_window(db_con):
    """The headline counted every scheme ever, including ones wound up years ago, while the
    table beside it counted only those alive in the window -- 25,377 against 8,864."""
    recent = db.get_kpis(start_date=DATES[-5], end_date=GLOBAL_MAX)
    rows = db.get_screener_dataframe(start_date=DATES[-5], end_date=GLOBAL_MAX, limit=None)
    assert recent["total_schemes"] == len(rows)
    assert recent["total_schemes"] < db.get_kpis()["total_schemes"], "scheme 333 stopped publishing in January"


def test_overview_counts_schemes_that_still_publish_not_every_one_ever_listed(db_con):
    """Scheme 333 stopped publishing in January. Counting it made the Overview describe a
    market two thirds of which no longer exists -- 17,215 debt schemes against 2,828 live."""
    cache.clear_all_caches()
    stats = db.get_market_overview_stats()

    assert stats["active_schemes"] == 2 and stats["total_schemes"] == 3
    equity = next(r for r in stats["asset_dist"].to_dict("records") if r["broad_category"] == "Equity")
    assert equity["count"] == 1, "only the live equity scheme counts"


def test_overview_honours_the_global_plan_filter(db_con):
    """The KPI row and macro chart already filtered by plan; these aggregates did not, so
    half the page described a different universe from the other half."""
    cache.clear_all_caches()
    everything = db.get_market_overview_stats()
    regular = db.get_market_overview_stats(plan_type="Regular")

    assert everything["active_schemes"] == 2
    assert regular["active_schemes"] == 0, "every seeded scheme is a Direct plan"


def test_category_matrix_is_scoped_the_same_way(db_con):
    cache.clear_all_caches()
    rows = db.get_category_performance_matrix().to_dict("records")
    assert sum(r["Schemes"] for r in rows) == 2
    assert db.get_category_performance_matrix(plan_type="Regular").empty


def test_refresh_summary_table_records_when_it_was_built(db_con):
    """get_data_quality() reports this timestamp; before the rebuild started writing it,
    the key was only ever read, so the field was null no matter how often it ran."""
    built = db.get_data_quality()["summary_table_built_at"]
    assert built and datetime.datetime.fromisoformat(built) <= datetime.datetime.now()

    db.refresh_summary_table()
    assert db.get_data_quality()["summary_table_built_at"] >= built


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


def test_refresh_summary_table_keeps_the_nav_the_1d_change_was_measured_against(db_con):
    """change_1d_pct is stored as ROUND(..., 4), so it cannot be inverted back to
    yesterday's NAV without losing paise. Holdings needs the rupee move of a position,
    units x (latest_nav - nav_1d_ago), so the NAV itself is carried, not just the
    percentage derived from it."""
    con = connection.get_connection()
    row = con.execute(
        "SELECT latest_nav, nav_1d_ago, change_1d_pct FROM summary_table WHERE scheme_code = 111"
    ).fetchone()
    con.close()
    latest_nav, nav_1d_ago, chg_1d = row
    assert nav_1d_ago == pytest.approx(172.0)        # 173 today, +1/day
    assert latest_nav == pytest.approx(173.0)
    assert chg_1d == pytest.approx(round((latest_nav - nav_1d_ago) / nav_1d_ago * 100, 4))


def test_refresh_summary_table_flags_stale_scheme_inactive(db_con):
    con = connection.get_connection()
    row = con.execute(
        "SELECT is_active, change_1d_pct, nav_1d_ago, return_30d_pct FROM summary_table WHERE scheme_code = 333"
    ).fetchone()
    con.close()
    is_active, chg_1d, nav_1d_ago, ret_30d = row
    assert is_active is False
    assert chg_1d is None
    assert nav_1d_ago is None       # gated exactly like the percentage it feeds
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
    # The app-wide label, not the bare base name: every plan/option variant of a fund
    # shares that name, so a KPI card naming one was ambiguous.
    assert kpis["top_performer"]["name"] == "Test Equity Fund (Direct - Growth) [111]"
    assert kpis["lag_performer"]["name"] == "Test Liquid Fund (Direct - Growth) [222]"
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
    assert "Median 30D %" in df_all.columns
    assert "Top Fund (30D)" in df_all.columns, "the best fund is named, not just its return"

    # Test filtering by specific asset class
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
    assert kpis["top_performer"]["name"] == "Test Equity Fund (Direct - Growth) [111]"
    assert kpis["top_performer"]["return_pct"] == pytest.approx(73.0)
    # Lag performer: Test Liquid Fund
    assert kpis["lag_performer"]["name"] == "Test Liquid Fund (Direct - Growth) [222]"
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
    # 333 stopped publishing in January: its "return" covers a fortnight, not this window,
    # so it is counted as left out rather than ranked beside funds priced throughout.
    assert 333 not in df["scheme_code"].values
    assert df.attrs["exclusions"]["partial_window"] == 1
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
    assert kpi_data["top_performer"]["name"] == "Test Equity Fund (Direct - Growth) [111]"
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


def test_a_fund_with_too_few_peers_gets_no_alpha_rather_than_a_fake_one(db_con):
    """111 is the only large-cap fund priced across the window: its "category median" would
    be itself, and an alpha of exactly 0 against nobody."""
    df = db.get_advanced_leaders_dataframe(plan_type="Direct", start_date=datetime.date(2025, 1, 1), end_date=GLOBAL_MAX)
    row = df[df["scheme_code"] == 111].iloc[0]
    assert row["peer_count"] == 1
    assert pd.isna(row["cat_alpha_pct"]) and pd.isna(row["quartile"])


def _seed_peer_group(first_code, category, plan, returns_pct, option="Growth", start_day=0):
    """Schemes priced daily across DATES, each ending `returns_pct[i]` percent up."""
    con = connection.get_connection()
    n = len(DATES) - 1
    for i, r in enumerate(returns_pct):
        code = first_code + i
        con.execute("INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) "
                    "VALUES (%s, %s, 'Peer AMC', %s, %s, %s)", (code, f"Peer Fund {code}", category, plan, option))
        con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)",
                        [(code, d.isoformat(), 100.0 * (1 + r / 100.0) ** (j / n)) for j, d in enumerate(DATES) if j >= start_day])
    con.close()


def test_peers_are_the_same_category_and_plan_and_filters_never_move_alpha(db_con):
    """Direct is ranked against Direct: a Regular plan trails its own Direct twin by the
    distributor commission. And the Asset-class filter changes which funds are LISTED, not
    the median they are measured against."""
    _seed_peer_group(8001, "Equity Scheme - Mid Cap Fund", "Direct", [10, 12, 14, 16, 18])
    _seed_peer_group(8101, "Equity Scheme - Mid Cap Fund", "Regular", [9, 11, 13, 15, 17])
    db.refresh_summary_table()
    cache.clear_all_caches()
    kw = dict(start_date=datetime.date(2025, 1, 1), end_date=GLOBAL_MAX)
    df = db.get_advanced_leaders_dataframe(**kw).set_index("scheme_code")

    assert df.loc[8003, "cat_median_return"] == pytest.approx(14.0, abs=1e-3), "the Direct median"
    assert df.loc[8103, "cat_median_return"] == pytest.approx(13.0, abs=1e-3), "the Regular median"
    assert df.loc[8005, "cat_alpha_pct"] == pytest.approx(4.0, abs=1e-3)
    assert df.loc[8005, "peer_rank"] == 1 and df.loc[8005, "quartile"] == 1
    assert df.loc[8001, "quartile"] == 4 and df.loc[8001, "peer_percentile"] == pytest.approx(0.0)

    only_equity = db.get_advanced_leaders_dataframe(broad_cat="Equity", plan_type="Direct", **kw).set_index("scheme_code")
    assert only_equity.loc[8005, "cat_alpha_pct"] == pytest.approx(df.loc[8005, "cat_alpha_pct"])
    assert set(only_equity.index) == {8001, 8002, 8003, 8004, 8005, 111}


def test_idcw_payouts_and_late_launches_are_not_ranked(db_con):
    """An IDCW NAV falls by what it pays out -- on NAV that reads as a loss, so every payout
    made its plan a "laggard". A fund launched mid-window would compete on a shorter period."""
    _seed_peer_group(8201, "Equity Scheme - Mid Cap Fund", "Direct", [10, 12, 14, 16, 18])
    _seed_peer_group(8301, "Equity Scheme - Mid Cap Fund", "Direct", [-8], option="IDCW")
    _seed_peer_group(8401, "Equity Scheme - Mid Cap Fund", "Direct", [30], start_day=40)
    db.refresh_summary_table()
    cache.clear_all_caches()
    df = db.get_advanced_leaders_dataframe(start_date=datetime.date(2025, 1, 1), end_date=GLOBAL_MAX)
    assert 8301 not in df["scheme_code"].values and 8401 not in df["scheme_code"].values
    assert df.attrs["exclusions"]["idcw"] == 1
    assert df.attrs["exclusions"]["partial_window"] >= 2, "the late launch, and scheme 333"
    # Asked for IDCW plans, those are what is shown.
    idcw = db.get_advanced_leaders_dataframe(option_type="IDCW", start_date=datetime.date(2025, 1, 1), end_date=GLOBAL_MAX)
    assert list(idcw["scheme_code"]) == [8301]


def test_volatility_is_annualised_at_the_rate_the_fund_actually_prices(db_con):
    """A liquid fund prices every calendar day; scaling its daily moves by sqrt(252) as if it
    priced on trading days only understated its volatility by about a sixth."""
    df = db.get_advanced_leaders_dataframe(start_date=datetime.date(2025, 1, 1), end_date=GLOBAL_MAX)
    row = df[df["scheme_code"] == 111].iloc[0]
    assert row["obs_per_year"] == pytest.approx(365.25, abs=1.0), "DATES are consecutive calendar days"


def test_overview_macro_trend_api_inverted_dates(db_con):
    """Verifies /api/overview/macro-trend API endpoint handles inverted dates gracefully."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/overview/macro-trend?start_date=2025-02-15&end_date=2025-01-01")
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) > 0


def _seed_year(code, name, house, category, growth_per_day, plan="Direct", option="Growth", days=400, drop_on=None):
    """A scheme with `days` daily NAVs ending on GLOBAL_MAX, compounding at growth_per_day --
    long enough for summary_table to carry a trailing 1-year return. `drop_on` = (day, pct):
    an IDCW payout, a one-day NAV fall of that size."""
    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) "
        "VALUES (%s, %s, %s, %s, %s, %s)", (code, name, house, category, plan, option))
    first = GLOBAL_MAX - datetime.timedelta(days=days - 1)
    nav, rows = 100.0, []
    for i in range(days):
        if i:
            nav *= 1 + growth_per_day
        if drop_on and i == drop_on[0]:
            nav *= 1 - drop_on[1] / 100.0
        rows.append((code, (first + datetime.timedelta(days=i)).isoformat(), round(nav, 4)))
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
    con.close()


def _seed_fund_house_landscape():
    """Fifteen large-cap funds from three houses -- Skilled beats its peers, Average IS the
    peer median, Weak trails it -- plus a debt house whose liquid funds beat their own peers,
    and a one-fund house with the best return of all."""
    code = 5000
    for house, g in (("Skilled AMC", 0.0010), ("Average AMC", 0.0005), ("Weak AMC", 0.0002)):
        for _ in range(5):
            code += 1
            _seed_year(code, f"{house} Large Cap {code}", house, "Equity Scheme - Large Cap Fund", g)
    for house, g in (("Debt Star AMC", 0.00025), ("Liquid Peer AMC", 0.00015)):
        for _ in range(5):
            code += 1
            _seed_year(code, f"{house} Liquid {code}", house, "Debt Scheme - Liquid Fund", g)
    _seed_year(5999, "Lone Large Cap", "Lone AMC", "Equity Scheme - Large Cap Fund", 0.0030)
    db.refresh_summary_table()
    cache.clear_all_caches()


def test_fund_houses_are_scored_on_skill_not_on_how_much_equity_they_run(db_con):
    """The old league table averaged every scheme's raw return, so a house was ranked by its
    mix: any equity-heavy house beat any debt house in a rising market. Scored as the median
    gap to each scheme's own peers, a debt house whose liquid funds beat other liquid funds
    outranks an equity house whose funds are merely average -- though equity returned far more."""
    _seed_fund_house_landscape()
    card = {r["fund_house"]: r for r in db.get_market_overview_stats()["amc_scorecard"].to_dict("records")}

    assert card["Skilled AMC"]["median_alpha_1y"] > 0 and card["Skilled AMC"]["beat_peers_pct"] == pytest.approx(100.0)
    assert card["Average AMC"]["median_alpha_1y"] == pytest.approx(0.0, abs=1e-6)
    assert card["Weak AMC"]["median_alpha_1y"] < 0 and card["Weak AMC"]["beat_peers_pct"] == pytest.approx(0.0)
    assert card["Debt Star AMC"]["median_alpha_1y"] > card["Average AMC"]["median_alpha_1y"]
    order = [r["fund_house"] for r in db.get_market_overview_stats()["amc_scorecard"].to_dict("records")]
    assert order.index("Skilled AMC") < order.index("Average AMC") < order.index("Weak AMC")


def test_a_one_fund_house_is_not_ranked_on_one_lucky_fund(db_con):
    _seed_fund_house_landscape()
    houses = set(db.get_market_overview_stats()["amc_scorecard"]["fund_house"])
    assert "Lone AMC" not in houses, "best return of all, but one fund is not a house's record"
    assert {"Skilled AMC", "Average AMC", "Weak AMC", "Debt Star AMC", "Liquid Peer AMC"} <= houses


def test_the_scorecard_honours_the_global_plan_filter(db_con):
    _seed_fund_house_landscape()
    assert len(db.get_market_overview_stats()["amc_scorecard"]) > 0
    assert db.get_market_overview_stats(plan_type="Regular")["amc_scorecard"].empty, "every seeded scheme is Direct"


def test_an_idcw_payout_is_not_averaged_in_as_a_loss(db_con):
    """Debt's trailing year read 2.55% on live data while its Growth plans' median was 5.41%:
    every IDCW plan's NAV falls by what it pays out, and that fall was averaged in."""
    for code in range(6001, 6006):
        _seed_year(code, f"Steady Liquid {code}", "Liquid AMC", "Debt Scheme - Liquid Fund", 0.0002)
    _seed_year(6100, "Steady Liquid IDCW", "Liquid AMC", "Debt Scheme - Liquid Fund", 0.0002,
               option="IDCW", drop_on=(390, 6.0))
    db.refresh_summary_table()
    cache.clear_all_caches()
    stats = db.get_market_overview_stats()
    cash = next(r for r in stats["asset_dist"].to_dict("records") if r["asset_class"] == "Cash & Liquid")
    expected = ((1.0002 ** 365) - 1) * 100
    assert cash["avg_1y"] == pytest.approx(expected, abs=0.05), "the -6% payout would pull the mean down a whole point"
    assert stats["idcw_excluded"] == 1
    # Asked for IDCW plans, the page shows exactly those.
    only_idcw = db.get_market_overview_stats(option_type="IDCW")
    assert only_idcw["returns_pool"] == 1 and only_idcw["idcw_excluded"] == 0


def test_fund_houses_and_asset_classes_carry_amfis_reported_assets(db_con):
    """AUM is reported once per FUND, so it is summed over funds, never over the Direct and
    Regular schemes of one fund. A fund no scheme of ours matched still counts, classed from
    the feed's own taxonomy."""
    con = connection.get_connection()
    con.execute(
        "INSERT INTO amfi_fund_snapshot (fund_name, sub_category, category_id, fund_house, scheme_codes, aum_cr, as_of) VALUES "
        "('Test Equity Fund', 'Large Cap', 1, 'Test AMC', ARRAY[111]::bigint[], 300.0, '2025-03-14'),"
        "('Unmatched Liquid Fund', 'Liquid', 2, 'Second AMC', '{}', 100.0, '2025-03-14')")
    con.close()
    cache.clear_all_caches()
    stats = db.get_market_overview_stats()

    houses = {r["fund_house"]: r for r in stats["amc_aum"].to_dict("records")}
    assert houses["Test AMC"]["share_pct"] == pytest.approx(75.0) and houses["Second AMC"]["aum_cr"] == pytest.approx(100.0)
    assert stats["aum_total_cr"] == pytest.approx(400.0) and stats["aum_as_of"] == datetime.date(2025, 3, 14)
    by_class = {r["asset_class"]: r for r in stats["asset_dist"].to_dict("records")}
    assert by_class["Equity"]["aum_cr"] == pytest.approx(300.0)
    assert by_class["Cash & Liquid"]["aum_cr"] == pytest.approx(100.0) and by_class["Cash & Liquid"]["aum_share_pct"] == pytest.approx(25.0)


def test_the_category_matrix_folds_amfis_legacy_label_into_one_row(db_con):
    """AMFI files Mirae Asset Liquid under "Income/Debt Oriented Schemes - Liquid Fund" and
    Axis Liquid under "Debt Scheme - Liquid Fund": one category, which showed as two rows."""
    con = connection.get_connection()
    con.execute("INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) "
                "VALUES (777, 'Legacy Label Liquid Fund', 'Test AMC', 'Income/Debt Oriented Schemes - Liquid Fund', 'Direct', 'Growth')")
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)",
                    [(777, d.isoformat(), 20.0 + i * 0.01) for i, d in enumerate(DATES)])
    con.close()
    db.refresh_summary_table()
    cache.clear_all_caches()
    rows = db.get_category_performance_matrix().to_dict("records")
    liquid = [r for r in rows if "Liquid" in r["Category"]]
    assert len(liquid) == 1 and liquid[0]["Category"] == "Liquid Fund" and liquid[0]["Schemes"] == 2
    assert liquid[0]["Asset Class"] == "Cash & Liquid"


def _ter_days(code, start, days, total, base=None):
    """The dense daily rows AMFI actually publishes, for one scheme over `days` days."""
    return pd.DataFrame([{
        "scheme_code": code,
        "ter_date": start + datetime.timedelta(days=i),
        "base_expense_ratio_pct": (total - 0.1) if base is None else base,
        "brokerage_cost_pct": 0.03, "transaction_cost_pct": 0.0,
        "statutory_levies_pct": 0.07, "total_ter_pct": total,
        "source_url": "https://www.amfiindia.com/ter-of-mf-schemes",
    } for i in range(days)])


def _runs(code):
    con = connection.get_connection()
    rows = con.execute(
        "SELECT ter_date, valid_to, total_ter_pct FROM ter_history WHERE scheme_code = %s "
        "ORDER BY ter_date", (code,)).fetchall()
    con.close()
    return [(r[0], r[1], float(r[2])) for r in rows]


class TestTerChangePointStorage:
    """AMFI republishes identical TER figures every calendar day. Storage keeps one row per
    change, spanning ter_date..valid_to, so these pin the merge rules that a dense
    delete-by-key-then-insert never had to think about."""

    def test_identical_days_collapse_into_one_period(self, db_con):
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 1), 30, 0.82))
        assert _runs(111) == [(datetime.date(2026, 4, 1), datetime.date(2026, 4, 30), 0.82)]

    def test_a_change_starts_a_new_period_and_closes_the_old_one(self, db_con):
        db.upsert_ter_history(pd.concat([
            _ter_days(111, datetime.date(2026, 4, 1), 10, 0.82),
            _ter_days(111, datetime.date(2026, 4, 11), 10, 0.79),
        ]))
        assert _runs(111) == [
            (datetime.date(2026, 4, 1), datetime.date(2026, 4, 10), 0.82),
            (datetime.date(2026, 4, 11), datetime.date(2026, 4, 20), 0.79),
        ]

    def test_a_publication_gap_breaks_the_period_rather_than_being_bridged(self, db_con):
        """Bridging would invent disclosures that never happened and silently reweight the
        duration-weighted mean that fee_drag builds on."""
        db.upsert_ter_history(pd.concat([
            _ter_days(111, datetime.date(2026, 4, 1), 5, 0.82),
            _ter_days(111, datetime.date(2026, 5, 1), 5, 0.82),  # same TER, month-long gap
        ]))
        assert _runs(111) == [
            (datetime.date(2026, 4, 1), datetime.date(2026, 4, 5), 0.82),
            (datetime.date(2026, 5, 1), datetime.date(2026, 5, 5), 0.82),
        ]

    def test_resyncing_the_same_month_changes_nothing(self, db_con):
        """The historical backfill re-enters months it has already written, and its
        checkpoint/resume logic assumes a repeat is harmless."""
        batch = _ter_days(111, datetime.date(2026, 4, 1), 30, 0.82)
        db.upsert_ter_history(batch)
        first = _runs(111)
        db.upsert_ter_history(batch)
        assert _runs(111) == first

    def test_an_adjacent_month_extends_the_period_instead_of_starting_another(self, db_con):
        """The backfill walks backwards, so the earlier month arrives second and has to
        merge into the run already stored rather than sit beside it."""
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 5, 1), 31, 0.82))
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 1), 30, 0.82))
        assert _runs(111) == [(datetime.date(2026, 4, 1), datetime.date(2026, 5, 31), 0.82)]

    def test_a_correction_inside_a_stored_period_splits_it(self, db_con):
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 1), 10, 0.82))
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 5), 1, 0.90))
        assert _runs(111) == [
            (datetime.date(2026, 4, 1), datetime.date(2026, 4, 4), 0.82),
            (datetime.date(2026, 4, 5), datetime.date(2026, 4, 5), 0.90),
            (datetime.date(2026, 4, 6), datetime.date(2026, 4, 10), 0.82),
        ]

    def test_one_scheme_is_not_disturbed_by_another_schemes_sync(self, db_con):
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 1), 30, 0.82))
        db.upsert_ter_history(_ter_days(222, datetime.date(2026, 4, 1), 30, 0.20))
        assert _runs(111) == [(datetime.date(2026, 4, 1), datetime.date(2026, 4, 30), 0.82)]
        assert _runs(222) == [(datetime.date(2026, 4, 1), datetime.date(2026, 4, 30), 0.20)]

    def test_syncing_one_month_leaves_earlier_months_intact(self, db_con):
        """A monthly sync must only rewrite the window it fetched. Deleting by scheme rather
        than by window would drop every month behind it."""
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 1), 30, 0.50))
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 9, 1), 23, 0.60))
        assert _runs(111) == [
            (datetime.date(2026, 4, 1), datetime.date(2026, 4, 30), 0.50),
            (datetime.date(2026, 9, 1), datetime.date(2026, 9, 23), 0.60),
        ]

    def test_a_long_period_is_not_truncated_by_a_sync_touching_only_its_tail(self, db_con):
        """The dangerous case: one period already spans months, and a sync arrives covering
        only its last few days. The period overlaps the window, so it is rebuilt -- and every
        day of it outside the batch has to come back."""
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 1), 120, 0.50))
        stored = _runs(111)
        assert stored == [(datetime.date(2026, 4, 1), datetime.date(2026, 7, 29), 0.50)]

        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 7, 25), 5, 0.55))
        assert _runs(111) == [
            (datetime.date(2026, 4, 1), datetime.date(2026, 7, 24), 0.50),
            (datetime.date(2026, 7, 25), datetime.date(2026, 7, 29), 0.55),
        ]

    def test_a_partial_fetch_does_not_delete_days_it_failed_to_refetch(self, db_con):
        """AMFI drops pages under load. A batch missing days in the middle of a stored period
        must leave those days alone rather than erase them."""
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 1), 30, 0.50))
        partial = pd.concat([
            _ter_days(111, datetime.date(2026, 4, 1), 5, 0.50),
            _ter_days(111, datetime.date(2026, 4, 26), 5, 0.50),  # days 6-25 never arrived
        ])
        db.upsert_ter_history(partial)
        assert _runs(111) == [(datetime.date(2026, 4, 1), datetime.date(2026, 4, 30), 0.50)]

    def test_an_authoritative_refetch_removes_days_the_portal_no_longer_claims(self, db_con):
        """The repair case. A merge can add days and correct figures but can never remove a
        day that should not be there, because the stored value survives as an observation.
        A complete re-fetch has to be able to say "these are the only days there were"."""
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 1), 30, 0.50))
        truth = pd.concat([
            _ter_days(111, datetime.date(2026, 4, 1), 5, 0.50),
            _ter_days(111, datetime.date(2026, 4, 26), 5, 0.50),
        ])
        db.upsert_ter_history(truth, authoritative=True)
        assert _runs(111) == [
            (datetime.date(2026, 4, 1), datetime.date(2026, 4, 5), 0.50),
            (datetime.date(2026, 4, 26), datetime.date(2026, 4, 30), 0.50),
        ]

    def test_an_authoritative_refetch_of_one_month_spares_the_others(self, db_con):
        """Authority extends only over the days the batch covers. Months either side must
        survive untouched, and a period running into the window must keep its head."""
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 1), 61, 0.50))  # Apr+May
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 5, 1), 10, 0.50), authoritative=True)
        # April and the rest of May are carried through untouched, so every day is still
        # present and still 0.50 -- which means one unbroken period, not three fragments.
        assert _runs(111) == [(datetime.date(2026, 4, 1), datetime.date(2026, 5, 31), 0.50)]

    def test_authority_is_scoped_to_the_batch_even_when_the_figures_change(self, db_con):
        """Same carry-through, but with a value change inside the window, so the period
        genuinely splits and the edges prove April and late May were not rewritten."""
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 1), 61, 0.50))
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 5, 1), 10, 0.55), authoritative=True)
        assert _runs(111) == [
            (datetime.date(2026, 4, 1), datetime.date(2026, 4, 30), 0.50),
            (datetime.date(2026, 5, 1), datetime.date(2026, 5, 10), 0.55),
            (datetime.date(2026, 5, 11), datetime.date(2026, 5, 31), 0.50),
        ]

    def test_a_merging_refetch_still_protects_days_a_dropped_page_lost(self, db_con):
        """The counterpart guard: when AMFI loses pages the batch is NOT authoritative, and
        the days it failed to re-deliver must survive."""
        db.upsert_ter_history(_ter_days(111, datetime.date(2026, 4, 1), 30, 0.50))
        partial = pd.concat([
            _ter_days(111, datetime.date(2026, 4, 1), 5, 0.50),
            _ter_days(111, datetime.date(2026, 4, 26), 5, 0.50),
        ])
        db.upsert_ter_history(partial, authoritative=False)
        assert _runs(111) == [(datetime.date(2026, 4, 1), datetime.date(2026, 4, 30), 0.50)]

    def test_every_stored_day_is_still_answerable(self, db_con):
        """The whole promise of the format: no calendar day that had a disclosure may become
        unreachable, and each must return the figures it originally carried."""
        days = pd.concat([
            _ter_days(111, datetime.date(2026, 4, 1), 20, 0.82),
            _ter_days(111, datetime.date(2026, 4, 21), 10, 0.79),
        ])
        db.upsert_ter_history(days)
        con = connection.get_connection()
        for _, row in days.iterrows():
            found = con.execute(
                "SELECT total_ter_pct FROM ter_history WHERE scheme_code = 111 "
                "AND %s BETWEEN ter_date AND valid_to", (row["ter_date"],)).fetchone()
            assert found is not None and float(found[0]) == pytest.approx(row["total_ter_pct"])
        con.close()


def _ter_record(code, when, total):
    return {
        "scheme_code": code, "ter_date": when,
        "base_expense_ratio_pct": total - 0.1, "brokerage_cost_pct": 0.03,
        "transaction_cost_pct": 0.0, "statutory_levies_pct": 0.07,
        "total_ter_pct": total, "source_url": "https://www.amfiindia.com/ter-of-mf-schemes",
        "ter_source": "AMFI TER Disclosure (test)",
    }


def test_an_older_ter_row_neither_updates_nor_rebuilds_the_cache(db_con, monkeypatch):
    """A historical TER backfill walks backwards in time, so a month it fetches normally
    carries an as-of date older than the one already stored and the regression guard blocks
    it. Each of those no-op months was still paying for a full summary_table rebuild --
    7.7s measured against the live database, to arrive at a byte-identical table."""
    con = connection.get_connection()
    con.execute("UPDATE schemes SET ter_as_of_date = %s WHERE scheme_code = 111",
                (datetime.date(2026, 9, 1),))
    con.close()

    rebuilds = []
    monkeypatch.setattr(db, "refresh_summary_table", lambda *a, **k: rebuilds.append(1))

    stale = pd.DataFrame([_ter_record(111, datetime.date(2018, 6, 29), 0.5)])
    assert db.apply_latest_official_ter(stale)["updated"] == 0
    assert rebuilds == [], "nothing was updated, so there was nothing to rebuild"

    fresh = pd.DataFrame([_ter_record(111, datetime.date(2026, 9, 22), 0.75)])
    assert db.apply_latest_official_ter(fresh)["updated"] == 1
    assert len(rebuilds) == 1, "a row that did land must still refresh the cache"

    con = connection.get_connection()
    ter, as_of = con.execute(
        "SELECT expense_ratio, ter_as_of_date FROM schemes WHERE scheme_code = 111").fetchone()
    con.close()
    assert float(ter) == pytest.approx(0.75)
    assert as_of == datetime.date(2026, 9, 22), "the stale June 2018 row must not have won"


def _insert_payout_schemes():
    """Two schemes whose NAV falls steadily, the way an IDCW scheme's does when it pays out.

    555 is labelled IDCW. 666 is labelled Growth but *named* IDCW -- AMFI ships 46 live
    schemes like that, so the column alone does not identify them. Both fall further than
    any honest fund in the base dataset (111 rises, 222 is flat), so either one would take
    the Lagging Performer card if it were eligible."""
    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) VALUES "
        "(555, 'Test Payout Fund - IDCW', 'Test AMC', 'Equity Scheme - Large Cap Fund', 'Direct', 'IDCW'),"
        "(666, 'Test Mislabelled IDCW Fund', 'Test AMC', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth')"
    )
    rows = []
    for i, d in enumerate(DATES):
        rows.append((555, d.isoformat(), 100.0 - i * 0.5))
        rows.append((666, d.isoformat(), 100.0 - i * 0.8))
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
    con.close()
    db.refresh_summary_table()
    cache.clear_all_caches()


def test_an_idcw_payout_does_not_take_the_lagging_performer_card(db_con):
    """The whole point of the exclusion: on live data the market's worst 30-day "performer"
    was an HDFC FMP IDCW plan at -20.95% that had merely distributed, while the worst fund
    that actually lost money was down 7.00%. The card named the payout."""
    _insert_payout_schemes()
    kpis = db.get_kpis(start_date=DATES[0], end_date=GLOBAL_MAX)

    assert "IDCW" not in kpis["lag_performer"]["name"]
    assert kpis["lag_performer"]["name"] == "Test Liquid Fund (Direct - Growth) [222]"
    assert kpis["lag_performer"]["return_pct"] == pytest.approx(0.0), \
        "the flat fund is the worst real performer once the two payout schemes are out"


def test_the_lagging_card_number_belongs_to_the_fund_it_names(db_con):
    """The return used to come from a MIN() over the unfiltered pool while the name came from
    a separate row, so excluding IDCW would have captioned the payout's -30% with the flat
    fund's name -- a worse bug than the one being fixed."""
    _insert_payout_schemes()
    kpis = db.get_kpis(start_date=DATES[0], end_date=GLOBAL_MAX)

    con = connection.get_connection()
    named_code = int(kpis["lag_performer"]["name"].split("[")[1].rstrip("]"))
    first, last = con.execute(
        "SELECT (SELECT nav FROM nav_history WHERE scheme_code = %s ORDER BY nav_date ASC LIMIT 1),"
        "       (SELECT nav FROM nav_history WHERE scheme_code = %s ORDER BY nav_date DESC LIMIT 1)",
        (named_code, named_code)).fetchone()
    con.close()

    expected = (float(last) - float(first)) / float(first) * 100.0
    assert kpis["lag_performer"]["return_pct"] == pytest.approx(expected, abs=1e-4)


def test_a_scheme_named_idcw_is_excluded_even_when_labelled_growth(db_con):
    """666 falls harder than 555, so if only option_type were checked it would take the card."""
    _insert_payout_schemes()
    kpis = db.get_kpis(start_date=DATES[0], end_date=GLOBAL_MAX)

    assert "666" not in kpis["lag_performer"]["name"]


def test_the_idcw_rule_also_applies_without_a_date_window(db_con):
    """The no-window branch is a separate query over summary_table.return_30d_pct, and it
    ranked with its own MAX()/MIN(); fixing only the windowed branch would leave the default
    page load -- which is the one most people see -- still naming the payout."""
    _insert_payout_schemes()
    kpis = db.get_kpis()

    assert "IDCW" not in kpis["lag_performer"]["name"]
    assert "666" not in kpis["lag_performer"]["name"]


def test_filtering_to_idcw_brings_those_schemes_back(db_con):
    """Asking for IDCW and getting an empty card would be the wrong kind of protection."""
    _insert_payout_schemes()
    kpis = db.get_kpis(option_type="IDCW", start_date=DATES[0], end_date=GLOBAL_MAX)

    # The option is not repeated in the label when the scheme name already carries it.
    assert kpis["lag_performer"]["name"] == "Test Payout Fund - IDCW (Direct) [555]"


def test_the_idcw_rule_does_not_touch_the_breadth_counts(db_con):
    """The exclusion is scoped to the two cards that name a single fund. Advancers/decliners
    and the median describe how the market moved, and an IDCW scheme's NAV move is real."""
    _insert_payout_schemes()
    kpis = db.get_kpis(start_date=DATES[0], end_date=GLOBAL_MAX)

    assert kpis["decliners"] == 2, "both payout schemes still count as decliners"
    assert kpis["unchanged"] == 1 and kpis["advancers"] == 2
    assert kpis["advancers"] + kpis["decliners"] + kpis["unchanged"] == 5, \
        "111 and 333 up, 222 unchanged, 555 and 666 down -- 333 stops publishing mid-window " \
        "but still has a return inside it, so breadth counts all five"




def _seed_path(code, name, category, path, plan="Direct"):
    """A scheme whose NAV on DATES[i] is path(i)."""
    con = connection.get_connection()
    con.execute("INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) "
                "VALUES (%s, %s, 'Rotation AMC', %s, %s, 'Growth')", (code, name, category, plan))
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)",
                    [(code, d.isoformat(), round(path(i), 4)) for i, d in enumerate(DATES)])
    con.close()


def test_rotation_tells_a_fading_leader_from_a_late_riser(db_con):
    """Mid Cap rose 10% early in the window and went flat; Flexi Cap sat still and rose 2% at
    the end. Over the window Mid Cap is ahead of the market; lately it is behind: Weakening.
    Flexi Cap is the reverse: Improving. A single ranked bar per category -- what this tab
    used to draw -- shows only that Mid Cap "won"."""
    turn = 49  # DATES[49] is the first day of the window's last third (24 of 73 days)
    for k in range(5):
        _seed_path(9001 + k, f"Early Riser {k}", "Equity Scheme - Mid Cap Fund",
                   lambda i: 100 * 1.10 ** (min(i, turn) / turn))
        _seed_path(9101 + k, f"Late Riser {k}", "Equity Scheme - Flexi Cap Fund",
                   lambda i: 100 * 1.02 ** (max(0, i - turn) / (len(DATES) - 1 - turn)))
    # The Regular plan of one early riser: the same fund, which must not count twice.
    _seed_path(9201, "Early Riser 0", "Equity Scheme - Mid Cap Fund", lambda i: 99 * 1.10 ** (min(i, turn) / turn), plan="Regular")
    db.refresh_summary_table()
    cache.clear_all_caches()

    rot = db.get_category_rotation(start_date=DATES[0], end_date=GLOBAL_MAX)
    cats = {c["category"]: c for c in rot["categories"]}
    assert rot["recent_days"] == 24 and rot["unit"] == "week"
    mid, flexi = cats["Mid Cap Fund"], cats["Flexi Cap Fund"]
    assert mid["funds"] == 5, "a fund's Direct and Regular plans are one fund"
    assert mid["median_return"] == pytest.approx(10.0, abs=1e-3) and mid["recent_median_return"] == pytest.approx(0.0, abs=1e-3)
    assert mid["strength_pp"] > 0 > mid["momentum_pp"] and mid["quadrant"] == "Weakening"
    assert flexi["strength_pp"] < 0 < flexi["momentum_pp"] and flexi["quadrant"] == "Improving"
    # Period by period: Mid Cap's gains are all in the early weeks, none in the last.
    assert len(mid["heat"]) == len(rot["periods"]) >= 10
    assert mid["heat"][1] > 0 and mid["heat"][-1] == pytest.approx(0.0, abs=1e-6)
    assert flexi["heat"][1] == pytest.approx(0.0, abs=1e-6) and flexi["heat"][-1] > 0

    only_equity = db.get_category_rotation(start_date=DATES[0], end_date=GLOBAL_MAX, broad_cat="Equity")
    assert only_equity["market_median"] == rot["market_median"], "filtering lists fewer categories, never moves the market"
    assert {c["asset_class"] for c in only_equity["categories"]} == {"Equity"}


def test_a_fund_younger_than_a_year_has_no_1y_return(db_con):
    """The 1Y base used to be the NAV NEAREST to 365 days back from a 330..420-day band, so a
    fund 342 days old reported its since-launch return as a "1-year" return (52 live schemes)
    and was ranked beside real one-year records. It is now the NAV as of a year ago."""
    _seed_year(7001, "Young Fund", "Young AMC", "Equity Scheme - Flexi Cap Fund", 0.001, days=342)
    _seed_year(7002, "Old Fund", "Old AMC", "Equity Scheme - Flexi Cap Fund", 0.001, days=400)
    db.refresh_summary_table()
    cache.clear_all_caches()
    con = connection.get_connection()
    try:
        rows = dict(con.execute("SELECT scheme_code, return_1y_pct FROM summary_table WHERE scheme_code IN (7001, 7002)").fetchall())
    finally:
        con.close()
    assert rows[7001] is None
    # Exactly 365 daily steps of 0.1% -- the NAV on the date a year back, not a nearby one.
    assert rows[7002] == pytest.approx((1.001 ** 365 - 1) * 100, abs=1e-3)
