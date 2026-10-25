"""Automated Institutional Quantitative Intelligence engine.

Produces natural-language diagnostics, factor attribution, style drift analysis,
tail-risk evaluation, fee drag compounding analysis, and 4-quadrant alignment
for mutual fund schemes based on empirical AMFI data and quantitative metrics.
"""

from typing import Any, Dict, Optional
import numpy as np


def generate_quantitative_intelligence(
    metrics: Dict[str, Any],
    benchmark: Dict[str, Any],
    profile: Dict[str, Any],
    gross_alpha_pct: Optional[float] = None,
    cat_median_ret: Optional[float] = None,
    cat_median_vol: Optional[float] = None,
) -> Dict[str, Any]:
    """Generates structured, natural-language executive insights for institutional allocators."""
    if not metrics:
        return {}

    cagr = metrics.get("cagr_pct") or 0.0
    vol = metrics.get("vol_annualized_pct") or 0.0
    sharpe = metrics.get("sharpe_ratio") or 0.0
    sortino = metrics.get("sortino_ratio") or 0.0
    calmar = metrics.get("calmar_ratio")
    mdd = abs(metrics.get("max_drawdown_pct") or 0.0)
    var_95_d = metrics.get("var_95_daily_pct") or 0.0
    cvar_95_ann = abs(metrics.get("cvar_95_ann_pct") or 0.0)
    skew = metrics.get("skewness") or 0.0
    kurt = metrics.get("kurtosis") or 0.0
    win_rate = metrics.get("win_rate_pct") or 0.0

    b_metrics = benchmark.get("metrics") or {}
    has_bench = benchmark.get("available", False) and bool(b_metrics)
    beta = b_metrics.get("beta") if has_bench else None
    alpha = b_metrics.get("alpha_annualized_pct") if has_bench else None
    r2 = b_metrics.get("r_squared") if has_bench else None
    te = b_metrics.get("tracking_error_pct") if has_bench else None
    info_ratio = b_metrics.get("information_ratio") if has_bench else None
    up_cap = b_metrics.get("up_market_capture_pct") if has_bench else None
    dn_cap = b_metrics.get("down_market_capture_pct") if has_bench else None
    cap_ratio = b_metrics.get("capture_ratio") if has_bench else None
    bench_label = benchmark.get("label", "Benchmark")

    # Expense ratio & fee details
    ter = profile.get("expense_ratio")
    ter_pct = float(ter) if ter is not None and not np.isnan(float(ter)) else None
    plan_type = profile.get("plan_type", "Regular")
    is_direct = "direct" in str(plan_type).lower() or "direct" in str(profile.get("scheme_name", "")).lower()

    # 1. 4-Quadrant Volatility Classification
    bench_cagr = b_metrics.get("benchmark_cagr_pct") if has_bench and b_metrics.get("benchmark_cagr_pct") is not None else cagr
    med_ret = cat_median_ret if cat_median_ret is not None else bench_cagr
    med_vol = cat_median_vol if cat_median_vol is not None else 15.0

    if cagr >= med_ret and vol <= med_vol:
        quadrant_title = "Institutional Alpha Star"
        quadrant_badge = "High Return, Low Risk"
        quadrant_color = "#059669"
        quadrant_desc = (
            f"Delivers superior compounding ({cagr:.2f}%) with restrained volatility ({vol:.2f}%), "
            f"consistently outperforming category median ({med_ret:.2f}%) on superior risk discipline."
        )
    elif cagr >= med_ret and vol > med_vol:
        quadrant_title = "High-Beta Momentum"
        quadrant_badge = "High Return, High Risk"
        quadrant_color = "#2563EB"
        quadrant_desc = (
            f"Captures strong absolute upside ({cagr:.2f}%) but with elevated price variance ({vol:.2f}%). "
            f"Best suited for tactical growth mandates rather than defensive core capital."
        )
    elif cagr < med_ret and vol <= med_vol:
        quadrant_title = "Defensive Anchor"
        quadrant_badge = "Low Return, Low Risk"
        quadrant_color = "#475569"
        quadrant_desc = (
            f"Maintains low volatility ({vol:.2f}%) and disciplined capital preservation, but lags category "
            f"upside ({cagr:.2f}% vs {med_ret:.2f}%). Functions as a lower-drawdown liquidity buffer."
        )
    else:
        quadrant_title = "Value Trap / Laggard"
        quadrant_badge = "Low Return, High Risk"
        quadrant_color = "#DC2626"
        quadrant_desc = (
            f"Exhibits undesirable risk-return asymmetry: high volatility ({vol:.2f}%) coupled with "
            f"sub-par compounding ({cagr:.2f}%). Suffers structural performance drag."
        )

    # 2. Return Drivers & Factor Attribution
    if has_bench and beta is not None and alpha is not None:
        beta_desc = "high market sensitivity (aggressive)" if beta > 1.15 else "defensive market stance" if beta < 0.85 else "balanced market beta"
        alpha_status = "positive true active value addition" if alpha > 0.5 else "negative manager drag" if alpha < -0.5 else "neutral index-level replication"
        return_drivers_text = (
            f"Market exposure (Beta = {beta:.2f}) indicates a {beta_desc}. Active manager selection generated "
            f"{alpha:+.2f}% annualized net alpha relative to {bench_label}, representing {alpha_status}. "
            f"Overall risk-adjusted efficiency stands at Sharpe {sharpe:.2f} and Sortino {sortino:.2f}."
        )
    else:
        return_drivers_text = (
            f"Delivers an annualized CAGR of {cagr:.2f}% with {vol:.2f}% annualized volatility. "
            f"Generates a Sharpe ratio of {sharpe:.2f} and a Sortino downside-adjusted ratio of {sortino:.2f}, "
            f"with daily trading win rate of {win_rate:.1f}%."
        )

    # 3. Style Drift & Benchmark Fit
    if has_bench and r2 is not None and te is not None:
        if r2 >= 0.90:
            drift_assessment = "High Benchmark Discipline (Low Style Drift)"
            drift_text = (
                f"With R² of {r2:.2f} and low tracking error ({te:.2f}%), this fund adheres strictly to its "
                f"category benchmark mandate. Idiosyncratic risk is low; performance tracks systematic category beta."
            )
        elif r2 >= 0.75:
            drift_assessment = "Moderate Active Tilts (Selective Deviation)"
            drift_text = (
                f"R² of {r2:.2f} indicates that {(1 - r2) * 100:.1f}% of return variance stems from active "
                f"security selection and sector allocation outside {bench_label}. Tracking error is {te:.2f}%."
            )
        else:
            drift_assessment = "Significant Idiosyncratic Risk (High Active Style Drift)"
            drift_text = (
                f"Low R² ({r2:.2f}) indicates significant deviation from {bench_label}. "
                f"The fund operates with high active share and thematic discretion, resulting in high tracking error ({te:.2f}%)."
            )
        if info_ratio is not None:
            drift_text += f" Information Ratio is {info_ratio:.2f}."
    else:
        drift_assessment = "Independent Profile (Benchmark Unavailable)"
        drift_text = "Benchmark overlap is insufficient to compute R-squared and tracking error metrics."

    # 4. Volatility Regime & Tail Risk
    tail_flags = []
    if skew < -0.3:
        tail_flags.append(f"negative skewness ({skew:.2f}) indicating left-tail downside vulnerability")
    elif skew > 0.3:
        tail_flags.append(f"positive skewness ({skew:.2f}) showing right-tail upside convexity")
    else:
        tail_flags.append(f"near-symmetric return dispersion (skewness {skew:.2f})")

    if kurt > 1.5:
        tail_flags.append(f"heavy excess kurtosis ({kurt:.2f}) warning of fat-tailed extreme shock probability")
    elif kurt < -0.5:
        tail_flags.append(f"light kurtosis ({kurt:.2f}) reflecting low tail-risk dispersion")

    tail_risk_text = (
        f"The return distribution exhibits {'; '.join(tail_flags)}. "
        f"Empirical 95% 1-day Value at Risk (VaR) is {abs(var_95_d):.2f}%, while 95% Conditional VaR (Expected Shortfall) "
        f"indicates an average annualized tail loss of {cvar_95_ann:.2f}% during severe drawdown regimes. "
        f"Historical maximum drawdown reached -{mdd:.2f}%" + (f" with Calmar ratio of {calmar:.2f}." if calmar else ".")
    )

    # 5. Fee Drag Impact
    if ter_pct is not None:
        bps = int(round(ter_pct * 100))
        capital = 100000.0
        gross_factor = max(0.01, 1.0 + (cagr + ter_pct) / 100.0)
        net_factor = max(0.01, 1.0 + cagr / 100.0)
        val_gross = capital * (gross_factor ** 5)
        val_net = capital * (net_factor ** 5)
        drag_5y = max(0.0, val_gross - val_net)

        if not is_direct:
            potential_saving_bps = max(10, min(150, int(round((ter_pct - 0.70) * 100)) if ter_pct > 0.70 else int(round(ter_pct * 0.5 * 100))))
            plan_advice = (
                f"Currently on a Regular plan ({ter_pct:.2f}% TER). Switching to Direct plan could save approximately "
                f"{potential_saving_bps} bps annually, preserving compounding capital over long holding horizons."
            )
        else:
            plan_advice = f"Direct plan deployment ({ter_pct:.2f}% TER) eliminates intermediary distribution commissions."

        fee_drag_text = (
            f"Annual expense ratio of {ter_pct:.2f}% ({bps} bps). Over a 5-year investment horizon on ₹100,000 capital, "
            f"this total expense ratio compounds to an estimated ₹{drag_5y:,.0f} in fee drag compared to zero-fee growth. "
            f"{plan_advice}"
        )
        if gross_alpha_pct is not None:
            fee_drag_text += f" Pre-fee gross manager alpha was {gross_alpha_pct:+.2f}%."
    else:
        fee_drag_text = "Official expense ratio disclosure is unavailable or derived for this scheme."

    # 6. Market Capture Asymmetry
    if up_cap is not None and dn_cap is not None:
        if up_cap > dn_cap:
            capture_verdict = "Positive Asymmetric Convexity"
            capture_text = (
                f"Favorable asymmetric market capture: {up_cap:.1f}% upside capture versus only {dn_cap:.1f}% downside capture "
                f"(Capture Ratio: {cap_ratio:.2f}x). The fund participates more strongly in market rallies than in corrections."
            )
        else:
            capture_verdict = "Downside Heavy Asymmetry"
            capture_text = (
                f"Downside capture ({dn_cap:.1f}%) exceeds upside capture ({up_cap:.1f}%), resulting in a capture ratio of "
                f"{cap_ratio:.2f}x. The portfolio experiences heightened sensitivity during benchmark drawdowns."
            )
    else:
        capture_verdict = "Capture Data Unavailable"
        capture_text = "Benchmark overlap is insufficient to calculate up/down market capture."

    # 7. Executive Verdict
    executive_verdict = (
        f"{profile.get('scheme_name', 'Scheme')} is classified as an {quadrant_title} ({quadrant_badge}). "
        f"It achieves a {cagr:.2f}% annualized return with a Sharpe ratio of {sharpe:.2f} and maximum drawdown of -{mdd:.2f}%. "
        + (f"Manager net alpha of {alpha:+.2f}% demonstrates {alpha_status}. " if (has_bench and alpha is not None) else "")
        + f"{quadrant_desc}"
    )

    return {
        "executive_verdict": executive_verdict,
        "quadrant": {
            "title": quadrant_title,
            "badge": quadrant_badge,
            "color": quadrant_color,
            "description": quadrant_desc,
        },
        "return_drivers": {
            "title": "Return Drivers & Factor Attribution",
            "text": return_drivers_text,
            "sharpe": sharpe,
            "sortino": sortino,
            "beta": beta,
            "alpha": alpha,
        },
        "style_drift": {
            "title": "Style Drift & Mandate Adherence",
            "assessment": drift_assessment,
            "text": drift_text,
            "r_squared": r2,
            "tracking_error_pct": te,
            "information_ratio": info_ratio,
        },
        "tail_risk": {
            "title": "Volatility Regime & Tail Risk",
            "text": tail_risk_text,
            "skewness": skew,
            "kurtosis": kurt,
            "var_95_daily_pct": var_95_d,
            "cvar_95_ann_pct": cvar_95_ann,
            "calmar_ratio": calmar,
            "max_drawdown_pct": mdd,
        },
        "fee_drag": {
            "title": "Expense Ratio Drag & Compounding",
            "text": fee_drag_text,
            "ter_pct": ter_pct,
            "is_direct": is_direct,
            "gross_alpha_pct": gross_alpha_pct,
        },
        "market_capture": {
            "title": "Market Capture Asymmetry",
            "verdict": capture_verdict,
            "text": capture_text,
            "up_market_capture_pct": up_cap,
            "down_market_capture_pct": dn_cap,
            "capture_ratio": cap_ratio,
        },
    }
