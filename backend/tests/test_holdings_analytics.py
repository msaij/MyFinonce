"""Holdings Phase 2: time-weighted index, periods, attribution, allocation."""

import datetime
import functools
import math

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.db import connection
from app.main import app
from app.services import holdings_analytics as ha
from app.services import holdings_service as svc
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
    ("Hybrid Scheme - Arbitrage Fund", "Hybrid", "Motilal Oswal Arbitrage Fund", "Cash & Liquid"),
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


# --- Recent-change tiles -------------------------------------------------------------

def test_recent_change_tiles_measure_nav_days_not_calendar_days(client):
    """"The last 2 days" over a weekend would otherwise be two days in which nothing
    could have changed. The window opens N NAV rows back, so it spans N daily moves."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100)
    body = client.get(f"/api/holdings/portfolios/{pid}/recent-changes").json()

    assert [w["days"] for w in body["windows"]] == [2, 7, 10]
    two = next(w for w in body["windows"] if w["days"] == 2)
    # With one fund the window return is that fund's own NAV move across the last two
    # NAV dates -- taken from the seeded series rather than assumed.
    days = list(business_days(d(2023, 1, 2), d(2024, 12, 31)))
    expected = (nav_on(1001, days[-1]) / nav_on(1001, days[-3]) - 1) * 100
    assert two["start"] == days[-3].isoformat()
    assert two["change_pct"] == pytest.approx(expected, rel=1e-4)
    assert two["avg_daily_pct"] == pytest.approx((1 + expected / 100) ** 0.5 * 100 - 100, rel=1e-4)


def test_money_paid_in_during_the_window_is_not_reported_as_a_gain(client):
    """The whole reason these are time-weighted: a fresh purchase raises the portfolio's
    value without the holdings having moved, and must not show up as a return."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-02", units=10)
    quiet = client.get(f"/api/holdings/portfolios/{pid}/recent-changes").json()
    before = next(w for w in quiet["windows"] if w["days"] == 7)["change_pct"]

    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-27", units=500)
    after = next(w for w in client.get(f"/api/holdings/portfolios/{pid}/recent-changes").json()["windows"]
                 if w["days"] == 7)["change_pct"]
    assert after == pytest.approx(before, rel=1e-6)


def test_a_window_longer_than_the_history_says_so_instead_of_guessing(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-27", units=10)
    windows = {w["days"]: w for w in client.get(f"/api/holdings/portfolios/{pid}/recent-changes").json()["windows"]}

    assert windows[10]["available"] is False and windows[10]["change_pct"] is None
    assert windows[10]["nav_days_held"] < 10
    # A countdown the tile can show instead of a bare "not yet": N windows need N+1 rows.
    assert windows[10]["days_needed"] == 11 - (windows[10]["nav_days_held"] + 1)
    assert windows[2]["days_needed"] == 0


def test_each_window_carries_the_same_window_of_the_benchmark(client):
    """So a tile can say whether a move was the funds or just the market they sit in."""
    pid = _pf(client)      # explicit benchmark 1003
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100)
    two = next(w for w in client.get(f"/api/holdings/portfolios/{pid}/recent-changes").json()["windows"] if w["days"] == 2)

    days = list(business_days(d(2023, 1, 2), d(2024, 12, 31)))
    bench = (nav_on(BENCHMARK, days[-1]) / nav_on(BENCHMARK, days[-3]) - 1) * 100
    assert two["benchmark_change_pct"] == pytest.approx(bench, rel=1e-4)
    assert two["excess_pp"] == pytest.approx(two["change_pct"] - bench, abs=1e-6)


def test_the_kpis_carry_a_since_start_return_beside_the_benchmark(client):
    """The headline a young portfolio can have before XIRR is allowed to show, on exactly
    the basis of the Performance tab's "Since start" row so the two never disagree."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", amount=100000)
    k = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]
    since = next(p for p in client.get(f"/api/holdings/portfolios/{pid}/performance").json()["periods"]
                 if p["label"] == "Since start")

    assert k["benchmark_since_start_pct"] is not None
    assert k["excess_since_start_pp"] == pytest.approx(k["twr_since_start_pct"] - k["benchmark_since_start_pct"], abs=1e-9)
    # The Performance row annualises beyond a year; the tile is the plain cumulative figure.
    assert since["annualised"] is True and k["twr_since_start_pct"] > since["twr_pct"]
    assert k["total_gain_pct"] == pytest.approx(k["total_gain"] / k["net_contributed"] * 100.0)
    assert k["first_investment_date"] == "2023-03-01" and k["xirr_available_on"] is None
    # One purchase: the money-weighted average is simply the time since it.
    assert k["days_since_first_investment"] > 600
    assert k["avg_days_invested"] == pytest.approx(k["days_since_first_investment"])


def test_average_days_invested_is_shorter_than_the_span_when_money_came_in_later(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", amount=100000)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-09-02", amount=300000)
    k = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]
    span = k["days_since_first_investment"]
    later = span - (d(2024, 9, 2) - d(2023, 3, 1)).days
    assert k["avg_days_invested"] == pytest.approx((100000 * span + 300000 * later) / 400000, rel=1e-3)
    assert k["avg_days_invested"] < span / 2


def test_a_withheld_xirr_says_when_it_will_appear(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-20", amount=10000)
    k = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]
    assert k["xirr_pct"] is None and k["xirr_note"] == "too_short"
    assert k["xirr_available_on"] == "2025-01-19", "30 calendar days after the first cash flow"


def test_xirr_waits_for_the_money_not_the_first_rupee(client):
    """A token opening purchase, then almost all of the money weeks later. Counting 30 days
    from the first rupee would annualise ~11 days of the money's life."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-10-01", amount=1500)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-20", amount=1000000)
    k = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]

    assert k["days_since_first_investment"] > 30 and k["avg_days_invested"] < 12
    assert k["xirr_pct"] is None and k["xirr_note"] == "too_short"
    wait = math.ceil(30 - k["avg_days_invested"])
    assert k["xirr_available_on"] == (d(2024, 12, 31) + datetime.timedelta(days=wait)).isoformat()


def _seed_extra(code, name, last_day):
    days = list(business_days(d(2023, 1, 2), last_day))
    seed([(code, name, "Delta MF", "Equity Scheme - Flexi Cap Fund", "Direct", "Growth")],
         {code: [(day, 50.0 * 1.0003 ** i) for i, day in enumerate(days)]})


def test_a_fund_still_to_publish_counts_its_own_latest_move_in_the_day_tile(client):
    """A day AMFI had priced only some of the funds held. Counting the rest as unchanged
    misstated the day; stopping everything at the last date all funds shared turned a
    falling day into a rising one, because it dropped a fund's already-published fall. The
    tile is the table: each fund's own latest move."""
    _seed_extra(1004, "Late Publisher Fund - Direct Plan - Growth", d(2024, 12, 30))
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-06-03", amount=100000)
    _add(client, portfolio_id=pid, scheme_code=1004, txn_type="BUY", trade_date="2024-06-03", amount=100000)

    body = client.get(f"/api/holdings/portfolios/{pid}/summary").json()
    units = {p["scheme_code"]: p["units"] for p in body["positions"]}
    own_latest_moves = (units[1001] * (nav_on(1001, d(2024, 12, 31)) - nav_on(1001, d(2024, 12, 30)))
                        + units[1004] * (nav_on(1004, d(2024, 12, 30)) - nav_on(1004, d(2024, 12, 27))))
    k = body["kpis"]
    assert k["day_change"] == pytest.approx(own_latest_moves, abs=0.01)
    assert k["day_change"] == pytest.approx(sum(p["day_change"] for p in body["positions"]), abs=1e-6)
    assert k["oldest_nav_date"] == "2024-12-30"

    # The windows run to the newest NAV and say which fund has yet to publish it.
    rc = client.get(f"/api/holdings/portfolios/{pid}/recent-changes").json()
    assert rc["as_of"] == "2024-12-31" and rc["latest_nav_date"] == "2024-12-31"
    assert rc["awaiting_funds"] == 1 and 40 < rc["awaiting_value_pct"] < 60


def test_peers_skip_the_days_a_fund_has_not_published_yet(monkeypatch):
    """The portfolio carries a pending fund unchanged until AMFI prices it; crediting its
    peers that day would put the portfolio behind for a move it simply has not seen yet."""
    idx = pd.to_datetime(["2024-12-27", "2024-12-30", "2024-12-31"])
    cats = pd.DataFrame({"A": [0.0, 0.01, 0.01], "B": [0.0, 0.02, 0.02]}, index=idx)
    monkeypatch.setattr(ha, "category_daily_returns", lambda *a, **k: cats)
    weights = pd.DataFrame({1: [1.0, 1.0, 1.0], 2: [1.0, 1.0, 1.0]}, index=idx)
    level = ha.blend_index(weights, {1: "A", 2: "B"}, idx, last_priced={1: idx[2], 2: idx[1]})
    # 30th: both funds priced -> half of each category; 31st: fund 2 pending -> only fund 1's half.
    assert level.iloc[1] / level.iloc[0] - 1 == pytest.approx(0.5 * 0.01 + 0.5 * 0.02)
    assert level.iloc[2] / level.iloc[1] - 1 == pytest.approx(0.5 * 0.01)


def test_a_fund_silent_for_over_a_week_does_not_hold_the_date_back(client):
    _seed_extra(1005, "Wound Up Fund - Direct Plan - Growth", d(2024, 12, 16))
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-06-03", amount=100000)
    _add(client, portfolio_id=pid, scheme_code=1005, txn_type="BUY", trade_date="2024-06-03", amount=100000)
    body = client.get(f"/api/holdings/portfolios/{pid}/recent-changes").json()
    assert body["as_of"] == "2024-12-31" and body["awaiting_funds"] == 0


def test_holding_the_benchmark_itself_is_level_with_peers_in_rupees(client):
    """The rupee comparison buys the benchmark with the same cash on the same dates, stamp
    duty included -- so a portfolio that IS the benchmark is exactly level with it, however
    uneven its purchases (the case the time-weighted pp figure gets wrong in spirit)."""
    pid = _pf(client)      # explicit benchmark 1003
    add_ok(client, portfolio_id=pid, scheme_code=BENCHMARK, txn_type="BUY", trade_date="2023-03-01", amount=1500)
    add_ok(client, portfolio_id=pid, scheme_code=BENCHMARK, txn_type="BUY", trade_date="2024-11-04", amount=5000000)
    k = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]
    assert k["stamp_duty"] > 0
    # Paise of AMC unit rounding (3-decimal units) are all that may separate them.
    assert k["gain_vs_peers"] == pytest.approx(0.0, abs=0.05)


def test_a_fund_that_is_its_own_category_is_level_with_peers_on_every_figure(client):
    """1002 is the only liquid fund here, so its category average IS the fund. Bought
    unevenly, at the previous day's NAV (as liquid funds are), with stamp duty, it must be
    level with its peers on every tile -- since start in pp and rupees, and in a window
    that contains a purchase. Before the peers took new money like-for-like, each purchase
    day flattered the portfolio by the day's accrual and charged it stamp duty the peers
    never paid; and the rupee peers grew new money at the mix already held."""
    pid = new_portfolio(client)            # no benchmark: the category blend
    for trade, prev, amount in ((d(2024, 6, 4), d(2024, 6, 3), 100000),
                                (d(2024, 11, 5), d(2024, 11, 4), 500000),
                                (d(2024, 12, 27), d(2024, 12, 26), 300000)):
        add_ok(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date=trade.isoformat(),
               amount=amount, nav=nav_on(1002, prev))
    k = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]
    assert k["stamp_duty"] > 0
    # AMC-style 3-decimal units on a ~Rs 1,000 NAV leave up to Rs 0.50 per purchase.
    assert k["excess_since_start_pp"] == pytest.approx(0.0, abs=1e-3)
    assert k["gain_vs_peers"] == pytest.approx(0.0, abs=1.5)
    seven = next(w for w in client.get(f"/api/holdings/portfolios/{pid}/recent-changes").json()["windows"] if w["days"] == 7)
    assert seven["start"] < "2024-12-27"      # the window contains the 27 Dec purchase
    assert seven["change_pct"] == pytest.approx(seven["benchmark_change_pct"], abs=1e-3)


def test_the_day_column_follows_the_allotment_nav_not_the_trade_date(client):
    """A liquid fund bought on the latest date is allotted at the previous day's NAV and
    has earned the day; an order allotted at the latest NAV (placed before it) has not."""
    pid = _pf(client)
    last, prev = d(2024, 12, 31), d(2024, 12, 30)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date=last.isoformat(), units=100, nav=nav_on(1002, prev))
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-06-03", units=50)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date=prev.isoformat(), units=70, nav=nav_on(1001, last))
    body = client.get(f"/api/holdings/portfolios/{pid}/summary").json()
    day = {p["scheme_code"]: p["day_change"] for p in body["positions"]}
    assert day[1002] == pytest.approx(100 * (nav_on(1002, last) - nav_on(1002, prev)), abs=0.01)
    assert day[1001] == pytest.approx(50 * (nav_on(1001, last) - nav_on(1001, prev)), abs=0.01)
    assert body["kpis"]["day_change"] == pytest.approx(day[1001] + day[1002], abs=1e-6)


def test_the_blend_names_one_category_once_whatever_label_amfi_filed_it_under():
    """AMFI files liquid funds under two labels; the legend printed "Liquid Fund 77% ·
    Liquid Fund 13%" for one 90% sleeve."""
    parts = ha.blend_components({1: 77.0, 2: 13.0, 3: 10.0}, {
        1: "Income/Debt Oriented Schemes - Liquid Fund",
        2: "Debt Scheme - Liquid Fund",
        3: "Hybrid Scheme - Arbitrage Fund"})
    assert parts == [{"category": "Liquid Fund", "weight_pct": 90.0}, {"category": "Arbitrage Fund", "weight_pct": 10.0}]


def test_a_liquid_purchase_is_matched_to_the_previous_days_nav():
    """Liquid funds allot at the previous day's NAV; the peers must buy on that date too."""
    raw = pd.Series([3163.0315, 3163.2604], index=pd.to_datetime(["2026-09-14", "2026-09-15"]))
    assert ha._allotment_date(raw, d(2026, 9, 15), "3163.0315") == pd.Timestamp("2026-09-14")
    # An app that printed the NAV a tenth of a paisa off still matches.
    assert ha._allotment_date(raw, d(2026, 9, 15), 3163.0316) == pd.Timestamp("2026-09-14")
    assert ha._allotment_date(raw, d(2026, 9, 15), "3163.2604") == pd.Timestamp("2026-09-15")
    assert ha._allotment_date(raw, d(2026, 9, 15), "3170.0") == pd.Timestamp("2026-09-15")
    assert ha._allotment_date(raw, d(2026, 9, 15), None) == pd.Timestamp("2026-09-15")


def test_an_empty_portfolio_reports_no_windows(client):
    pid = _pf(client)
    assert client.get(f"/api/holdings/portfolios/{pid}/recent-changes").json()["empty"] is True


def test_a_flow_free_window_is_exactly_the_change_in_value(client):
    """Regression anchor for the sub-period split. On a day with no cash flow the split
    must change nothing: the pre-flow book is the whole book, the second sub-period's
    return is zero, and the day collapses to V_t / V_{t-1} - 1, which telescopes to
    V_end / V_start over the window. Portfolio 1's independently verified trailing
    figures (2d +0.04498261142422688, 5d +0.07783823316471761, 7d +0.14084264059792684)
    were measured before this change and rest on exactly that."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2023-03-01", units=10)
    days = list(business_days(d(2023, 1, 2), d(2024, 12, 31)))
    windows = {w["days"]: w for w in client.get(f"/api/holdings/portfolios/{pid}/recent-changes").json()["windows"]}

    def value_on(day):
        return 100 * nav_on(1001, day) + 10 * nav_on(1002, day)

    for n in ha.RECENT_WINDOWS:
        v_end, v_start = value_on(days[-1]), value_on(days[-(n + 1)])
        assert windows[n]["change_pct"] == pytest.approx((v_end / v_start - 1) * 100, rel=1e-9)
        assert windows[n]["gain"] == pytest.approx(v_end - v_start, abs=0.01)


def test_a_large_deposit_into_a_tiny_portfolio_does_not_swamp_the_window(client):
    """A token Rs ~1,500 on the books, then a deposit hundreds of times larger in one day.

    The purchase does not add its full rupee amount to the book -- stamp duty comes out
    of it and units round to 3 decimals -- and charging that shortfall to the Rs 1,500
    opening base read as -1.35% for the day, then dragged the whole 10-day window red
    while its rupee figure stayed green. Splitting the day at the flow charges it to the
    post-flow book, where it is a rounding error."""
    pid = _pf(client)
    add_ok(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-16", amount=1500)
    add_ok(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-18", amount=1_000_000)
    w = next(x for x in client.get(f"/api/holdings/portfolios/{pid}/recent-changes").json()["windows"] if x["days"] == 10)

    assert w["available"] is True
    # The headline and the rupee figure must at least agree on their sign.
    assert w["gain"] > 0 and w["change_pct"] > 0
    # 1002 only crawls upwards, so a time-weighted window over it is that fund's own NAV
    # move, deposit or no deposit. The old one-line form gave about -3.2% here.
    days = list(business_days(d(2023, 1, 2), d(2024, 12, 31)))
    fund_move = (nav_on(1002, days[-1]) / nav_on(1002, days[-11]) - 1) * 100
    assert w["change_pct"] == pytest.approx(fund_move, abs=0.02)


# --- Earned-in-month tile ----------------------------------------------------------------

def test_each_month_runs_from_the_last_nav_of_the_month_before(client):
    """The newest NAV's year, month by month. A month opens at the previous month's last
    NAV, so a flow-free month is exactly the change in value across it."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2023-03-01", units=10)
    body = client.get(f"/api/holdings/portfolios/{pid}/monthly-earnings").json()
    days = list(business_days(d(2023, 1, 2), d(2024, 12, 31)))

    def value_on(day):
        return 100 * nav_on(1001, day) + 10 * nav_on(1002, day)

    assert body["year"] == 2024 and body["as_of"] == "2024-12-31"
    assert [m["month"] for m in body["months"]] == [f"2024-{m:02d}" for m in range(1, 13)]
    for m in body["months"]:
        in_month = [x for x in days if x.year == 2024 and x.month == int(m["month"][5:])]
        opening = max(x for x in days if x < in_month[0])
        assert (m["start"], m["end"], m["nav_days"]) == (opening.isoformat(), in_month[-1].isoformat(), len(in_month))
        v_start, v_end = value_on(opening), value_on(in_month[-1])
        assert m["gain"] == pytest.approx(v_end - v_start, abs=0.01)
        assert m["change_pct"] == pytest.approx((v_end / v_start - 1) * 100, rel=1e-9)

    dec = body["months"][-1]
    bench = (nav_on(BENCHMARK, d(2024, 12, 31)) / nav_on(BENCHMARK, d(2024, 11, 29)) - 1) * 100
    assert dec["benchmark_change_pct"] == pytest.approx(bench, rel=1e-4)
    assert dec["excess_pp"] == pytest.approx(dec["change_pct"] - bench, abs=1e-6)


def test_money_paid_in_during_a_month_is_not_earnings(client):
    """Units bought at the month's last NAV have not moved yet: the month earned nothing
    more, however much went in."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-03-01", units=100)
    before = client.get(f"/api/holdings/portfolios/{pid}/monthly-earnings").json()["months"][-1]
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-31", units=5000)
    after = client.get(f"/api/holdings/portfolios/{pid}/monthly-earnings").json()["months"][-1]
    assert after["gain"] == pytest.approx(before["gain"], abs=0.01)
    assert after["change_pct"] == pytest.approx(before["change_pct"], rel=1e-6)


def test_the_first_month_opens_from_nothing_and_carries_the_stamp_duty(client):
    """No month before the first investment is offered. The first one opens from an empty
    book, so the purchase's stamp duty lands in it -- as it does in Total gain."""
    pid = _pf(client)
    buy = add_ok(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-06-04", amount=100000)
    row = buy["transactions"][0]
    assert float(row["stamp_duty"]) > 0
    months = client.get(f"/api/holdings/portfolios/{pid}/monthly-earnings").json()["months"]

    assert months[0]["month"] == "2024-06" and months[0]["start"] is None
    june_end = float(row["units"]) * nav_on(1002, d(2024, 6, 28))
    assert months[0]["gain"] == pytest.approx(june_end - 100000, abs=0.01)
    assert months[0]["change_pct"] == pytest.approx((june_end / 100000 - 1) * 100, abs=1e-3)


def test_every_months_earnings_add_up_to_total_gain(client):
    """Money in or out is never earnings, so the months since the first investment add up
    to the Total gain tile -- stamp duty, a sale, a switch and a dividend paid out."""
    pid = _pf(client)
    for code, kind, day, amount in ((1001, "BUY", "2024-02-05", 50000), (1002, "BUY", "2024-02-20", 200000),
                                    (1001, "BUY", "2024-07-15", 25000), (1002, "REDEEM", "2024-09-10", 80000),
                                    (1002, "DIVIDEND_PAYOUT", "2024-11-05", 750), (1002, "BUY", "2024-12-16", 40000)):
        add_ok(client, portfolio_id=pid, scheme_code=code, txn_type=kind, trade_date=day, amount=amount)
    # Money moving between your own funds: no rupee of it is earnings either way.
    add_ok(client, portfolio_id=pid, scheme_code=1001, txn_type="SWITCH", trade_date="2024-10-08", units=1000,
           switch_to_scheme_code=1002)
    months = client.get(f"/api/holdings/portfolios/{pid}/monthly-earnings").json()["months"]
    k = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]

    assert months[0]["month"] == "2024-02" and len(months) == 11
    assert k["stamp_duty"] > 0
    assert sum(m["gain"] for m in months) == pytest.approx(k["total_gain"], abs=0.05)


def test_an_empty_portfolio_has_no_months(client):
    pid = _pf(client)
    assert client.get(f"/api/holdings/portfolios/{pid}/monthly-earnings").json() == {"empty": True, "months": []}


# --- The 1-day KPI tile ----------------------------------------------------------------

def test_the_kpi_day_change_is_the_tables_column_total(client):
    """One engine for the day: the tile is the sum of the per-fund 1D column, and its
    percentage is on what those units were worth before the move. With every fund priced
    on the same day it is also exactly the last day of the value series."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2023-03-01", units=10)
    body = client.get(f"/api/holdings/portfolios/{pid}/summary").json()
    k, positions = body["kpis"], body["positions"]
    series = client.get(f"/api/holdings/portfolios/{pid}/performance").json()["series"]

    assert k["day_change"] == pytest.approx(sum(p["day_change"] for p in positions), abs=1e-6)
    last_day = (series["value"][-1] - series["value"][-2]) - (series["invested"][-1] - series["invested"][-2])
    assert k["day_change"] == pytest.approx(last_day, abs=0.02)
    base = sum(p["day_base"] for p in positions)
    assert k["day_change_pct"] == pytest.approx(k["day_change"] / base * 100.0)


def test_units_bought_today_are_not_credited_with_todays_nav_move(client):
    """The tile multiplied today's unit balance by today's NAV move with no date filter,
    so a purchase allotted this morning collected a full day's gain it was never held
    for -- hundreds of rupees on a single large purchase."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-02", units=100)
    before = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]["day_change"]

    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-31", units=100_000)
    body = client.get(f"/api/holdings/portfolios/{pid}/summary").json()
    assert body["kpis"]["day_change"] == pytest.approx(before, abs=0.02)
    # The per-fund column follows the same rule: only units held at the previous close
    # earned the move, so today's 100,000 units add nothing and the column adds up to the tile.
    assert body["positions"][0]["day_change"] == pytest.approx(before, abs=0.02)


def test_the_per_fund_day_column_adds_up_to_the_tile_across_buys_and_sells_today(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-11-01", units=300)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-11-01", units=40)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="REDEEM", trade_date="2024-12-31", units=120)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-31", units=900)
    body = client.get(f"/api/holdings/portfolios/{pid}/summary").json()

    prev, last = d(2024, 12, 30), d(2024, 12, 31)
    # Units redeemed today were still held through today's move; units bought today were not.
    assert body["positions"] and {p["scheme_code"]: p["day_change"] for p in body["positions"]} == pytest.approx({
        1001: 300 * (nav_on(1001, last) - nav_on(1001, prev)),
        1002: 40 * (nav_on(1002, last) - nav_on(1002, prev)),
    }, abs=0.01)
    assert sum(p["day_change"] for p in body["positions"]) == pytest.approx(body["kpis"]["day_change"], abs=0.02)


def test_each_position_says_where_its_one_day_move_starts(client):
    """A Monday NAV's move starts on Friday: three days, which the 1D column now says."""
    _seed_extra(1004, "Late Publisher Fund - Direct Plan - Growth", d(2024, 12, 30))   # a Monday
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-06-03", units=10)
    _add(client, portfolio_id=pid, scheme_code=1004, txn_type="BUY", trade_date="2024-06-03", units=10)
    prev = {p["scheme_code"]: (p["prev_nav_date"], p["latest_date"]) for p in client.get(f"/api/holdings/portfolios/{pid}/summary").json()["positions"]}
    assert prev[1001] == ("2024-12-30", "2024-12-31")
    assert prev[1004] == ("2024-12-27", "2024-12-30")


def test_earning_now_is_the_last_30_days_annualised_for_cash_and_debt_funds_only(client):
    """What the money earns at today's rates: the NAV is already net of fees, so its last
    30 days of growth, annualised, is the rate. An equity fund's month scaled to a year is
    noise, so it gets none and is left out of the portfolio rate."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-06-03", units=100)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-06-03", units=100)
    body = client.get(f"/api/holdings/portfolios/{pid}/summary").json()
    pos = {p["scheme_code"]: p for p in body["positions"]}

    # 31 Dec 2024 minus 30 days is Sunday 1 Dec: the window starts at Friday 29 Nov's NAV.
    start, end = d(2024, 11, 29), d(2024, 12, 31)
    rate = ((nav_on(1002, end) / nav_on(1002, start)) ** (365 / (end - start).days) - 1) * 100
    liquid = pos[1002]
    assert liquid["earning_now_from"] == "2024-11-29"
    assert liquid["earning_now_pct"] == pytest.approx(rate, rel=1e-9)
    # A day's rupees are the fund's average day on today's value -- not rate / 365, which
    # is larger because the yearly rate compounds.
    daily = (nav_on(1002, end) / nav_on(1002, start)) ** (1 / (end - start).days) - 1
    assert liquid["earning_now_per_day"] == pytest.approx(liquid["current_value"] * daily, rel=1e-9)
    assert liquid["earning_now_per_day"] < liquid["current_value"] * rate / 100 / 365
    assert pos[1001]["earning_now_pct"] is None and pos[1001]["earning_now_per_day"] is None

    k = body["kpis"]
    assert k["earning_now_pct"] == pytest.approx(rate, rel=1e-9)          # the equity fund is not averaged in
    assert k["earning_now_per_day"] == pytest.approx(liquid["earning_now_per_day"])
    assert k["earning_now_coverage_pct"] == pytest.approx(liquid["weight_pct"])


def test_a_fund_earning_less_than_another_in_its_category_is_flagged(client):
    seed([(2004, "Slow Liquid Fund - Direct Plan - Growth", "Slow MF", "Income/Debt Oriented Schemes - Liquid Fund", "Direct", "Growth")],
         {2004: [(day, 1000.0 + 0.10 * i) for i, day in enumerate(business_days(d(2023, 1, 2), d(2024, 12, 31)))]})
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-06-03", units=100)
    _add(client, portfolio_id=pid, scheme_code=2004, txn_type="BUY", trade_date="2024-06-03", units=100)
    pos = {p["scheme_code"]: p for p in client.get(f"/api/holdings/portfolios/{pid}/summary").json()["positions"]}

    fast, slow = pos[1002], pos[2004]
    assert fast["earning_now_behind"] is None
    # Same SEBI category although AMFI files them under different labels.
    gap = fast["earning_now_pct"] - slow["earning_now_pct"]
    assert gap > 0.25
    assert slow["earning_now_behind"]["than"] == fast["display_name"]
    assert slow["earning_now_behind"]["pp"] == pytest.approx(gap)
    assert slow["earning_now_behind"]["rupees_per_year"] == pytest.approx(slow["current_value"] * gap / 100)


def test_the_earning_now_tile_compounds_the_mixs_own_day(client):
    """The tile's rate is the mix's average day compounded to a year, so it and its rupees a
    day describe the same money; averaging the funds' yearly rates would overstate it."""
    seed([(2004, "Slow Liquid Fund - Direct Plan - Growth", "Slow MF", "Income/Debt Oriented Schemes - Liquid Fund", "Direct", "Growth")],
         {2004: [(day, 1000.0 + 0.05 * i) for i, day in enumerate(business_days(d(2023, 1, 2), d(2024, 12, 31)))]})
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-06-03", units=100)
    _add(client, portfolio_id=pid, scheme_code=2004, txn_type="BUY", trade_date="2024-06-03", units=300)
    body = client.get(f"/api/holdings/portfolios/{pid}/summary").json()
    k, pos = body["kpis"], body["positions"]

    per_day = sum(p["earning_now_per_day"] for p in pos)
    value = sum(p["current_value"] for p in pos)
    assert k["earning_now_per_day"] == pytest.approx(per_day)
    assert k["earning_now_pct"] == pytest.approx(((1 + per_day / value) ** 365 - 1) * 100, rel=1e-9)
    averaged = sum(p["current_value"] * p["earning_now_pct"] for p in pos) / value
    assert k["earning_now_pct"] < averaged


def test_earning_now_ignores_what_you_paid_so_a_loss_does_not_change_it(client):
    """The rate is the fund's NAV growth alone. A holding under water -- bought dearer than
    today's NAV -- earns at the same rate as one in profit, and a falling NAV reads as a
    negative rate rather than breaking."""
    seed([(2005, "Falling Debt Fund - Direct Plan - Growth", "Fall MF", "Debt Scheme - Short Duration Fund", "Direct", "Growth")],
         {2005: [(day, 1500.0 - 0.4 * i) for i, day in enumerate(business_days(d(2023, 1, 2), d(2024, 12, 31)))]})
    early, late = _pf(client), new_portfolio(client, name="Late")
    _add(client, portfolio_id=early, scheme_code=2005, txn_type="BUY", trade_date="2023-03-01", units=100)
    _add(client, portfolio_id=late, scheme_code=2005, txn_type="BUY", trade_date="2024-12-02", units=100)
    under_water = client.get(f"/api/holdings/portfolios/{early}/summary").json()["positions"][0]
    recent = client.get(f"/api/holdings/portfolios/{late}/summary").json()["positions"][0]

    assert under_water["unrealised_gain"] < recent["unrealised_gain"] < 0
    start, end = d(2024, 11, 29), d(2024, 12, 31)
    rate = ((nav_on(2005, end) / nav_on(2005, start)) ** (365 / (end - start).days) - 1) * 100
    assert rate < 0
    assert under_water["earning_now_pct"] == pytest.approx(rate, rel=1e-9)
    assert recent["earning_now_pct"] == pytest.approx(rate, rel=1e-9)
    assert under_water["earning_now_per_day"] < 0


def test_a_portfolio_with_one_nav_day_reports_no_day_change(client):
    """Bought today: there is no previous close to have moved from, and inventing one
    from the fund's own NAV history would credit a day the units were not held."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-31", units=100)
    k = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]
    assert k["day_change"] == 0.0 and k["day_change_pct"] is None


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


def test_default_benchmark_is_each_funds_category_not_an_equity_index(client):
    """No benchmark chosen: each holding is compared with its own SEBI category.
    1001 is the only Flexi Cap fund here, so its category average IS the fund --
    a lone holding must then track its benchmark exactly."""
    pid = new_portfolio(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100)
    perf = client.get(f"/api/holdings/portfolios/{pid}/performance").json()
    b = perf["benchmark"]
    assert b["kind"] == "category_blend" and b["scheme_code"] is None
    assert b["components"] == [{"category": "Flexi Cap Fund", "weight_pct": 100.0}]
    twr, bench = perf["series"]["twr_index"], perf["series"]["benchmark_index"]
    assert bench[0] == pytest.approx(100.0) and bench[-1] == pytest.approx(twr[-1], rel=1e-3)


def test_category_blend_weights_follow_the_portfolio(client):
    pid = new_portfolio(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-02", amount=75000)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-02", amount=25000)
    comps = {svc.sebi_category(c["category"]): c["weight_pct"]
             for c in client.get(f"/api/holdings/portfolios/{pid}/performance").json()["benchmark"]["components"]}
    alloc = {r["bucket"]: r["weight_pct"] for r in client.get(f"/api/holdings/portfolios/{pid}/allocation").json()["by_category"]}
    assert comps == pytest.approx(alloc, abs=0.01)


def test_an_explicit_benchmark_still_wins(client):
    pid = _pf(client)      # benchmark set to 1003
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2023-03-01", amount=1000)
    b = client.get(f"/api/holdings/portfolios/{pid}/performance").json()["benchmark"]
    assert b["kind"] == "scheme" and b["scheme_code"] == BENCHMARK


def test_attribution_sums_to_total_gain(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", amount=40000)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2023-03-01", amount=60000)
    perf = client.get(f"/api/holdings/portfolios/{pid}/performance").json()
    parts = perf["attribution"]["holdings"]
    assert sum(p["gain"] for p in parts) == pytest.approx(perf["attribution"]["total_gain"])
    assert perf["attribution"]["total_gain"] == pytest.approx(
        perf["series"]["value"][-1] - perf["series"]["invested"][-1], abs=0.05)
    # Both seeded funds rose, so the shares of the gain are plain shares of the total.
    assert sum(p["gain_share_pct"] for p in parts) == pytest.approx(100.0)
    assert sum(p["weight_pct"] for p in parts) == pytest.approx(100.0)
    assert "monthly_flows" not in perf, "replaced by the share-of-money vs share-of-gain view"


def test_gain_shares_stay_bounded_when_a_fund_loses_money():
    """A share of the NET would be 150% / 100% / -150% here, and run away as the net nears
    zero. Against gross gains and gross losses the winners sum to 100% and the losers to
    -100%, whatever the net does."""
    shares, gross_gain, gross_loss = ha.gain_shares([600.0, 400.0, -500.0])
    assert shares == pytest.approx([60.0, 40.0, -100.0])
    assert (gross_gain, gross_loss) == (1000.0, -500.0)

    near_zero_net, _, _ = ha.gain_shares([1000.0, -999.0])
    assert near_zero_net == pytest.approx([100.0, -100.0]), "no blow-up as the net approaches zero"


def test_gain_shares_with_nothing_down_are_the_plain_share_of_the_total():
    shares, _, gross_loss = ha.gain_shares([750.0, 250.0, 0.0])
    assert shares == pytest.approx([75.0, 25.0, 0.0]) and gross_loss == 0.0


def test_empty_portfolio_performance_is_explicitly_empty(client):
    pid = _pf(client)
    assert client.get(f"/api/holdings/portfolios/{pid}/performance").json() == {"empty": True}


# --- Allocation ------------------------------------------------------

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
    assert "targets" not in a and "drift" not in a, "targets and drift were removed"
    assert a["basis"] == "current_value"
    assert a["concentration"]["amc_count"] == 2
    assert a["concentration"]["top_amc"] == "Alpha MF" and a["concentration"]["top_amc_pct"] > 70
    assert a["by_portfolio"] is None, "one portfolio: nothing to split"


@pytest.mark.parametrize("raw,expected", [
    ("Income/Debt Oriented Schemes - Liquid Fund", "Liquid Fund"),
    ("Debt Scheme - Liquid Fund", "Liquid Fund"),
    ("Liquid", "Liquid Fund"),
    ("Income/Debt Oriented Schemes - Banking and PSU Debt Fund", "Banking and PSU Fund"),
    ("Debt Scheme - Banking and PSU Fund", "Banking and PSU Fund"),
    ("Equity Scheme - Flexi Cap Fund", "Flexi Cap Fund"),
    ("Other Scheme - FoF Overseas", "FoF Overseas"),
    ("Fund of Funds - Overseas", "FoF Overseas"),
    # Redefined by SEBI's 2017 recategorisation: kept, never guessed onto a modern name.
    ("Income/Debt Oriented Schemes - Short Term Fund", "Short Term Fund"),
    ("Income", "Income"),
    (None, None),
    ("  ", None),
])
def test_sebi_category_folds_amfis_two_labels_for_one_category(raw, expected):
    assert svc.sebi_category(raw) == expected


def test_legacy_and_modern_labels_for_one_category_are_one_bucket(client):
    """The owner's liquid money read as 59.6% + 25.0% in two buckets, because AMFI files
    Mirae Asset Liquid under its pre-2018 header and Motilal Oswal Liquid under the new one."""
    days = list(business_days(d(2024, 11, 1), d(2024, 12, 31)))
    seed([(1004, "Delta Liquid Fund - Direct Plan - Growth", "Delta MF", "Income/Debt Oriented Schemes - Liquid Fund", "Direct", "Growth")],
         {1004: [(day, 2000.0 + 0.3 * i) for i, day in enumerate(days)]})
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-02", amount=60000)
    _add(client, portfolio_id=pid, scheme_code=1004, txn_type="BUY", trade_date="2024-12-02", amount=40000)
    a = client.get(f"/api/holdings/portfolios/{pid}/allocation").json()
    assert [(r["bucket"], round(r["weight_pct"])) for r in a["by_category"]] == [("Liquid Fund", 100)]
    assert {h["category"] for h in a["holdings"]} == {"Liquid Fund"}
    assert {r["bucket"] for r in a["by_asset_class"]} == {"Cash & Liquid"}


def test_household_allocation_is_traced_back_to_each_portfolio(client):
    self_pid, spouse_pid = _pf(client, "Self"), _pf(client, "Spouse")
    _add(client, portfolio_id=self_pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-02", amount=75000)
    _add(client, portfolio_id=spouse_pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-02", amount=25000)
    a = client.get("/api/holdings/portfolios/all/allocation").json()
    rows = {r["bucket"]: r for r in a["by_portfolio"]}
    assert set(rows) == {"Self", "Spouse"}
    assert sum(r["weight_pct"] for r in rows.values()) == pytest.approx(100.0)
    assert sum(r["value"] for r in rows.values()) == pytest.approx(a["total_value"], abs=0.01)
    assert rows["Self"]["by_asset_class"] == pytest.approx({"Equity": 100.0})
    assert rows["Spouse"]["by_asset_class"] == pytest.approx({"Cash & Liquid": 100.0})


def test_positions_carry_the_yearly_fee_and_the_kpis_its_weighted_ter(client):
    seed([(1005, "Epsilon Flexi Cap Fund - Direct Plan - Growth", "Epsilon MF", "Equity Scheme - Flexi Cap Fund", "Direct", "Growth", 0.75, "official")],
         {1005: [(day, 50.0) for day in business_days(d(2024, 11, 1), d(2024, 12, 31))]})
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1005, txn_type="BUY", trade_date="2024-12-02", units=2000)   # Rs 1,00,000
    _add(client, portfolio_id=pid, scheme_code=1002, txn_type="BUY", trade_date="2024-12-02", amount=100000)  # no TER seeded
    body = client.get(f"/api/holdings/portfolios/{pid}/summary").json()
    pos = {p["scheme_code"]: p for p in body["positions"]}
    assert pos[1005]["annual_fee"] == pytest.approx(750.0)
    assert pos[1002]["annual_fee"] is None
    k = body["kpis"]
    # Weighted over the money that HAS a TER, with the coverage stated beside it.
    assert k["weighted_ter_pct"] == pytest.approx(0.75)
    assert k["annual_fee"] == pytest.approx(750.0)
    assert k["ter_coverage_pct"] == pytest.approx(pos[1005]["current_value"] / k["current_value"] * 100)
    assert pos[1005]["sebi_category"] == "Flexi Cap Fund" and pos[1005]["asset_class"] == "Equity"


def test_a_withheld_position_xirr_says_when_it_will_show(client):
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-20", units=10)
    p = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["positions"][0]
    assert p["xirr_note"] == "too_short" and p["xirr_available_on"] == "2025-01-19"


def test_backup_carries_targets(client):
    """Nothing sets targets any more, but the table stays and a backup round-trips its rows."""
    pid = _pf(client)
    _add(client, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2024-12-02", amount=1000)
    con = connection.get_connection()
    con.execute("INSERT INTO portfolio_targets (portfolio_id, asset_class, target_pct) VALUES (%s, 'Equity', 100)", (pid,))
    con.close()
    backup = client.get("/api/holdings/backup.json").json()
    assert backup["portfolio_targets"][0]["asset_class"] == "Equity"
    con = connection.get_connection()
    for t in ("portfolio_targets", "holding_transactions", "portfolios"):
        con.execute(f"DELETE FROM {t}")
    con.close()
    assert client.post("/api/holdings/restore", json=backup).status_code == 200
    con = connection.get_connection()
    rows = con.execute("SELECT portfolio_id, asset_class, target_pct FROM portfolio_targets").fetchall()
    con.close()
    assert [(r[0], r[1], float(r[2])) for r in rows] == [(pid, "Equity", 100.0)]
