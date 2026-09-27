"""Tests for the Compare tab's server-side comparison (app/services/compare.py) and its
endpoint, GET /api/schemes/compare.

The rules under test are the ones that decide whether two funds' numbers can be put side by
side at all: one common period, valued "as of" its dates, with funds that cannot share it
named rather than silently shortening everyone's comparison.
"""

import datetime

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db import connection
from app.db import queries as db
from app.main import app
from app.services import compare as cmp


def _series(pairs):
    return pd.Series([float(v) for _, v in pairs], index=pd.DatetimeIndex([pd.Timestamp(d) for d, _ in pairs]))


def _business_days(start, n, value_fn):
    out, d = [], pd.Timestamp(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d, value_fn(len(out))))
        d += pd.Timedelta(days=1)
    return _series(out)


# --- own_window / common_period ----------------------------------------------------------

def test_window_starting_on_a_holiday_is_valued_at_the_previous_nav():
    """The value held on a day with no NAV is the last NAV before it. Starting at the next
    NAV instead drops the move across the holiday -- the old page did exactly that."""
    s = _series([("2026-06-25", 100.0), ("2026-06-29", 104.0), ("2026-07-01", 110.0)])
    w = cmp.own_window(s, pd.Timestamp("2026-06-26"), pd.Timestamp("2026-07-01"))
    assert w["anchored_before_start"] is True
    assert w["base_date"] == pd.Timestamp("2026-06-25") and w["base_nav"] == 100.0
    assert w["effective_start"] == pd.Timestamp("2026-06-26")
    assert w["return_pct"] == pytest.approx(10.0)


def test_fund_launched_inside_the_window_starts_at_its_first_nav():
    """No NAV within ANCHOR_MAX_GAP_DAYS before the window means the fund did not exist (or
    was not publishing); its period starts where its NAVs do."""
    s = _series([("2026-01-01", 50.0), ("2026-07-10", 10.0), ("2026-07-20", 11.0)])
    w = cmp.own_window(s, pd.Timestamp("2026-07-01"), pd.Timestamp("2026-07-31"))
    assert w["anchored_before_start"] is False
    assert w["effective_start"] == pd.Timestamp("2026-07-10")
    assert w["return_pct"] == pytest.approx(10.0)


def test_common_period_ends_at_the_slowest_publisher_and_starts_at_the_latest_starter():
    start, end = pd.Timestamp("2026-07-01"), pd.Timestamp("2026-09-24")
    lagging = _series([("2026-06-30", 100.0), ("2026-09-23", 101.0)])
    fresh = _series([("2026-06-30", 200.0), ("2026-09-23", 210.0), ("2026-09-24", 190.0)])
    late = _series([("2026-08-03", 10.0), ("2026-09-24", 11.0)])
    windows = {1: cmp.own_window(lagging, start, end), 2: cmp.own_window(fresh, start, end),
               3: cmp.own_window(late, start, end)}
    p = cmp.common_period(windows)
    assert p["status"] == {1: "ok", 2: "ok", 3: "ok"}
    assert p["start"] == pd.Timestamp("2026-08-03")
    assert p["end"] == pd.Timestamp("2026-09-23"), "fund 2's 24 Sep NAV has no counterpart for fund 1"


def test_stale_and_empty_funds_are_left_out_of_the_common_period_not_allowed_to_shrink_it():
    start, end = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-09-24")
    live = _series([("2025-12-31", 100.0), ("2026-09-24", 120.0)])
    wound_up = _series([("2025-12-31", 100.0), ("2026-03-31", 90.0)])
    windows = {1: cmp.own_window(live, start, end), 2: cmp.own_window(wound_up, start, end), 3: None}
    p = cmp.common_period(windows)
    assert p["status"] == {1: "ok", 2: "stale", 3: "no_data"}
    assert p["end"] == pd.Timestamp("2026-09-24")


def test_a_fund_younger_than_the_horizon_gets_no_trailing_return():
    """summary_table will call a 342-day since-launch return a "1Y return"; Compare won't."""
    assert cmp.full_horizon_only(-3.0486, "2025-10-17", "2026-09-24", 365) is None
    assert cmp.full_horizon_only(5.0, "2025-09-20", "2026-09-24", 365) == 5.0
    assert cmp.full_horizon_only(5.0, "2025-09-30", "2026-09-24", 365) == 5.0, "a holiday's tolerance"
    assert cmp.full_horizon_only(None, "2020-01-01", "2026-09-24", 365) is None


# --- risk -------------------------------------------------------------------------------

def test_volatility_is_annualised_on_the_funds_own_nav_frequency():
    """A fund that publishes every calendar day has ~365 returns a year; annualising those
    on sqrt(252) understates its volatility by ~17%."""
    rng = np.random.default_rng(7)
    rets = rng.normal(0.0003, 0.002, 400)
    daily = pd.Series(100.0 * np.cumprod(1 + rets), index=pd.date_range("2025-01-01", periods=400, freq="D"))
    obs = cmp.qa.infer_obs_per_year(daily.index)
    assert obs == pytest.approx(365.25, abs=1.0)
    risk = cmp.period_risk(daily, obs, 6.5)
    expected = float(daily.pct_change().dropna().std(ddof=1)) * np.sqrt(obs) * 100.0
    assert risk["vol_ann_pct"] == pytest.approx(expected, abs=1e-3)
    assert risk["obs_per_year"] == pytest.approx(365.2, abs=0.1)


def test_sharpe_is_undefined_not_zero_for_a_flat_nav_and_drawdown_is_measured_peak_to_trough():
    flat = pd.Series([100.0] * 10, index=pd.date_range("2026-01-01", periods=10, freq="D"))
    assert cmp.period_risk(flat, 365.0, 6.5)["sharpe"] is None
    s = _series([("2026-01-01", 100.0), ("2026-01-02", 120.0), ("2026-01-03", 90.0), ("2026-01-04", 95.0)])
    r = cmp.period_risk(s, 365.0, 6.5)
    assert r["max_drawdown_pct"] == pytest.approx(-25.0)
    assert (r["max_drawdown_peak_date"], r["max_drawdown_trough_date"]) == ("2026-01-02", "2026-01-03")


# --- TER drag ---------------------------------------------------------------------------

def test_ter_drag_accrues_daily_on_the_value_held():
    """Flat NAV, 1% TER, 365 days: the fund charged 1% of what was invested."""
    s = pd.Series([100.0] * 366, index=pd.date_range("2025-01-01", periods=366, freq="D"))
    out = cmp.ter_drag(s, None, 1.0)
    assert out["fee_pct_of_initial"] == pytest.approx(1.0, abs=1e-6)
    assert out["basis"] == "current" and out["avg_ter_pct"] == 1.0


def test_ter_drag_uses_the_disclosed_ter_where_history_exists_and_says_how_much_did():
    s = pd.Series([100.0] * 11, index=pd.date_range("2026-03-27", periods=11, freq="D"))
    runs = pd.DataFrame({"ter_date": [datetime.date(2026, 4, 1)], "valid_to": [datetime.date(2026, 4, 30)],
                         "total_ter_pct": [0.5]})
    out = cmp.ter_drag(s, runs, 1.0)
    # Intervals ending 28 Mar..31 Mar (4 days) take the current 1.00%; 1..6 Apr (6 days) the disclosed 0.50%.
    assert out["history_coverage_pct"] == pytest.approx(60.0)
    assert out["basis"] == "mixed"
    assert out["avg_ter_pct"] == pytest.approx((4 * 1.0 + 6 * 0.5) / 10, abs=1e-4)
    assert out["fee_pct_of_initial"] == pytest.approx((4 * 1.0 + 6 * 0.5) / 365, abs=1e-4)


def test_no_ter_drag_without_a_ter_to_apply():
    s = pd.Series([100.0, 101.0], index=pd.date_range("2026-01-01", periods=2, freq="D"))
    assert cmp.ter_drag(s, None, None) is None


# --- rolling / correlation ----------------------------------------------------------------

def test_rolling_one_year_return_uses_the_nav_one_year_earlier():
    s = pd.Series(np.linspace(100.0, 200.0, 731), index=pd.date_range("2024-01-01", periods=731, freq="D"))
    out = cmp.rolling_returns(s, pd.Timestamp("2025-06-01"), pd.Timestamp("2025-06-10"))
    assert out["n"] == 10
    t = pd.Timestamp("2025-06-01")
    expected = (s[t] / s[t - pd.Timedelta(days=365)] - 1) * 100
    assert out["points"][0] == ["2025-06-01", pytest.approx(round(expected, 4))]
    assert out["pct_positive"] == 100.0


def test_no_rolling_return_before_the_fund_is_a_year_old():
    s = pd.Series(np.linspace(100.0, 110.0, 100), index=pd.date_range("2026-01-01", periods=100, freq="D"))
    assert cmp.rolling_returns(s, s.index[0], s.index[-1])["n"] == 0


def test_correlation_aligns_each_pair_on_dates_both_funds_published():
    """A liquid fund's weekend NAVs must fold into its Monday return, not be dropped or
    compared against a missing equity weekend."""
    rng = np.random.default_rng(3)
    idx = pd.date_range("2026-01-01", periods=120, freq="D")
    base = 100 * np.cumprod(1 + rng.normal(0, 0.01, 120))
    daily = pd.Series(base, index=idx)
    weekdays = daily[daily.index.weekday < 5] * 2.0  # same path, sampled on weekdays only
    out = cmp.correlation_matrix({1: daily, 2: weekdays})
    assert out["matrix"][0][1] == pytest.approx(1.0, abs=1e-9)
    assert out["n_obs"][0][1] == len(weekdays) - 1


# --- endpoint ---------------------------------------------------------------------------

DATES = [datetime.date(2025, 1, 1) + datetime.timedelta(days=i) for i in range(500)]


@pytest.fixture()
def client(pg_db, monkeypatch):
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    with TestClient(app) as c:
        con = connection.get_connection()
        con.execute(
            "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, expense_ratio, ter_status) VALUES "
            "(111, 'Alpha Mid Cap Fund', 'Alpha AMC', 'Equity Scheme - Mid Cap Fund', 'Direct', 'Growth', 0.5, 'official'),"
            "(222, 'Beta Mid Cap Fund', 'Beta AMC', 'Equity Scheme - Mid Cap Fund', 'Direct', 'Growth', 0.7, 'official'),"
            "(333, 'Gamma Mid Cap Fund', 'Gamma AMC', 'Equity Scheme - Mid Cap Fund', 'Direct', 'IDCW', 0.9, 'official')"
        )
        rows = []
        for i, d in enumerate(DATES):
            if d.weekday() >= 5:
                continue
            rows.append((111, d, 100.0 + i * 0.30))
            rows.append((333, d, 100.0 + i * 0.10))
            # 222 publishes a day late: its last NAV is one business day behind the others.
            if d < DATES[-1]:
                rows.append((222, d, 100.0 + i * 0.20))
        con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
        con.close()
        db.refresh_summary_table()
        cmp._compare_cached.cache_clear()
        yield c


def test_compare_endpoint_measures_every_fund_over_one_period(client):
    end = DATES[-1]
    body = client.get(f"/api/schemes/compare?codes=111,222,333&start={end - datetime.timedelta(days=90)}&end={end}").json()
    common = body["common"]
    assert common["end"] < end.isoformat(), "the lagging fund sets the end"
    assert common["limited_end_by"] == [222]
    funds = {f["scheme_code"]: f for f in body["funds"]}
    ends = {f["common"]["end_date"] for f in funds.values()}
    assert ends == {common["end"]}
    assert funds[111]["display_name"] == "Alpha Mid Cap Fund (Direct - Growth) [111]"
    assert funds[111]["common"]["return_pct"] > funds[222]["common"]["return_pct"] > funds[333]["common"]["return_pct"]
    # Its own window still runs to its own last NAV -- shown beside, never mixed in.
    assert funds[111]["own_window"]["end_date"] == DATES[-1].isoformat()
    assert body["correlation"]["codes"] == [111, 222, 333]


def test_idcw_fund_is_flagged_and_not_ranked_against_growth_plans(client):
    body = client.get("/api/schemes/compare?codes=111,333").json()
    funds = {f["scheme_code"]: f for f in body["funds"]}
    assert funds[333]["is_idcw"] is True and funds[111]["is_idcw"] is False
    assert funds[333]["peer"]["rank_1y"] is None and funds[333]["peer"]["rank_note"]
    # Peers exclude IDCW: 111 and 222 only, 111 grows fastest.
    assert funds[111]["peer"]["n_1y"] == 2 and funds[111]["peer"]["rank_1y"] == 1


def test_compare_endpoint_rejects_more_than_eight_funds_and_names_unknown_codes(client):
    resp = client.get("/api/schemes/compare?codes=" + ",".join(str(c) for c in range(1, 10)))
    assert resp.status_code == 422
    body = client.get("/api/schemes/compare?codes=111,999999").json()
    unknown = [f for f in body["funds"] if f["scheme_code"] == 999999][0]
    assert unknown["status"] == "not_found"


def test_nav_history_route_is_not_shadowed_by_compare(client):
    assert client.get("/api/schemes/nav-history?codes=111").status_code == 200
    assert client.get("/api/schemes/111/profile").status_code == 200


def test_sharpe_and_sortino_agree_in_sign_when_nav_days_are_missing():
    """Quant Liquid returned 6.10% in a year against a 6.5% Rf, yet showed Sharpe +2.98 beside
    Sortino -4.78: its NAV history skips some weekends, and each 2-3 day return was charged a
    single day's Rf. Charged Rf over its own span, a fund that lagged Rf has a negative Sharpe."""
    import numpy as np
    import pandas as pd
    from app.services import compare

    days = pd.date_range("2025-09-25", "2026-09-24", freq="D")
    rng = np.random.default_rng(7)
    daily = (1.06) ** (1 / 365.25) - 1
    nav = pd.Series(100 * np.cumprod(1 + daily + rng.normal(0, 0.00005, len(days))), index=days)
    # Drop the weekend NAVs of every other week, as AMFI's file does for this fund.
    gappy = nav[~((nav.index.dayofweek >= 5) & ((nav.index.isocalendar().week % 2) == 0))]
    risk = compare.period_risk(gappy, 354.0, 6.5)
    assert risk["sortino"] < 0
    assert risk["sharpe"] < 0, "a 6% year does not beat a 6.5% risk-free rate"


# --- risk audit 2026-09-25: annualisation base, span-aware volatility, one Sharpe/Sortino
#     numerator, drawdown peak, correlation on the right clock --------------------------

def _accrual(days, rate=0.065, noise=0.000004, seed=1):
    """A liquid-fund NAV: a calendar-day accrual plus a little noise per NAV interval."""
    rng = np.random.default_rng(seed)
    gaps = np.diff(days).astype("timedelta64[D]").astype(float)
    step = (1 + rate) ** (gaps / 365.25) - 1 + rng.normal(0, noise, len(gaps))
    return pd.Series(100 * np.concatenate([[1.0], np.cumprod(1 + step)]), index=days)


def test_annualisation_base_is_the_cadence_around_the_period_not_an_old_one():
    """Quant Liquid published ~355 NAVs a year in 2015-17 and ~307 since; the whole-history
    90th percentile annualised its 2026 risk on 354 (+7.4% on volatility and Sharpe)."""
    old = pd.date_range("2015-01-01", "2017-12-31", freq="D")
    new = pd.date_range("2018-01-01", "2026-09-24", freq="D")
    new = new[~((new.dayofweek == 6) | ((new.dayofweek == 5) & (new.day % 2 == 0)))]  # skips Sundays + some Saturdays
    idx = old.append(new)
    per_year_now = len(new[new > "2025-09-24"])
    assert cmp.qa.infer_obs_per_year(idx) > 350
    got = cmp.qa.cadence_obs_per_year(idx, pd.Timestamp("2026-06-26"), pd.Timestamp("2026-09-24"))
    assert got == pytest.approx(per_year_now, abs=1.5)
    # A period longer than a year is annualised on exactly the returns it holds.
    three = cmp.qa.cadence_obs_per_year(idx, pd.Timestamp("2023-09-25"), pd.Timestamp("2026-09-24"))
    n_ret = ((idx > "2023-09-25") & (idx <= "2026-09-24")).sum()
    assert three == pytest.approx(n_ret / ((pd.Timestamp("2026-09-24") - pd.Timestamp("2023-09-25")).days / 365.25), rel=1e-9)


def test_volatility_of_an_accrual_fund_does_not_measure_how_often_it_skips_a_nav():
    """Zerodha Overnight (missing ~13 NAVs a year) read 4.3x SBI Overnight's volatility on a
    plain standard deviation. Same fund, NAVs dropped: the reading must stay put."""
    days = pd.date_range("2025-09-24", "2026-09-24", freq="D")
    full = _accrual(days)
    gappy = full[~((full.index.dayofweek >= 5) & (full.index.day % 3 == 0))]
    v_full = cmp.period_risk(full, cmp.qa.cadence_obs_per_year(full.index, full.index[0], full.index[-1]), 6.5)["vol_ann_pct"]
    v_gap = cmp.period_risk(gappy, cmp.qa.cadence_obs_per_year(gappy.index, gappy.index[0], gappy.index[-1]), 6.5)["vol_ann_pct"]
    raw_gap = float(gappy.pct_change().dropna().std()) * np.sqrt(350) * 100
    assert raw_gap > 2 * v_full, "the plain deviation is dominated by the multi-day returns"
    assert v_gap == pytest.approx(v_full, rel=0.25)


def test_sharpe_and_sortino_share_one_arithmetic_numerator():
    """Sortino used (CAGR - Rf): a compounded CAGR on a 90-day window read 24% above the
    arithmetic figure Sharpe uses (SBI Technology: 5.66 vs 4.56) and could differ from
    Sharpe in sign (ICICI Arbitrage: Sharpe +0.0052, Sortino -0.0071)."""
    s = _business_days("2026-06-26", 62, lambda i: 100 * 1.004 ** i * (1 + 0.01 * np.sin(i)))
    obs = 250.0
    risk = cmp.period_risk(s, obs, 6.5)
    r = s.pct_change().dropna().values
    g = np.asarray((s.index[1:] - s.index[:-1]).days, float)
    ex = r - ((1.065) ** (g / 365.25) - 1)
    mean_ann = ex.mean() * obs
    dd = np.sqrt((np.minimum(ex, 0) ** 2).sum() / len(ex)) * np.sqrt(obs)
    assert risk["sortino"] == pytest.approx(mean_ann / dd, abs=1e-4)
    assert risk["sharpe"] == pytest.approx(mean_ann / (risk["vol_ann_pct"] / 100), rel=1e-3)
    assert np.sign(risk["sharpe"]) == np.sign(risk["sortino"])


def test_drawdown_peak_is_the_last_day_at_the_high():
    s = _series([("2026-01-01", 100.0), ("2026-01-02", 99.0), ("2026-01-05", 100.0),
                 ("2026-01-06", 95.0), ("2026-01-07", 97.0)])
    r = cmp.period_risk(s, 250.0, 6.5)
    assert (r["max_drawdown_peak_date"], r["max_drawdown_trough_date"]) == ("2026-01-05", "2026-01-06")


def test_accrual_funds_are_not_correlated_by_their_shared_weekends():
    """Two independent accrual funds, one daily, one trading-day: on aligned dates both have
    3-day Monday returns, which made raw returns 'correlated' (PSU-bond index vs SBI
    Overnight: 0.80). Net of each return's expected accrual they are not."""
    days = pd.date_range("2026-03-01", "2026-06-15", freq="D")
    a = _accrual(days, seed=3)
    b = _accrual(days, rate=0.07, seed=4)
    b = b[b.index.dayofweek < 5]
    pair = pd.concat([a, b], axis=1, join="inner").pct_change().dropna()
    assert pair.corr().iloc[0, 1] > 0.8, "the artefact being fixed"
    out = cmp.correlation_matrix({1: a[a.index <= "2026-06-15"].iloc[:60], 2: b.iloc[:44]})
    assert out["basis"] == "daily"
    assert abs(out["matrix"][0][1]) < 0.35


def test_correlation_uses_weekly_returns_when_the_period_allows_it():
    """An overseas FoF's NAV for day t is struck on the US close of t-1. Two funds holding
    the same thing one day apart must read as correlated, not 0.16 (Motilal vs Navi
    Nasdaq 100 FoFs over three years)."""
    idx = pd.bdate_range("2025-01-01", "2026-06-30")
    rng = np.random.default_rng(9)
    path = 100 * np.cumprod(1 + rng.normal(0.0005, 0.012, len(idx) + 1))
    same = pd.Series(path[1:], index=idx)
    lagged = pd.Series(path[:-1], index=idx)  # yesterday's market on today's date
    daily_raw = pd.concat([same, lagged], axis=1).pct_change().dropna().corr().iloc[0, 1]
    out = cmp.correlation_matrix({1: same, 2: lagged})
    assert out["basis"] == "weekly"
    assert daily_raw < 0.2 and out["matrix"][0][1] > 0.6
    short = cmp.correlation_matrix({1: same.iloc[:60], 2: lagged.iloc[:60]})
    assert short["basis"] == "daily" and short["n_obs"][0][1] == 59
