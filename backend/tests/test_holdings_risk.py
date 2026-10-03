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
DAILY, YOUNG = 2004, 2005             # seeded per-test, not in the shared fixture


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


def _calendar_days(start: datetime.date, end: datetime.date) -> list:
    return [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]


def _seed_daily_liquid() -> None:
    """A real liquid fund: a NAV on every calendar day, weekends included."""
    days = _calendar_days(d(2021, 1, 1), d(2024, 12, 31))
    rets = np.random.default_rng(11).normal(0.00018, 0.00006, len(days))
    seed([(DAILY, "Cash Daily Liquid Fund", "AMC", "Debt Scheme - Liquid Fund", "Direct", "Growth")],
         {DAILY: list(zip(days, 100.0 * np.cumprod(1.0 + rets)))})


def _seed_young_fund() -> None:
    days = list(business_days(d(2023, 6, 1), d(2024, 12, 31)))
    rets = np.random.default_rng(13).normal(0.0004, 0.009, len(days))
    seed([(YOUNG, "Newborn Flexi Cap Fund", "AMC", "Equity Scheme - Flexi Cap Fund", "Direct", "Growth")],
         {YOUNG: list(zip(days, 100.0 * np.cumprod(1.0 + rets)))})


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


def test_twin_liquid_funds_correlate_as_twins_whatever_days_they_publish_and_however_amfi_labels_them(client):
    """The owner's case: Motilal Liquid skips days Axis and Mirae Liquid publish, and AMFI
    files it as "Debt Scheme - Liquid Fund" where they are "Income/Debt Oriented Schemes -
    Liquid Fund". Carrying its NAV forward over the gaps made zero-return days and lumps
    (a 0.50 correlation), and the raw labels kept the pair from ever being flagged."""
    _seed_daily_liquid()
    con = connection.get_connection()
    try:
        rows = con.execute("SELECT nav_date, nav FROM nav_history WHERE scheme_code = %s ORDER BY nav_date", (DAILY,)).fetchall()
    finally:
        con.close()
    # Same NAVs, but no Saturdays -- and AMFI's legacy label.
    seed([(2006, "Gamma Liquid Fund", "AMC", "Income/Debt Oriented Schemes - Liquid Fund", "Direct", "Growth")],
         {2006: [(day, float(nav)) for day, nav in rows if day.weekday() != 5]})
    pid = _portfolio(client, [(DAILY, "2024-06-03", 50000), (2006, "2024-06-03", 50000)])
    h = client.get(f"/api/holdings/portfolios/{pid}/risk").json()["holdings"]
    assert h["available"]
    assert np.array(h["correlation"])[0, 1] > 0.99
    assert [p["category"] for p in h["redundant_pairs"]] == ["Liquid Fund"]


def test_goal_projections_step_through_a_whole_month_of_a_calendar_day_series():
    """21 steps covered ~70% of a month for a liquid portfolio that ticks every day: a
    year's median growth came out at 4.7% against 6.9% realised."""
    from app.services import holdings_planning as hp
    rets = np.full(800, 0.00018) + np.random.default_rng(3).normal(0, 1e-6, 800)
    G, _ = hp._simulate(rets, 12, 500, 0.0, obs_per_year=365)
    assert np.median(G[:, -1]) == pytest.approx(np.exp(0.00018 * 365), rel=1e-3)


def test_young_portfolio_gets_risk_from_its_funds_history_labelled_as_such(client):
    """Bought 2 weeks before the data ends, in a fund with 4 years of NAVs: the
    statistics come from today's mix replayed over that history, and say so."""
    pid = _portfolio(client, [(FUND, "2024-12-16", 10000)])
    r = client.get(f"/api/holdings/portfolios/{pid}/risk").json()
    assert r["basis"] == "current_mix" and not r.get("insufficient")
    assert "hypothetical" in r["basis_note"] and r["window"]["n_trading_days"] > 500
    assert r["relative"]["beta"] == pytest.approx(0.8, abs=0.05)     # still recovers the known beta
    f = client.get(f"/api/holdings/portfolios/{pid}/factors").json()
    assert f["basis"] == "current_mix" and not f.get("unavailable")


def test_a_seasoned_portfolio_uses_its_own_track_record(client):
    pid = _portfolio(client, [(FUND, "2021-02-01", 100000)])
    r = client.get(f"/api/holdings/portfolios/{pid}/risk").json()
    assert r["basis"] == "realised" and r["basis_note"] is None


def test_no_history_anywhere_is_still_reported_not_computed(client):
    seed([(9101, "Brand New Fund", "AMC", "Equity Scheme - Flexi Cap Fund", "Direct", "Growth")],
         {9101: [(day, 10.0 + 0.01 * i) for i, day in enumerate(business_days(d(2024, 12, 2), d(2024, 12, 31)))]})
    pid = _portfolio(client, [(9101, "2024-12-16", 10000)])
    r = client.get(f"/api/holdings/portfolios/{pid}/risk").json()
    assert r["insufficient"] and "60 trading days" in r["message"]


def test_drawdown_signals_ignore_the_hypothetical_replay():
    from app.services import holdings_insights

    replay = {"basis": "current_mix", "metrics": {"current_drawdown_pct": -35.0}}
    actual = {"basis": "realised", "metrics": {"current_drawdown_pct": -35.0}}
    assert holdings_insights.realised_drawdown(replay) is None
    assert holdings_insights.realised_drawdown(actual) == -35.0


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
    # One step per observation of the calibration sample, so the fan is as long as a year
    # of THIS series -- not a fixed 252 (see test_monte_carlo_simulates_the_years_it_says).
    assert len(mc["p50"]) == mc["n_days"] + 1 and mc["p5"][-1] <= mc["p50"][-1] <= mc["p95"][-1]
    assert mc["initial_value"] == pytest.approx(summary_value) and mc["p50"][0] == pytest.approx(summary_value)
    assert 0.0 <= mc["prob_profit_pct"] <= 100.0


# --- Annualisation follows the series' sampling frequency -------------------------------

def test_a_calendar_day_fund_annualises_on_365_not_252(client):
    """Liquid funds publish a NAV every calendar day, so their return series carries ~365
    observations a year. Annualising those on 252 understated volatility by 17% and pushed
    the owner's Sharpe to -14."""
    _seed_daily_liquid()
    pid = _portfolio(client, [(DAILY, "2021-02-01", 100000)])
    r = client.get(f"/api/holdings/portfolios/{pid}/risk").json()
    assert r["window"]["obs_per_year"] == pytest.approx(365.25, abs=1.0)
    m = r["metrics"]
    assert m["vol_annualized_pct"] == pytest.approx(m["vol_daily_pct"] * np.sqrt(365.25), rel=1e-3)
    assert m["sharpe_ratio"] > 0                       # a fund that only ever gains cannot score negative


def test_a_trading_day_portfolio_still_annualises_near_252(client):
    pid = _portfolio(client, [(FUND, "2021-02-01", 100000)])
    r = client.get(f"/api/holdings/portfolios/{pid}/risk").json()
    ppy = r["window"]["obs_per_year"]
    assert abs(ppy - 252.0) / 252.0 < 0.05
    m = r["metrics"]
    assert m["vol_annualized_pct"] == pytest.approx(m["vol_daily_pct"] * np.sqrt(ppy), rel=1e-3)


def test_risk_contributions_are_unchanged_by_the_annualisation_base(client):
    """Percentage risk contributions are scale-invariant: the covariance scaling cancels.
    The reported portfolio volatility does move -- to the truth. The funds are compared on
    the dates BOTH published (a weekday equity fund and a calendar-day liquid fund share
    weekdays only), so the base is weekdays a year, not the liquid fund's 365: the 365 came
    from carrying the equity fund's Friday NAV through every weekend."""
    _seed_daily_liquid()
    pid = _portfolio(client, [(FUND, "2021-02-01", 50000), (DAILY, "2021-02-01", 50000)])
    h = client.get(f"/api/holdings/portfolios/{pid}/risk").json()["holdings"]
    assert h["available"] and h["obs_per_year"] == pytest.approx(261.0, abs=2.0)
    assert sum(x["risk_pct"] for x in h["risk_contributions"]) == pytest.approx(100.0, abs=0.01)


def test_monte_carlo_simulates_the_years_it_says(client):
    """The step count and the chart's years-from-today divisor must be the same number.
    On a calendar-day series, 5 x 252 steps is 3.45 years, not 5."""
    _seed_daily_liquid()
    pid = _portfolio(client, [(DAILY, "2021-02-01", 100000)])
    mc = client.get(f"/api/holdings/portfolios/{pid}/monte-carlo", params={"years": 5}).json()
    assert mc["obs_per_year"] == pytest.approx(365.25, abs=1.0)
    assert mc["n_days"] / mc["obs_per_year"] == pytest.approx(5.0, abs=0.01)
    assert mc["n_days"] > 1700                         # was a flat 1260
    assert len(mc["p50"]) == mc["n_days"] + 1


def test_the_charts_carry_the_coverage_the_stress_cards_already_show(client):
    """The drawdown and volatility lines come from the same replay as the stress cards.
    Dates before a fund existed represent only part of today's money, and the charts have
    to say so rather than read as history."""
    _seed_young_fund()
    pid = _portfolio(client, [(FUND, "2024-12-16", 40000), (YOUNG, "2024-12-16", 60000)])
    r = client.get(f"/api/holdings/portfolios/{pid}/risk").json()
    assert r["basis"] == "current_mix"
    cov = r["drawdown"]["coverage_pct"]
    assert cov is not None and len(cov) == len(r["drawdown"]["dates"])
    # Before YOUNG existed only FUND is represented. Coverage is a share of today's money
    # by *current value*, not by what was paid in, so the 40/60 contribution split has
    # drifted with the seeded NAVs by the time the backcast is taken -- the figure is near
    # 40%, not exactly it, and pinning it exactly would be pinning the fixture's NAVs.
    early = min(c for c in cov if c is not None)
    assert 35.0 < early < 45.0, f"only FUND existed then, so coverage should be ~40%, got {early}"
    assert cov[-1] == pytest.approx(100.0)


def test_your_own_track_record_needs_no_coverage_caveat(client):
    """On the realised basis the series IS the portfolio you held, so it is 100% covered
    by construction and a coverage line would be noise."""
    pid = _portfolio(client, [(FUND, "2021-02-01", 100000)])
    r = client.get(f"/api/holdings/portfolios/{pid}/risk").json()
    assert r["basis"] == "realised" and r["drawdown"]["coverage_pct"] is None


# --- SEBI riskometer ----------------------------------------------------------------------

LEVELS = ["Low", "Low to Moderate", "Moderate", "Moderately High", "High", "Very High"]


def _set_riskometer(code, level):
    con = connection.get_connection()
    try:
        con.execute("UPDATE schemes SET riskometer = %s, riskometer_as_of = %s WHERE scheme_code = %s",
                    (level, d(2024, 12, 31), code))
    finally:
        con.close()


def test_the_riskometer_places_the_money_on_sebis_scale(client):
    """A money-weighted position on SEBI's ordinal scale, with the highest level held
    reported separately so the average cannot hide the riskiest fund."""
    _set_riskometer(FUND, "Very High")
    _set_riskometer(LIQUID, "Low")
    pid = _portfolio(client, [(FUND, "2023-03-01", 75000), (LIQUID, "2023-03-01", 25000)])
    meter = client.get(f"/api/holdings/portfolios/{pid}/risk").json()["riskometer"]

    assert meter["available"] is True and meter["coverage_pct"] == pytest.approx(100.0)
    dist = {x["level"]: x["weight_pct"] for x in meter["distribution"]}
    assert sum(dist.values()) == pytest.approx(100.0)
    assert dist["Very High"] > 50 and dist["Low"] > 0 and dist["Moderate"] == 0
    assert meter["highest"]["level"] == "Very High"
    expected = (dist["Very High"] * 6 + dist["Low"] * 1) / 100.0
    assert meter["weighted_rank"] == pytest.approx(expected)
    assert meter["portfolio_level"] == LEVELS[round(expected) - 1]


def test_a_fund_without_a_published_riskometer_is_reported_not_guessed(client):
    _set_riskometer(FUND, "Moderate")
    pid = _portfolio(client, [(FUND, "2023-03-01", 50000), (LIQUID, "2023-03-01", 50000)])
    meter = client.get(f"/api/holdings/portfolios/{pid}/risk").json()["riskometer"]

    assert meter["coverage_pct"] < 100.0
    unknown = next(f for f in meter["funds"] if f["scheme_code"] == LIQUID)
    assert unknown["level"] is None and unknown["rank"] is None
    assert meter["portfolio_level"] == "Moderate", "averaged over the funds that have a label only"


def test_the_monte_carlo_horizon_can_be_any_number_of_days_from_a_month_to_five_years(client):
    """The horizon is typed or slid to in calendar days, and becomes simulation steps at the
    portfolio's own observation rate -- so the last step always lands on the chosen day."""
    pid = _portfolio(client, [(FUND, "2021-02-01", 100000)])
    mc = client.get(f"/api/holdings/portfolios/{pid}/monte-carlo", params={"days": 45}).json()
    assert mc["horizon_days"] == 45
    assert mc["days"][-1] == max(21, round(45 / 365.25 * mc["obs_per_year"]))

    assert client.get(f"/api/holdings/portfolios/{pid}/monte-carlo", params={"days": 1826}).status_code == 200
    assert client.get(f"/api/holdings/portfolios/{pid}/monte-carlo", params={"days": 29}).status_code == 422
    assert client.get(f"/api/holdings/portfolios/{pid}/monte-carlo", params={"days": 1827}).status_code == 422, "five years is the cap"


def test_the_riskometer_needs_no_price_history(client):
    """Available even while the statistics are still waiting for days of data."""
    _set_riskometer(FUND, "High")
    pid = _portfolio(client, [(FUND, "2024-12-30", 10000)])
    body = client.get(f"/api/holdings/portfolios/{pid}/risk").json()
    assert body["riskometer"]["available"] is True and body["riskometer"]["portfolio_level"] == "High"
