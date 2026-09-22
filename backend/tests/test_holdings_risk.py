"""Holdings Phase 3: risk, factors, stress and Monte Carlo on a synthetic market
with KNOWN structure, so the tests check the engines recover it -- not just
that they return numbers."""

import datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import factor_model
from app.db import connection
from app.main import app
from tests.holdings_support import add_ok, business_days, new_portfolio, seed

d = datetime.date
MARKET, MOMENTUM = 118482, 150452     # factor_model's default proxy codes
FUND, TWIN, LIQUID = 2001, 2002, 2003


@pytest.fixture()
def client(pg_db):
    rng = np.random.default_rng(7)
    days = list(business_days(d(2021, 1, 4), d(2024, 12, 31)))
    n = len(days)
    mkt = rng.normal(0.0005, 0.010, n)
    size = rng.normal(0.0, 0.004, n)
    val = rng.normal(0.0, 0.003, n)
    series = {
        MARKET: mkt,
        MOMENTUM: mkt + rng.normal(0.0002, 0.003, n),
        3001: mkt + rng.normal(0, 0.001, n),                       # large cap
        3002: mkt + size + rng.normal(0, 0.001, n),                # mid cap
        3003: mkt + size + rng.normal(0, 0.001, n),                # small cap
        3004: mkt + val + rng.normal(0, 0.001, n),                 # value
        3005: mkt + val + rng.normal(0, 0.001, n),                 # contra
        FUND: 0.8 * mkt + rng.normal(0.0001, 0.002, n),            # beta 0.8 to the market
        LIQUID: np.full(n, 0.00025),
    }
    series[TWIN] = series[FUND] + rng.normal(0, 0.0001, n)         # a near-clone of FUND
    cats = {
        MARKET: ("Nifty 50 Index Fund", "Other Scheme - Index Funds"),
        MOMENTUM: ("Nifty 200 Momentum 30 Index Fund", "Other Scheme - Index Funds"),
        3001: ("Large One", "Equity Scheme - Large Cap Fund"),
        3002: ("Mid One", "Equity Scheme - Mid Cap Fund"),
        3003: ("Small One", "Equity Scheme - Small Cap Fund"),
        3004: ("Value One", "Equity Scheme - Value Fund"),
        3005: ("Contra One", "Equity Scheme - Contra Fund"),
        FUND: ("Alpha Flexi Cap Fund", "Equity Scheme - Flexi Cap Fund"),
        TWIN: ("Alpha Flexi Cap Clone Fund", "Equity Scheme - Flexi Cap Fund"),
        LIQUID: ("Beta Liquid Fund", "Debt Scheme - Liquid Fund"),
    }
    seed(
        [(c, name, "AMC", cat, "Direct", "Growth") for c, (name, cat) in cats.items()],
        {code: list(zip(days, 100.0 * np.cumprod(1.0 + r))) for code, r in series.items()},
    )
    factor_model._cached_base_factor_df = None      # process-wide 1h cache; don't leak across DBs
    yield TestClient(app)
    factor_model._cached_base_factor_df = None


def _portfolio(c, buys):
    pid = new_portfolio(c, benchmark=MARKET)
    for code, date, amount in buys:
        add_ok(c, portfolio_id=pid, scheme_code=code, txn_type="BUY", trade_date=date, amount=amount, apply_stamp_duty=False)
    return pid

def test_realised_risk_recovers_the_known_beta(client):
    pid = _portfolio(client, [(FUND, "2021-02-01", 100000)])
    r = client.get(f"/api/holdings/portfolios/{pid}/risk").json()
    assert not r.get("insufficient")
    assert r["relative"]["beta"] == pytest.approx(0.8, abs=0.05)
    m = r["metrics"]
    assert m["max_drawdown_pct"] <= 0 and m["vol_annualized_pct"] > 0
    assert len(r["drawdown"]["dates"]) == len(r["drawdown"]["drawdown_pct"]) == r["window"]["n_trading_days"]


def test_risk_contributions_sum_to_100_and_clones_are_flagged(client):
    pid = _portfolio(client, [(FUND, "2021-02-01", 50000), (TWIN, "2021-02-01", 30000), (LIQUID, "2021-02-01", 20000)])
    h = client.get(f"/api/holdings/portfolios/{pid}/risk").json()["holdings"]
    assert h["available"]
    assert sum(x["risk_pct"] for x in h["risk_contributions"]) == pytest.approx(100.0, abs=0.01)
    assert sum(x["weight_pct"] for x in h["risk_contributions"]) == pytest.approx(100.0, abs=0.01)
    liquid = next(x for x in h["risk_contributions"] if x["scheme_code"] == LIQUID)
    assert liquid["risk_pct"] < liquid["weight_pct"]              # cash-like: carries less risk than money
    corr = np.array(h["correlation"])
    assert np.allclose(corr, corr.T) and np.allclose(np.diag(corr), 1.0)
    assert [(p["a"], p["b"]) for p in h["redundant_pairs"]] == [(FUND, TWIN)]
    assert h["effective_bets"] < h["effective_funds"]             # two clones are one bet


def test_short_history_is_reported_not_computed(client):
    pid = _portfolio(client, [(FUND, "2024-12-16", 10000)])
    r = client.get(f"/api/holdings/portfolios/{pid}/risk").json()
    assert r["insufficient"] and "60 trading days" in r["message"]


def test_factor_regression_on_the_portfolio(client):
    pid = _portfolio(client, [(FUND, "2021-02-01", 100000)])
    f = client.get(f"/api/holdings/portfolios/{pid}/factors").json()
    assert not f.get("unavailable"), f
    betas = f["regression"]["factor_betas"]
    market_beta = next(v for k, v in betas.items() if "m" in k.lower() and "mom" not in k.lower())
    assert market_beta == pytest.approx(0.8, abs=0.08)
    assert f["figures"]


def test_factors_degrade_gracefully_without_proxies(client):
    con = connection.get_connection()
    con.execute("DELETE FROM nav_history WHERE scheme_code IN (118482, 3001, 3002, 3003, 3004, 3005)")
    con.close()
    factor_model._cached_base_factor_df = None
    pid = _portfolio(client, [(FUND, "2021-02-01", 100000)])
    f = client.get(f"/api/holdings/portfolios/{pid}/factors").json()
    assert f["unavailable"] and f["reason"]


def test_stress_is_hypothetical_in_rupees_with_coverage(client):
    pid = _portfolio(client, [(FUND, "2021-02-01", 70000), (LIQUID, "2021-02-01", 30000)])
    s = client.get(f"/api/holdings/portfolios/{pid}/stress").json()
    assert s["hypothetical"] is True
    by_id = {x["id"]: x for x in s["scenarios"]}
    assert by_id["covid_march_2020"]["available"] is False            # our data starts 2021
    ok = by_id["rate_hike_2022"]
    assert ok["available"] and ok["coverage_pct"] == pytest.approx(100.0)
    assert ok["rupee_impact"] == pytest.approx(s["current_value"] * ok["drawdown_pct"] / 100.0)
    p = s["parametric"]
    assert p["available"]
    # The default -15% market shock at a market beta of ~0.56 (0.8 x ~70% equity) must
    # actually bite: a silent 0% would mean the beta keys stopped matching the shock keys.
    assert -12.0 < p["total_return_impact_pct"] < -5.0
    assert p["rupee_impact"] == pytest.approx(s["current_value"] * p["total_return_impact_pct"] / 100.0, abs=0.01 + s["current_value"] * 1e-6)


def test_monte_carlo_fan_starts_at_todays_value(client):
    pid = _portfolio(client, [(FUND, "2021-02-01", 100000)])
    summary_value = client.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]["current_value"]
    mc = client.get(f"/api/holdings/portfolios/{pid}/monte-carlo", params={"years": 1}).json()
    assert len(mc["p50"]) == 253 and mc["p5"][-1] <= mc["p50"][-1] <= mc["p95"][-1]
    assert mc["initial_value"] == pytest.approx(summary_value) and mc["p50"][0] == pytest.approx(summary_value)
    assert 0.0 <= mc["prob_profit_pct"] <= 100.0
