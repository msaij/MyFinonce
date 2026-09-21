"""Tests for app/services/quant_service.py -- the Quantitative MF Analysis
page's composition layer (prepare_fund_timeseries + compute_risk_adjusted_metrics
+ get_synthetic_category_benchmark + compute_benchmark_relative_metrics +
compute_rolling_metrics + server-side Plotly figure construction).

Covers: the happy path returns all expected top-level keys and JSON-safe
figures; each of the 3 benchmark modes (including "no peers found" /
"no Nifty fund found" graceful degradation, not a crash); the
regression_points DataFrame never leaks into a route-level response (tested
indirectly here at the service level by confirming it's present so the
router has something to strip -- the router's own stripping is exercised via
FastAPI's TestClient in test_api_quant.py); Monte Carlo's happy path and its
insufficient-data error path.
"""

import datetime
import json
import math

import pytest

from app.db import connection
from app.db import queries as db
from app.services import quant_service


N_DAYS = 120
START = datetime.date(2025, 1, 1)
DATES = [START + datetime.timedelta(days=i) for i in range(N_DAYS)]


@pytest.fixture()
def db_con(pg_db):
    db.init_db()

    con = connection.get_connection()
    con.execute(
        "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, expense_ratio, ter_status) VALUES "
        "(111, 'Test Large Cap Fund - Direct Plan - Growth', 'AMC1', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth', 1.1, 'official'),"
        "(222, 'Peer Large Cap Fund - Direct Plan - Growth', 'AMC2', 'Equity Scheme - Large Cap Fund', 'Direct', 'Growth', 1.3, 'official'),"
        "(333, 'HDFC Nifty 50 Index Fund - Direct Plan - Growth', 'AMC3', 'Other Scheme - Index Fund', 'Direct', 'Growth', 0.2, 'official'),"
        "(444, 'Uncategorized Fund - Direct Plan - Growth', 'AMC4', NULL, 'Direct', 'Growth', 0.5, 'official')"
    )
    rows = []
    for i, d in enumerate(DATES):
        rows.append((111, d.isoformat(), round(100.0 * (1.0 + 0.0006 * i + 0.01 * math.sin(i / 5.0)), 4)))
        rows.append((222, d.isoformat(), round(50.0 * (1.0 + 0.0004 * i + 0.008 * math.sin(i / 7.0 + 1.0)), 4)))
        rows.append((333, d.isoformat(), round(200.0 * (1.0 + 0.0005 * i + 0.006 * math.sin(i / 6.0 + 2.0)), 4)))
        # 444 (a different category, no peers) gets a short, deliberately thin history --
        # exercises Monte Carlo's <10-return-days error path.
        if i < 8:
            rows.append((444, d.isoformat(), round(20.0 * (1.0 + 0.0002 * i), 4)))
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
    con.close()

    db.refresh_summary_table()
    yield


def _assert_json_safe(obj) -> None:
    """Round-trips through json.dumps with allow_nan=False -- catches any stray
    numpy scalar/array or NaN/Inf that sanitize_floats-equivalent handling missed
    (figures here are built via fig.to_plotly_json(), which is supposed to already
    guarantee this)."""
    json.dumps(obj, allow_nan=False)


class TestGetQuantAnalysis:
    def test_unknown_scheme_is_a_clean_error_not_a_crash(self, db_con):
        result = quant_service.get_quant_analysis(999999, DATES[0], DATES[-1])
        assert "error" in result

    def test_happy_path_category_benchmark_returns_all_expected_keys(self, db_con):
        result = quant_service.get_quant_analysis(
            111, DATES[10], DATES[-1], bench_mode="Category Benchmark (Synthesized Peer Average)"
        )
        assert "error" not in result
        assert result["profile"]["scheme_code"] == 111
        assert result["coverage"]["n_trading_days"] > 0

        metrics = result["metrics"]
        for key in ("sharpe_ratio", "sortino_ratio", "max_drawdown_pct", "var_95_daily_pct", "cvar_95_daily_pct", "skewness", "kurtosis"):
            assert key in metrics

        bench = result["benchmark"]
        assert bench["available"] is True
        assert bench["label"].startswith("Category Avg")
        assert "beta" in bench["metrics"]
        assert "regression_points" in bench["metrics"]  # present at service level; router strips it

        figs = result["figures"]
        assert figs["cumulative_return"] is not None
        assert figs["distribution"] is not None
        assert figs["drawdown"] is not None
        assert figs["capm_regression"] is not None
        assert figs["capture"] is not None
        # 120-day fixture minus the 10-day slice-start comfortably clears the 30-day rolling window.
        assert figs["rolling_volatility"] is not None
        assert figs["rolling_sharpe"] is not None

        _assert_json_safe(result["figures"])
        _assert_json_safe(metrics)

    def test_category_benchmark_with_no_category_degrades_gracefully(self, db_con):
        """Scheme 444 has category = NULL -- get_synthetic_category_benchmark
        short-circuits to an empty DataFrame for a falsy category (its own `if not
        category: return pd.DataFrame()` guard), and the service must report an
        unavailable_reason rather than crash on an empty benchmark frame. (NOT the
        same as "sole member of a real category" -- the ported query has no
        self-exclusion, so a fund that's the only member of its own category
        legitimately gets compared against itself; that's pre-existing upstream
        behavior, not something to test for here.)"""
        result = quant_service.get_quant_analysis(444, DATES[0], DATES[7], bench_mode="Category Benchmark (Synthesized Peer Average)")
        assert "error" not in result
        assert result["benchmark"]["available"] is False
        assert result["benchmark"]["unavailable_reason"]
        assert result["figures"]["capm_regression"] is None
        assert result["figures"]["capture"] is None
        _assert_json_safe(result["figures"])

    def test_nifty_50_proxy_mode_finds_the_index_fund(self, db_con):
        result = quant_service.get_quant_analysis(111, DATES[10], DATES[-1], bench_mode="Nifty 50 Index Fund Proxy")
        assert "error" not in result
        assert result["benchmark"]["available"] is True
        assert "Nifty 50" in result["benchmark"]["label"]

    def test_custom_peer_mode_uses_the_given_scheme(self, db_con):
        result = quant_service.get_quant_analysis(
            111, DATES[10], DATES[-1], bench_mode="Custom Peer Mutual Fund", custom_peer_code=222
        )
        assert "error" not in result
        assert result["benchmark"]["available"] is True
        assert result["benchmark"]["label"] == "Peer Large Cap Fund - Direct Plan - Growth"

    def test_gross_alpha_computed_when_ter_is_official(self, db_con):
        result = quant_service.get_quant_analysis(111, DATES[10], DATES[-1], bench_mode="Custom Peer Mutual Fund", custom_peer_code=222)
        alpha = result["benchmark"]["metrics"]["alpha_annualized_pct"]
        assert result["gross_alpha_pct"] == pytest.approx(alpha + 1.1)

    def test_empty_window_outside_nav_history_is_a_clean_error(self, db_con):
        result = quant_service.get_quant_analysis(111, datetime.date(2030, 1, 1), datetime.date(2030, 2, 1))
        assert "error" in result

    def test_window_too_short_for_risk_metrics_is_a_clean_error_not_a_missing_keys_crash(self, db_con):
        """compute_risk_adjusted_metrics returns {} below 3 valid daily-return rows --
        without the explicit guard in get_quant_analysis, this would reach the caller
        as {"metrics": {}, ...} (no "error" key), which the frontend would then crash
        on trying to read metrics.sharpe_ratio etc."""
        result = quant_service.get_quant_analysis(111, DATES[0], DATES[1])
        assert "error" in result


class TestGetMonteCarlo:
    def test_happy_path_returns_probabilities_and_a_json_safe_figure(self, db_con):
        result = quant_service.get_monte_carlo(111, DATES[10], DATES[-1])
        assert "error" not in result
        assert 0.0 <= result["prob_profit_pct"] <= 100.0
        assert result["median_terminal"] > 0
        assert result["figure"]["data"]
        _assert_json_safe(result["figure"])

    def test_seed_is_deterministic(self, db_con):
        r1 = quant_service.get_monte_carlo(111, DATES[10], DATES[-1], seed=7)
        r2 = quant_service.get_monte_carlo(111, DATES[10], DATES[-1], seed=7)
        assert r1["median_terminal"] == r2["median_terminal"]
        assert r1["prob_profit_pct"] == r2["prob_profit_pct"]

    def test_insufficient_return_history_is_a_clean_error_not_a_crash(self, db_con):
        # Scheme 444 has only 8 NAV rows (7 daily returns) -- below run_monte_carlo_simulation's
        # own <10-returns guard.
        result = quant_service.get_monte_carlo(444, DATES[0], DATES[7])
        assert "error" in result

    def test_unknown_scheme_is_a_clean_error(self, db_con):
        result = quant_service.get_monte_carlo(999999, DATES[0], DATES[-1])
        assert "error" in result
