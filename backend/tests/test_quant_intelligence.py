"""Unit tests for the Automated Institutional Quantitative Intelligence engine."""

from app.services.quant_intelligence import generate_quantitative_intelligence


def test_empty_metrics_returns_empty_dict():
    assert generate_quantitative_intelligence({}, {}, {}) == {}


def test_happy_path_full_intelligence_generation():
    metrics = {
        "cagr_pct": 18.5,
        "vol_annualized_pct": 12.2,
        "sharpe_ratio": 1.15,
        "sortino_ratio": 1.75,
        "calmar_ratio": 1.45,
        "max_drawdown_pct": -12.8,
        "var_95_daily_pct": -1.1,
        "cvar_95_ann_pct": -18.5,
        "skewness": 0.25,
        "kurtosis": 0.8,
        "win_rate_pct": 56.4,
    }
    benchmark = {
        "label": "Category Benchmark",
        "available": True,
        "metrics": {
            "beta": 0.92,
            "alpha_annualized_pct": 3.4,
            "r_squared": 0.94,
            "tracking_error_pct": 2.8,
            "information_ratio": 1.21,
            "up_market_capture_pct": 105.0,
            "down_market_capture_pct": 82.0,
            "capture_ratio": 1.28,
            "benchmark_cagr_pct": 14.5,
        },
    }
    profile = {
        "scheme_name": "Axis Bluechip Fund - Direct Plan - Growth",
        "plan_type": "Direct",
        "expense_ratio": 0.55,
    }

    intel = generate_quantitative_intelligence(metrics, benchmark, profile, gross_alpha_pct=3.95)

    assert intel is not None
    assert "executive_verdict" in intel
    assert intel["quadrant"]["title"] == "Institutional Alpha Star"
    assert intel["quadrant"]["badge"] == "High Return, Low Risk"
    assert "Return Drivers & Factor Attribution" in intel["return_drivers"]["title"]
    assert "High Benchmark Discipline" in intel["style_drift"]["assessment"]
    assert "Expense Ratio Drag" in intel["fee_drag"]["title"]
    assert intel["fee_drag"]["is_direct"] is True
    assert "Positive Asymmetric Convexity" in intel["market_capture"]["verdict"]


def test_regular_plan_recommends_direct_migration():
    metrics = {"cagr_pct": 10.0, "vol_annualized_pct": 15.0, "sharpe_ratio": 0.6}
    benchmark = {"available": False}
    profile = {
        "scheme_name": "HDFC Top 100 Fund - Regular Plan",
        "plan_type": "Regular",
        "expense_ratio": 1.75,
    }

    intel = generate_quantitative_intelligence(metrics, benchmark, profile)
    assert "Switching to Direct plan" in intel["fee_drag"]["text"]
    assert intel["fee_drag"]["is_direct"] is False


def test_quadrant_classifications():
    # Value Trap / Laggard: low return, high vol
    m_laggard = {"cagr_pct": 4.0, "vol_annualized_pct": 22.0}
    b = {"available": True, "metrics": {"benchmark_cagr_pct": 12.0, "beta": 1.0}}
    intel_laggard = generate_quantitative_intelligence(m_laggard, b, {})
    assert intel_laggard["quadrant"]["title"] == "Value Trap / Laggard"

    # High-Beta Momentum: high return, high vol
    m_momentum = {"cagr_pct": 25.0, "vol_annualized_pct": 24.0}
    intel_momentum = generate_quantitative_intelligence(m_momentum, b, {})
    assert intel_momentum["quadrant"]["title"] == "High-Beta Momentum"

    # Defensive Anchor: low return, low vol
    m_defensive = {"cagr_pct": 8.0, "vol_annualized_pct": 7.0}
    intel_defensive = generate_quantitative_intelligence(m_defensive, b, {})
    assert intel_defensive["quadrant"]["title"] == "Defensive Anchor"


def test_fee_drag_with_negative_cagr_does_not_clamp_to_zero():
    metrics = {"cagr_pct": -5.0, "vol_annualized_pct": 18.0, "sharpe_ratio": -0.6}
    benchmark = {"available": False}
    profile = {
        "scheme_name": "Struggling Small Cap Fund - Regular Plan",
        "plan_type": "Regular",
        "expense_ratio": 1.5,
    }
    intel = generate_quantitative_intelligence(metrics, benchmark, profile)
    fee_text = intel["fee_drag"]["text"]
    assert "estimated ₹" in fee_text
    # 5Y drag is non-zero and positive
    assert "Switching to Direct plan" in fee_text


def test_category_median_parameters_override_hardcoded_defaults():
    # Fund has 12% vol. With default med_vol=15%, it would be low risk.
    # With empirical cat_median_vol=10%, it is high risk!
    metrics = {"cagr_pct": 16.0, "vol_annualized_pct": 12.0}
    benchmark = {"available": True, "metrics": {"benchmark_cagr_pct": 14.0}}
    # Case 1: cat_median_vol = 10.0 -> High-Beta Momentum (high return, high risk)
    intel = generate_quantitative_intelligence(
        metrics, benchmark, {}, cat_median_ret=14.0, cat_median_vol=10.0
    )
    assert intel["quadrant"]["title"] == "High-Beta Momentum"
    assert intel["quadrant"]["badge"] == "High Return, High Risk"

    # Case 2: cat_median_vol = 14.0 -> Institutional Alpha Star (high return, low risk)
    intel2 = generate_quantitative_intelligence(
        metrics, benchmark, {}, cat_median_ret=14.0, cat_median_vol=14.0
    )
    assert intel2["quadrant"]["title"] == "Institutional Alpha Star"
    assert intel2["quadrant"]["badge"] == "High Return, Low Risk"
