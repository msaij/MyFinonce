"""Holdings Phase 2: time-weighted index, periods, attribution, allocation, targets."""

import datetime
import functools

import pytest
from fastapi.testclient import TestClient

from app.db import connection
from app.main import app
from app.services import holdings_analytics as ha
from tests.holdings_support import add_ok, business_days, nav_on, new_portfolio, seed

d = datetime.date
BENCHMARK = 1003
# These suites check unit/value arithmetic exactly, so stamp duty is off by default.
_add = functools.partial(add_ok, apply_stamp_duty=False)
_pf = functools.partial(new_portfolio, benchmark=BENCHMARK)


@pytest.fixture()
def client(pg_db):
    days = list(business_days(d(2023, 1, 2), d(2024, 12, 31)))
    seed(
        [(1001, "Alpha Flexi Cap Fund - Direct Plan - Growth", "Alpha MF", "Equity Scheme - Flexi Cap Fund", "Direct", "Growth"),
         (1002, "Beta Liquid Fund - Direct Plan - Growth", "Beta MF", "Debt Scheme - Liquid Fund", "Direct", "Growth"),
         (BENCHMARK, "Gamma Nifty 50 Index Fund - Direct Plan - Growth", "Gamma MF", "Other Scheme - Index Funds", "Direct", "Growth")],
        {1001: [(day, 10.0 * 1.0005 ** i) for i, day in enumerate(days)],       # steady equity-ish growth
         1002: [(day, 1000.0 + 0.18 * i) for i, day in enumerate(days)],        # liquid fund crawl
         BENCHMARK: [(day, 20.0 * 1.0004 ** i) for i, day in enumerate(days)]},
    )
    return TestClient(app)

# --- Classification --------------------------------------------------------------

@pytest.mark.parametrize("category,broad,name,expected", [
    ("Equity Scheme - Flexi Cap Fund", "Equity", "Parag Parikh Flexi Cap Fund", "Equity"),
    ("Debt Scheme - Liquid Fund", "Debt", "HDFC Liquid Fund", "Cash & Liquid"),
    ("Debt Scheme - Corporate Bond Fund", "Debt", "ICICI Corporate Bond Fund", "Debt"),
    ("Hybrid Scheme - Balanced Advantage", "Hybrid", "HDFC Balanced Advantage Fund", "Hybrid"),
    ("Other Scheme - Index Funds", "Other / Index / ETF", "UTI Nifty 50 Index Fund", "Equity"),
    ("Other Scheme - Index Funds", "Other / Index / ETF", "Bharat Bond ETF FoF April 2030", "Debt"),
    ("Other Scheme - Other ETFs", "Other / Index / ETF", "Nippon India ETF Gold BeES", "Gold & Commodities"),
    ("Other Scheme - FoF Overseas", "Other / Index / ETF", "Motilal Oswal Nasdaq 100 FoF", "International Equity"),
    ("Other Scheme - Index Funds", "Other / Index / ETF", "Motilal Oswal S&P 500 Index Fund", "International Equity"),
    ("Solution Oriented Scheme - Retirement Fund", "Solution Oriented", "HDFC Retirement Savings Fund - Equity Plan", "Hybrid"),
])
def test_every_scheme_gets_exactly_one_asset_class(category, broad, name, expected):
    assert ha.classify(category, broad, name) == expected
    assert expected in ha.ASSET_CLASSES


# --- Time-weighted index -----------------------------------------------------------

def test_twr_tracks_the_fund_not_the_cash_flows(client):
    """Adding money mid-way changes the rupee value but must not change the
    time-weighted return: with one fund, TWR == that fund's NAV return."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="SIP", trade_date="2023-09-01", units=250)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="REDEEM", trade_date="2024-02-01", units=80)
    perf = client.get(f"/api/holdings/portfolios/{pid}/performance").json()
    twr = perf["series"]["twr_index"]
    fund_return = nav_on(1001, d(2024, 12, 31)) / nav_on(1001, d(2023, 3, 1))
    assert twr[-1] / 100.0 == pytest.approx(fund_return, rel=1e-4)
    since = next(p for p in perf["periods"] if p["label"] == "Since start")
    assert since["annualised"] is True


def test_value_and_invested_series_end_where_the_summary_does(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", amount=50000)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="SIP", trade_date="2023-06-01", amount=20000)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="REDEEM", trade_date="2024-01-02", amount=10000)
    perf = client.get(f"/api/holdings/portfolios/{pid}/performance").json()
    summ = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]
    assert perf["series"]["value"][-1] == pytest.approx(summ["current_value"], abs=0.02)
    assert perf["series"]["invested"][-1] == pytest.approx(summ["net_contributed"], abs=0.02)
    since = next(p for p in perf["periods"] if p["label"] == "Since start")
    assert since["xirr_pct"] == pytest.approx(summ["xirr_pct"], abs=1e-6)


def test_switches_do_not_move_the_portfolio_twr(client):
    """A switch at that day's NAVs is value-neutral, so TWR is continuous across it."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="SWITCH", trade_date="2023-09-01",
         redeem_all=True, switch_to_scheme_code=1002)
    perf = client.get(f"/api/holdings/portfolios/{pid}/performance").json()
    dates, twr = perf["series"]["dates"], perf["series"]["twr_index"]
    i = dates.index("2023-09-01")
    # Day of the switch: equity leg's own daily move, not a jump from the flows.
    equity_move = nav_on(1001, d(2023, 9, 1)) / nav_on(1001, d(2023, 8, 31))
    assert twr[i] / twr[i - 1] == pytest.approx(equity_move, rel=1e-3)


def test_benchmark_is_indexed_to_100_and_excess_is_reported(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100)
    perf = client.get(f"/api/holdings/portfolios/{pid}/performance").json()
    assert perf["benchmark"]["scheme_code"] == 1003
    assert perf["series"]["benchmark_index"][0] == pytest.approx(100.0)
    one_y = next(p for p in perf["periods"] if p["label"] == "1Y")
    assert one_y["excess_pct"] == pytest.approx(one_y["twr_pct"] - one_y["benchmark_pct"])


def test_attribution_sums_to_total_gain(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", amount=40000)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2023-03-01", amount=60000)
    perf = client.get(f"/api/holdings/portfolios/{pid}/performance").json()
    parts = perf["attribution"]["holdings"]
    assert sum(p["gain"] for p in parts) == pytest.approx(perf["attribution"]["total_gain"])
    assert perf["attribution"]["total_gain"] == pytest.approx(
        perf["series"]["value"][-1] - perf["series"]["invested"][-1], abs=0.05)
    assert sum(p["share_pct"] for p in parts) == pytest.approx(100.0)
    assert perf["monthly_flows"] == [{"month": "2023-03", "invested": 100000.0, "withdrawn": 0.0}]


def test_empty_portfolio_performance_is_explicitly_empty(client):
    pid = _pf(client)
    assert client.get(f"/api/holdings/portfolios/{pid}/performance").json() == {"empty": True}


# --- Allocation / targets / rebalance ------------------------------------------------------

def test_allocation_groups_and_concentration(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-02", amount=75000)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-02", amount=25000)
    a = client.get(f"/api/holdings/portfolios/{pid}/allocation").json()
    classes = {r["bucket"]: r["weight_pct"] for r in a["by_asset_class"]}
    assert set(classes) == {"Equity", "Cash & Liquid"}
    assert sum(classes.values()) == pytest.approx(100.0)
    w = [r["weight_pct"] / 100 for r in a["by_asset_class"]]
    assert a["concentration"]["hhi"] == pytest.approx(sum(x * x for x in w), rel=1e-6)
    assert a["concentration"]["effective_funds"] == pytest.approx(1 / a["concentration"]["hhi"])
    assert a["targets"] is None and a["drift"] is None


def test_targets_must_total_100_and_drive_drift(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-02", amount=80000)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-02", amount=20000)
    bad = client.put(f"/api/holdings/portfolios/{pid}/targets", json={"targets": {"Equity": 60, "Debt": 30}})
    assert bad.status_code == 422 and "100%" in bad.json()["detail"]["message"]
    unknown = client.put(f"/api/holdings/portfolios/{pid}/targets", json={"targets": {"Crypto": 100}})
    assert unknown.status_code == 422
    ok = client.put(f"/api/holdings/portfolios/{pid}/targets", json={"targets": {"Equity": 60, "Cash & Liquid": 40}})
    assert ok.status_code == 200
    a = client.get(f"/api/holdings/portfolios/{pid}/allocation").json()
    drift = {r["asset_class"]: r for r in a["drift"]}
    assert drift["Equity"]["status"] == "over" and drift["Cash & Liquid"]["status"] == "under"


def test_rebalance_uses_only_new_money_and_moves_toward_target(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-02", amount=80000)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-02", amount=20000)
    client.put(f"/api/holdings/portfolios/{pid}/targets", json={"targets": {"Equity": 60, "Cash & Liquid": 40}})
    r = client.get(f"/api/holdings/portfolios/{pid}/rebalance", params={"new_money": 10000}).json()
    rows = {row["asset_class"]: row for row in r["rows"]}
    assert sum(row["amount"] for row in r["rows"]) == pytest.approx(10000)
    assert rows["Cash & Liquid"]["amount"] == pytest.approx(10000)     # all of it goes to the under-weight class
    assert rows["Equity"]["amount"] == pytest.approx(0)
    assert rows["Cash & Liquid"]["suggested_scheme_code"] == 1002
    assert abs(rows["Cash & Liquid"]["after_pct"] - 40) < abs(rows["Cash & Liquid"]["before_pct"] - 40)
    # Enough money to overshoot every shortfall: the remainder splits by target weight.
    big = client.get(f"/api/holdings/portfolios/{pid}/rebalance", params={"new_money": 1_000_000}).json()
    after = {row["asset_class"]: row["after_pct"] for row in big["rows"]}
    assert after["Equity"] == pytest.approx(60, abs=0.01) and after["Cash & Liquid"] == pytest.approx(40, abs=0.01)


def test_household_view_has_no_targets_and_rebalance_is_per_portfolio(client):
    _pf(client, "Self")
    assert client.get("/api/holdings/portfolios/all/allocation").json()["targets"] is None
    assert client.get("/api/holdings/portfolios/all/rebalance", params={"new_money": 100}).status_code == 422


def test_backup_carries_targets(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-02", amount=1000)
    client.put(f"/api/holdings/portfolios/{pid}/targets", json={"targets": {"Equity": 100}})
    backup = client.get("/api/holdings/backup.json").json()
    assert backup["portfolio_targets"][0]["asset_class"] == "Equity"
    con = connection.get_connection()
    for t in ("portfolio_targets", "holding_transactions", "portfolios"):
        con.execute(f"DELETE FROM {t}")
    con.close()
    assert client.post("/api/holdings/restore", json=backup).status_code == 200
    assert client.get(f"/api/holdings/portfolios/{pid}/targets").json()["targets"] == {"Equity": 100.0}
