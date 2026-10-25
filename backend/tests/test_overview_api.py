"""API tests for the numbers the Overview page shows most prominently: the pulse KPI tiles,
the leaders table's benchmark columns, the rotation tab's market row, and the leaders
volatility-lookback contract. Hand-computed values, on test_db_queries.py's dataset
(111 +73% over the window, 222 flat, 333 stale) plus the peer groups seeded here."""

import datetime

import pytest
from fastapi.testclient import TestClient

from app.core import cache
from app.db import connection
from app.db import queries as db
from app.main import app
from tests.test_db_queries import DATES, GLOBAL_MAX, _seed_path, _seed_peer_group, db_con  # noqa: F401 (fixture)

WINDOW = f"start_date={DATES[0].isoformat()}&end_date={GLOBAL_MAX.isoformat()}"
LEADERS_WINDOW = f"start={DATES[0].isoformat()}&end={GLOBAL_MAX.isoformat()}"


def _refresh():
    db.refresh_summary_table()
    cache.clear_all_caches()


@pytest.fixture()
def market(db_con):
    """Mid Cap 8001..8005 up 10..18%, Small Cap 8011..8015 at -6, -4, -2, -1, +1%."""
    _seed_peer_group(8001, "Equity Scheme - Mid Cap Fund", "Direct", [10, 12, 14, 16, 18])
    _seed_peer_group(8011, "Equity Scheme - Small Cap Fund", "Direct", [-6, -4, -2, -1, 1])
    _refresh()


def test_pulse_kpis_are_the_whole_markets(market):
    body = TestClient(app).get(f"/api/overview/pulse-kpis?{WINDOW}").json()
    # 10 peers + 111 + 222; 333 stopped publishing mid-window, so it is not ranked.
    assert body["ranked_funds"] == 12
    assert (body["advancers"], body["decliners"], body["unchanged"]) == (7, 4, 1)
    # Sorted: -6 -4 -2 -1 0 1 | 10 12 14 16 18 73 -> (1 + 10) / 2.
    assert body["median_return"] == pytest.approx(5.5, abs=1e-3)
    assert body["mean_return"] == pytest.approx(131 / 12, abs=1e-3)
    # 8005 is 4 pp ahead of the Mid Cap median (14); Small Cap's best is 3 pp ahead of -2.
    assert body["top_alpha"]["return_pct"] == pytest.approx(4.0, abs=1e-3)
    assert "8005" in body["top_alpha"]["name"]
    assert body["leading_category"]["name"] == "Mid Cap Fund (Equity)"
    assert body["leading_category"]["return_pct"] == pytest.approx(14.0, abs=1e-3)
    assert body["leading_category"]["funds"] == 5
    assert body["lagging_category"]["name"] == "Small Cap Fund (Equity)"
    assert body["lagging_category"]["return_pct"] == pytest.approx(-2.0, abs=1e-3)
    assert body["excluded_partial_window"] >= 1, "scheme 333"
    assert (body["first_nav_date"], body["last_nav_date"]) == (DATES[0].isoformat(), GLOBAL_MAX.isoformat())


def test_leaders_rows_carry_amfis_benchmark_figures(market):
    con = connection.get_connection()
    try:
        con.execute(
            "INSERT INTO amfi_fund_snapshot (fund_name, sub_category, fund_house, scheme_codes, benchmark, "
            "return_1y_direct, return_1y_regular, return_1y_benchmark, as_of) "
            "VALUES ('Peer Fund 8005', 'Mid Cap Fund', 'Peer AMC', '{8005}', 'NIFTY Midcap 150 TRI', 20.0, 19.0, 15.0, %s)",
            (GLOBAL_MAX,))
    finally:
        con.close()
    cache.clear_all_caches()
    body = TestClient(app).get(f"/api/leaders?{LEADERS_WINDOW}").json()
    rows = {r["scheme_code"]: r for r in body["rows"]}
    assert rows[8005]["official_1y_pct"] == pytest.approx(20.0), "the Direct plan's own figure"
    assert rows[8005]["benchmark_1y_pct"] == pytest.approx(15.0)
    assert rows[8005]["excess_1y_pp"] == pytest.approx(5.0)
    assert rows[8005]["benchmark"] == "NIFTY Midcap 150 TRI"
    assert rows[8004]["excess_1y_pp"] is None and rows[8004]["benchmark"] is None


@pytest.fixture()
def rotation_market(db_con):
    """test_db_queries' early and late risers, plus three falling gilt funds: a Debt median
    that differs from the whole market's in every period."""
    turn = 49
    for k in range(5):
        _seed_path(9001 + k, f"Early Riser {k}", "Equity Scheme - Mid Cap Fund",
                   lambda i: 100 * 1.10 ** (min(i, turn) / turn))
        _seed_path(9101 + k, f"Late Riser {k}", "Equity Scheme - Flexi Cap Fund",
                   lambda i: 100 * 1.02 ** (max(0, i - turn) / (len(DATES) - 1 - turn)))
    for k in range(3):
        _seed_path(9301 + k, f"Gilt Fund {k}", "Debt Scheme - Gilt Fund", lambda i, k=k: 100 * (1 - (0.01 + 0.002 * k) * i / 73))
    _refresh()


_MARKET_FIELDS = ("periods", "market_period_median", "unit", "recent_days", "market_median", "market_recent_median")


@pytest.mark.parametrize("asset_class", ["Equity", "Debt"])
def test_the_rotation_market_row_is_the_whole_market_under_any_class_filter(rotation_market, asset_class):
    """The "Market (median fund)" row used to be the median of the filtered class's funds."""
    client = TestClient(app)
    everything = client.get(f"/api/leaders/rotation?{LEADERS_WINDOW}").json()
    one_class = client.get(f"/api/leaders/rotation?{LEADERS_WINDOW}&broad_cat={asset_class}").json()
    assert everything["periods"], "the dataset spans several weeks"
    for field in _MARKET_FIELDS:
        assert one_class[field] == everything[field], field
    narrowed = [c for c in everything["categories"] if c["asset_class"] == asset_class]
    assert narrowed, f"the dataset has {asset_class} categories"
    assert one_class["categories"] == narrowed


def test_an_unknown_volatility_lookback_is_rejected(db_con):
    """Each made-up value used to cost its own cold scan and cache entry."""
    res = TestClient(app).get(f"/api/leaders?{LEADERS_WINDOW}&vol_lookback=bogus")
    assert res.status_code == 422


@pytest.fixture()
def universe_calls(monkeypatch):
    calls = []
    real = db._leaders_universe

    def spy(start_date, end_date, vol_lookback):
        calls.append(vol_lookback)
        return real(start_date, end_date, vol_lookback)

    monkeypatch.setattr(db, "_leaders_universe", spy)
    return calls


@pytest.mark.parametrize("lookback", ["6M", "3Y", "Full Window"])
def test_a_lookback_that_changes_nothing_shares_the_1y_scan(market, universe_calls, lookback):
    """On a 74-day window every lookback starts the volatility at the window's start."""
    body = TestClient(app).get(f"/api/leaders?{LEADERS_WINDOW}&vol_lookback={lookback.replace(' ', '%20')}").json()
    assert universe_calls == ["1Y"]
    assert body["vol_lookback"] == lookback, "the page's own choice is echoed back"
    assert body["window"]["vol_start"] == DATES[0].isoformat()


def test_a_lookback_that_changes_the_scan_is_kept(market, universe_calls):
    start = datetime.date(2024, 1, 1)  # over 180 days: 6M starts later than 1Y
    TestClient(app).get(f"/api/leaders?start={start.isoformat()}&end={GLOBAL_MAX.isoformat()}&vol_lookback=6M")
    assert universe_calls == ["6M"]


def test_an_overlong_search_is_rejected(db_con):
    assert TestClient(app).get(f"/api/leaders?{LEADERS_WINDOW}&search={'a' * 201}").status_code == 422


@pytest.mark.parametrize("path", [
    "/api/overview/stats?plan_type=x1",
    "/api/overview/all-funds?option_type=x1",
    f"/api/overview/pulse-kpis?{WINDOW}&plan_type=x1",
    f"/api/overview/macro-trend?{WINDOW}&option_type=x1",
    "/api/overview/category-matrix?plan_type=x1",
    f"/api/leaders?{LEADERS_WINDOW}&plan_type=x1",
    f"/api/leaders/rotation?{LEADERS_WINDOW}&option_type=x1",
])
def test_an_unknown_plan_or_option_is_rejected(db_con, path):
    """Each made-up value was its own cache entry and, on the leaders routes, a cold scan."""
    res = TestClient(app).get(path)
    assert res.status_code == 422
    assert "x1" in res.json()["detail"]


@pytest.mark.parametrize("query", ["plan_type=Direct", "plan_type=Regular", "option_type=Growth",
                                   f"option_type={db.UNSPECIFIED_FILTER}", "plan_type=&option_type="])
def test_the_pickers_own_values_are_accepted(market, query):
    assert TestClient(app).get(f"/api/leaders?{LEADERS_WINDOW}&{query}").status_code == 200


@pytest.mark.parametrize("bad", [datetime.date(1989, 12, 31), datetime.date.today() + datetime.timedelta(days=2)])
def test_a_window_outside_the_data_is_rejected(db_con, bad):
    client = TestClient(app)
    assert client.get(f"/api/overview/pulse-kpis?start_date={bad.isoformat()}&end_date={GLOBAL_MAX.isoformat()}").status_code == 422
    assert client.get(f"/api/leaders?start={DATES[0].isoformat()}&end={bad.isoformat()}").status_code == 422
    assert client.get(f"/api/leaders/rotation?start={bad.isoformat()}&end={GLOBAL_MAX.isoformat()}").status_code == 422


def test_tomorrow_is_accepted(market):
    """AMFI's day can run ahead of the browser's clock near midnight."""
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    assert TestClient(app).get(f"/api/leaders?start={DATES[0].isoformat()}&end={tomorrow.isoformat()}").status_code == 200
