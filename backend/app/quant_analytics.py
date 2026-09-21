import datetime
import math
import threading
import numpy as np
import pandas as pd
import scipy.stats as stats
from typing import Dict, Any, Optional, Tuple, List
from app.core.cache import cached
from app.db.connection import get_connection, fetchdf

_ts_cache: Dict[Tuple, Tuple[pd.DataFrame, Dict[str, Any]]] = {}
_ts_cache_lock = threading.Lock()

def calendar_day_cagr(cum_return: float, start_date, end_date) -> float:
    """Annualizes a cumulative return over its actual calendar-day span. Compounds for spans
    of 30+ days; linearly extrapolates below that to avoid absurd over-annualization of a short
    window. This is the single CAGR convention used everywhere in this module — previously
    compute_benchmark_relative_metrics annualized over trading-day count (252/n) instead, so a
    fund's CAGR used for Alpha/Treynor/Information Ratio could silently disagree with the
    CAGR headline number computed the same way in compute_risk_adjusted_metrics."""
    total_days = max(1, (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days)
    if total_days >= 30:
        return ((1.0 + cum_return) ** (365.25 / total_days)) - 1.0
    return cum_return * (365.25 / total_days)


def compute_daily_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes daily simple and log returns from historical NAV series.
    Input df must contain 'nav_date' and 'nav'.
    """
    if df.empty or len(df) < 2:
        return df
    df = df.copy()
    df["nav_date"] = pd.to_datetime(df["nav_date"])
    df["nav"] = df["nav"].astype(float)
    df = df.sort_values("nav_date").reset_index(drop=True)

    df["daily_return"] = df["nav"].pct_change()
    df["log_return"] = np.log(df["nav"] / df["nav"].shift(1))
    df["cum_return"] = (df["nav"] / df["nav"].iloc[0]) - 1.0

    # Peak and Drawdown
    df["peak_nav"] = df["nav"].cummax()
    df["drawdown"] = (df["nav"] - df["peak_nav"]) / df["peak_nav"]
    df["drawdown_pct"] = df["drawdown"] * 100.0
    return df


def prepare_fund_timeseries(
    df_raw: pd.DataFrame,
    start_date: datetime.date,
    end_date: datetime.date,
    rolling_window: int = 30,
    risk_free_rate_ann: float = 0.065
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    Takes complete raw historical NAV records for a fund, calculates continuous
    daily returns and rolling metrics across the full history to prevent edge-effect dropouts,
    and then slices strictly into the active [start_date, end_date] window.
    Guarantees day 1 in the selected window has an accurate, valid daily return and rolling metrics.
    """
    if df_raw.empty or len(df_raw) < 2:
        return pd.DataFrame(), {"has_data": False}

    try:
        cache_key = (
            len(df_raw),
            str(df_raw["nav_date"].iloc[0]),
            str(df_raw["nav_date"].iloc[-1]),
            round(float(df_raw["nav"].iloc[0]), 4),
            round(float(df_raw["nav"].iloc[-1]), 4),
            str(start_date),
            str(end_date),
            rolling_window,
            round(risk_free_rate_ann, 6),
        )
    except Exception:
        cache_key = None

    if cache_key is not None:
        with _ts_cache_lock:
            if cache_key in _ts_cache:
                df_res, cov = _ts_cache[cache_key]
                return df_res.copy(), dict(cov)

    df = df_raw.copy()
    df["nav_date"] = pd.to_datetime(df["nav_date"])
    df["nav"] = df["nav"].astype(float)
    df = df.sort_values("nav_date").reset_index(drop=True)

    # 1. Full-history continuous returns (guarantees day 1 in window has valid return)
    df["daily_return"] = df["nav"].pct_change()
    df["log_return"] = np.log(df["nav"] / df["nav"].shift(1))

    # 2. Full-history rolling risk metrics
    rf_daily = (1.0 + risk_free_rate_ann) ** (1.0 / 252.0) - 1.0
    roll_mean = df["daily_return"].rolling(rolling_window).mean()
    roll_std = df["daily_return"].rolling(rolling_window).std()

    df["rolling_vol_ann"] = roll_std * np.sqrt(252.0) * 100.0
    df["rolling_sharpe"] = np.where(
        roll_std > 1e-8,
        (roll_mean - rf_daily) / roll_std * np.sqrt(252.0),
        0.0
    )
    df["rolling_return_30d"] = (df["nav"] / df["nav"].shift(rolling_window) - 1.0) * 100.0

    # 3. Peak and Drawdown
    df["historical_peak"] = df["nav"].cummax()
    df["historical_drawdown"] = (df["nav"] - df["historical_peak"]) / df["historical_peak"]

    # 4. Slice strictly into the active date window
    s_ts = pd.to_datetime(start_date)
    e_ts = pd.to_datetime(end_date)
    mask = (df["nav_date"] >= s_ts) & (df["nav_date"] <= e_ts)
    df_window = df[mask].copy().reset_index(drop=True)

    if df_window.empty:
        return pd.DataFrame(), {"has_data": False}

    # Recalculate window-specific peak, drawdown, and cumulative returns
    df_window["window_peak"] = df_window["nav"].cummax()
    df_window["drawdown"] = (df_window["nav"] - df_window["window_peak"]) / df_window["window_peak"]
    df_window["drawdown_pct"] = df_window["drawdown"] * 100.0
    df_window["cum_return"] = (df_window["nav"] / df_window["nav"].iloc[0]) - 1.0

    actual_start = df_window["nav_date"].iloc[0].date()
    actual_end = df_window["nav_date"].iloc[-1].date()
    actual_days = (actual_end - actual_start).days
    req_days = (end_date - start_date).days
    is_partial = req_days > 100 and actual_days < int(req_days * 0.75)

    coverage = {
        "has_data": True,
        "n_trading_days": len(df_window),
        "actual_start": actual_start,
        "actual_end": actual_end,
        "actual_days": actual_days,
        "requested_days": req_days,
        "is_partial": is_partial
    }

    if cache_key is not None:
        with _ts_cache_lock:
            if len(_ts_cache) > 200:
                _ts_cache.clear()
            _ts_cache[cache_key] = (df_window.copy(), dict(coverage))

    return df_window, coverage


def cornish_fisher_var(returns: np.ndarray, alpha: float = 0.05) -> float:
    """
    Computes Cornish-Fisher expansion adjusted Value at Risk return quantile.
    Adjusts standard Gaussian VaR quantile for sample skewness and excess kurtosis:
      w_alpha = z_alpha + (z^2 - 1)*S/6 + (z^3 - 3z)*K/24 - (2z^3 - 5z)*S^2/36
      VaR_CF = mu + w_alpha * sigma
    Returns daily return quantile as a float (e.g. -0.018 for -1.8%).
    """
    if len(returns) < 3:
        return 0.0
    mu = float(np.mean(returns))
    sigma = float(np.std(returns, ddof=1))
    if sigma < 1e-12:
        return mu

    ret_s = pd.Series(returns)
    s = float(ret_s.skew()) if not np.isnan(ret_s.skew()) else 0.0
    k = float(ret_s.kurtosis()) if not np.isnan(ret_s.kurtosis()) else 0.0

    z = float(stats.norm.ppf(alpha))
    w = (
        z
        + ((z ** 2 - 1.0) * s) / 6.0
        + ((z ** 3 - 3.0 * z) * k) / 24.0
        - ((2.0 * z ** 3 - 5.0 * z) * (s ** 2)) / 36.0
    )
    if alpha < 0.5 and s < 0 and w >= z:
        # Under extreme moments where quadratic terms invert the expansion (w >= z when skewness is negative),
        # fall back to the linear skewness expansion so tail losses cannot become gains.
        w = z + ((z ** 2 - 1.0) * s) / 6.0
    return float(mu + w * sigma)


def compute_drawdown_duration_metrics(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Computes comprehensive drawdown duration and recovery metrics:
    - max_drawdown_pct: peak-to-trough worst drop percentage
    - max_drawdown_peak_date, max_drawdown_trough_date, max_drawdown_recovery_date
    - max_drawdown_peak_nav, max_drawdown_trough_nav
    - drawdown_decline_days: calendar days from peak to trough
    - drawdown_recovery_days: calendar days from trough to recovery (or None)
    - drawdown_total_duration_days: calendar days from peak to recovery (or current)
    - recovered: bool indicating if peak was restored
    - longest_underwater_days: longest calendar-day underwater spell in history
    - current_underwater_days: calendar days currently below high watermark
    - is_currently_underwater: bool
    """
    if df.empty or len(df) < 2:
        return {
            "max_drawdown_pct": 0.0,
            "max_drawdown_peak_date": None,
            "max_drawdown_trough_date": None,
            "max_drawdown_recovery_date": None,
            "max_drawdown_peak_nav": None,
            "max_drawdown_trough_nav": None,
            "drawdown_decline_days": 0,
            "drawdown_recovery_days": None,
            "drawdown_total_duration_days": 0,
            "recovered": True,
            "longest_underwater_days": 0,
            "current_underwater_days": 0,
            "is_currently_underwater": False,
        }

    d = df.copy()
    d["nav_date"] = pd.to_datetime(d["nav_date"])
    d["nav"] = d["nav"].astype(float)
    d = d.sort_values("nav_date").reset_index(drop=True)

    d["peak_nav"] = d["nav"].cummax()
    d["dd"] = (d["nav"] - d["peak_nav"]) / d["peak_nav"]

    # Maximum drawdown trough
    mdd_idx = int(d["dd"].idxmin())
    mdd_val = float(d.loc[mdd_idx, "dd"])

    if mdd_val >= -1e-6:
        start_date_str = d["nav_date"].iloc[0].strftime("%Y-%m-%d")
        end_date_str = d["nav_date"].iloc[-1].strftime("%Y-%m-%d")
        first_nav = float(d["nav"].iloc[0])
        return {
            "max_drawdown_pct": 0.0,
            "max_drawdown_peak_date": start_date_str,
            "max_drawdown_trough_date": start_date_str,
            "max_drawdown_recovery_date": end_date_str,
            "max_drawdown_peak_nav": round(first_nav, 4),
            "max_drawdown_trough_nav": round(first_nav, 4),
            "drawdown_decline_days": 0,
            "drawdown_recovery_days": 0,
            "drawdown_total_duration_days": 0,
            "recovered": True,
            "longest_underwater_days": 0,
            "current_underwater_days": 0,
            "is_currently_underwater": False,
        }

    trough_date = d.loc[mdd_idx, "nav_date"]
    trough_nav = float(d.loc[mdd_idx, "nav"])
    peak_nav = float(d.loc[mdd_idx, "peak_nav"])

    # Locate peak date before or at mdd_idx
    prior_peaks = d.loc[:mdd_idx][d.loc[:mdd_idx, "nav"] >= peak_nav - 1e-6]
    peak_date = prior_peaks.iloc[-1]["nav_date"] if not prior_peaks.empty else d.loc[0, "nav_date"]

    # Locate recovery date after mdd_idx
    post_recovery = d.loc[mdd_idx:][d.loc[mdd_idx:, "nav"] >= peak_nav - 1e-6]
    if not post_recovery.empty:
        recovery_date = post_recovery.iloc[0]["nav_date"]
        recovered = True
        recovery_days = int((recovery_date - trough_date).days)
        total_duration = int((recovery_date - peak_date).days)
        rec_str = recovery_date.strftime("%Y-%m-%d")
    else:
        recovery_date = None
        recovered = False
        recovery_days = None
        total_duration = int((d["nav_date"].iloc[-1] - peak_date).days)
        rec_str = None

    decline_days = int((trough_date - peak_date).days)

    # Underwater spells across entire history
    longest_underwater_days = 0
    current_episode_start = None
    running_peak_val = -1.0
    running_peak_dt = None

    for i in range(len(d)):
        cur_nav = float(d.loc[i, "nav"])
        cur_dt = d.loc[i, "nav_date"]
        if cur_nav >= running_peak_val - 1e-6:
            if current_episode_start is not None and running_peak_dt is not None:
                ep_days = int((cur_dt - running_peak_dt).days)
                if ep_days > longest_underwater_days:
                    longest_underwater_days = ep_days
                current_episode_start = None
            running_peak_val = cur_nav
            running_peak_dt = cur_dt
        else:
            if current_episode_start is None:
                current_episode_start = running_peak_dt

    is_currently_underwater = current_episode_start is not None
    current_underwater_days = 0
    if is_currently_underwater and running_peak_dt is not None:
        current_underwater_days = int((d["nav_date"].iloc[-1] - running_peak_dt).days)
        if current_underwater_days > longest_underwater_days:
            longest_underwater_days = current_underwater_days

    return {
        "max_drawdown_pct": round(mdd_val * 100.0, 4),
        "max_drawdown_peak_date": peak_date.strftime("%Y-%m-%d"),
        "max_drawdown_trough_date": trough_date.strftime("%Y-%m-%d"),
        "max_drawdown_recovery_date": rec_str,
        "max_drawdown_peak_nav": round(peak_nav, 4),
        "max_drawdown_trough_nav": round(trough_nav, 4),
        "drawdown_decline_days": decline_days,
        "drawdown_recovery_days": recovery_days,
        "drawdown_total_duration_days": total_duration,
        "recovered": recovered,
        "longest_underwater_days": longest_underwater_days,
        "current_underwater_days": current_underwater_days,
        "is_currently_underwater": is_currently_underwater,
    }


def compute_risk_adjusted_metrics(
    df: pd.DataFrame,
    risk_free_rate_ann: float = 0.065
) -> Dict[str, Any]:
    """
    Computes institutional risk-adjusted return metrics, downside risk,
    and tail risk metrics (Sharpe, Sortino, Calmar, VaR, CVaR, Skewness, Kurtosis).
    """
    df_clean = df.dropna(subset=["daily_return"]).copy()
    if len(df_clean) < 3:
        return {}

    returns = df_clean["daily_return"].values
    navs = df_clean["nav"].values
    n_days = len(returns)

    # Daily risk free rate
    rf_daily = (1.0 + risk_free_rate_ann) ** (1.0 / 252.0) - 1.0

    # Return metrics
    total_ret = (navs[-1] - navs[0]) / navs[0]
    total_days = max(1, (df_clean["nav_date"].iloc[-1] - df_clean["nav_date"].iloc[0]).days)
    cagr = calendar_day_cagr(total_ret, df_clean["nav_date"].iloc[0], df_clean["nav_date"].iloc[-1])

    # Volatility
    vol_daily = np.std(returns, ddof=1)
    vol_ann = vol_daily * np.sqrt(252.0)

    # Excess return
    excess_daily = returns - rf_daily
    mean_excess = np.mean(excess_daily)

    # Sharpe Ratio (Annualized)
    sharpe = (mean_excess / vol_daily) * np.sqrt(252.0) if vol_daily > 1e-8 else 0.0

    # Downside Deviation (Semi-deviation relative to Rf)
    downside_diff = returns[returns < rf_daily] - rf_daily
    if len(downside_diff) > 0:
        downside_dev_daily = np.sqrt(np.sum(downside_diff ** 2) / n_days)
        downside_dev_ann = downside_dev_daily * np.sqrt(252.0)
    else:
        downside_dev_ann = 1e-6

    # Sortino Ratio
    if vol_daily < 1e-8:
        sortino = 0.0
    else:
        sortino = (cagr - risk_free_rate_ann) / downside_dev_ann if downside_dev_ann > 1e-8 else 0.0

    # Maximum Drawdown
    drawdown = df_clean["drawdown"].values
    mdd = np.min(drawdown)
    current_dd = drawdown[-1]

    # Calmar Ratio: undefined (not zero) when there was no drawdown to divide by —
    # a flat/monotonically-rising NAV is the *best* case, not a "zero risk-adjusted return".
    calmar = (cagr / abs(mdd)) if abs(mdd) > 1e-6 else np.nan

    # Value at Risk (VaR) & Expected Shortfall (CVaR)
    var_95_daily = np.percentile(returns, 5.0)
    var_99_daily = np.percentile(returns, 1.0)
    var_95_ann = var_95_daily * np.sqrt(252.0)
    var_99_ann = var_99_daily * np.sqrt(252.0)

    tail_95 = returns[returns <= var_95_daily]
    cvar_95_daily = np.mean(tail_95) if len(tail_95) > 0 else var_95_daily
    cvar_95_ann = cvar_95_daily * np.sqrt(252.0)

    tail_99 = returns[returns <= var_99_daily]
    cvar_99_daily = np.mean(tail_99) if len(tail_99) > 0 else var_99_daily
    cvar_99_ann = cvar_99_daily * np.sqrt(252.0)

    # Cornish-Fisher Expansion Adjusted VaR
    cf_var_95_daily = cornish_fisher_var(returns, 0.05)
    cf_var_95_ann = cf_var_95_daily * np.sqrt(252.0)
    cf_var_99_daily = cornish_fisher_var(returns, 0.01)
    cf_var_99_ann = cf_var_99_daily * np.sqrt(252.0)

    # Higher Moments & Tail Risk
    ret_series = pd.Series(returns)
    skew = ret_series.skew()
    kurt = ret_series.kurtosis()  # Excess kurtosis (Fisher, Normal=0)

    # Daily Trading Win Rate & Gain/Loss Statistics
    pos_returns = returns[returns > 0]
    neg_returns = returns[returns < 0]
    win_rate = (len(pos_returns) / n_days) * 100.0 if n_days > 0 else 0.0

    best_day = np.max(returns) * 100.0 if len(returns) > 0 else 0.0
    worst_day = np.min(returns) * 100.0 if len(returns) > 0 else 0.0

    sum_gains = np.sum(pos_returns)
    sum_losses = abs(np.sum(neg_returns))
    # Profit Factor = gains / losses (Kestner). Gain-to-Pain Ratio = (gains - losses) / losses
    # (Schwager) — a distinct metric that nets the losses out of the numerator; the two were
    # previously computed with the same formula and always rendered identically.
    profit_factor = (sum_gains / sum_losses) if sum_losses > 1e-8 else np.nan
    gain_to_pain = ((sum_gains - sum_losses) / sum_losses) if sum_losses > 1e-8 else np.nan

    avg_gain = (np.mean(pos_returns) * 100.0) if len(pos_returns) > 0 else 0.0
    avg_loss = (np.mean(neg_returns) * 100.0) if len(neg_returns) > 0 else 0.0

    return {
        "n_trading_days": n_days,
        "total_days": total_days,
        "total_return_pct": total_ret * 100.0,
        "cagr_pct": cagr * 100.0,
        "vol_annualized_pct": vol_ann * 100.0,
        "vol_daily_pct": vol_daily * 100.0,
        "downside_dev_ann_pct": downside_dev_ann * 100.0,
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "calmar_ratio": round(calmar, 4) if not np.isnan(calmar) else None,
        "max_drawdown_pct": mdd * 100.0,
        "current_drawdown_pct": current_dd * 100.0,
        "var_95_daily_pct": var_95_daily * 100.0,
        "var_95_ann_pct": var_95_ann * 100.0,
        "var_99_daily_pct": var_99_daily * 100.0,
        "var_99_ann_pct": float(round(var_99_ann * 100.0, 4)),
        "cvar_95_daily_pct": cvar_95_daily * 100.0,
        "cvar_95_ann_pct": cvar_95_ann * 100.0,
        "cvar_99_daily_pct": float(round(cvar_99_daily * 100.0, 4)),
        "cvar_99_ann_pct": float(round(cvar_99_ann * 100.0, 4)),
        "cf_var_95_daily_pct": float(round(cf_var_95_daily * 100.0, 4)),
        "cf_var_95_ann_pct": float(round(cf_var_95_ann * 100.0, 4)),
        "cf_var_99_daily_pct": float(round(cf_var_99_daily * 100.0, 4)),
        "cf_var_99_ann_pct": float(round(cf_var_99_ann * 100.0, 4)),
        "skewness": round(skew, 4),
        "kurtosis": round(kurt, 4),
        "win_rate_pct": round(win_rate, 2),
        "best_day_pct": round(best_day, 4),
        "worst_day_pct": round(worst_day, 4),
        "gain_to_pain_ratio": round(gain_to_pain, 4) if not np.isnan(gain_to_pain) else None,
        "profit_factor": round(profit_factor, 4) if not np.isnan(profit_factor) else None,
        "avg_gain_pct": round(avg_gain, 4),
        "avg_loss_pct": round(avg_loss, 4),
    }


def compute_benchmark_relative_metrics(
    df_fund: pd.DataFrame,
    df_bench: pd.DataFrame,
    risk_free_rate_ann: float = 0.065
) -> Dict[str, Any]:
    """
    Computes Modern Portfolio Theory (MPT) and Capital Asset Pricing Model (CAPM)
    relative metrics: Beta, Jensen's Alpha, R-Squared, Tracking Error, Information Ratio,
    Treynor Ratio, and Up/Down Market Capture Ratios.
    """
    if df_fund.empty or df_bench.empty:
        return {}

    m1 = df_fund[["nav_date", "daily_return"]].dropna().rename(columns={"daily_return": "r_fund"})
    m2 = df_bench[["nav_date", "daily_return"]].dropna().rename(columns={"daily_return": "r_bench"})

    merged = pd.merge(m1, m2, on="nav_date", how="inner").dropna()
    if len(merged) < 5:
        return {}

    r_fund = merged["r_fund"].values
    r_bench = merged["r_bench"].values

    # Linear Regression (Beta and Alpha)
    cov_mat = np.cov(r_fund, r_bench)
    var_bench = cov_mat[1, 1]
    bench_vol = np.sqrt(var_bench) * np.sqrt(252.0) * 100.0 if var_bench > 0 else 0.0
    cov_fb = cov_mat[0, 1]

    beta = cov_fb / var_bench if var_bench > 1e-10 else 1.0
    corr = np.corrcoef(r_fund, r_bench)[0, 1]
    r_squared = (corr ** 2) if not np.isnan(corr) else 0.0

    # Annualized Returns of Fund and Bench, over their common (overlapping) window — using the
    # same calendar-day annualization convention as compute_risk_adjusted_metrics's headline
    # CAGR, so Alpha/Treynor/Information Ratio don't silently disagree with the displayed CAGR.
    n_days = len(merged)
    fund_ret_cum = np.prod(1.0 + r_fund) - 1.0
    bench_ret_cum = np.prod(1.0 + r_bench) - 1.0
    window_start, window_end = merged["nav_date"].iloc[0], merged["nav_date"].iloc[-1]
    fund_cagr = calendar_day_cagr(fund_ret_cum, window_start, window_end)
    bench_cagr = calendar_day_cagr(bench_ret_cum, window_start, window_end)

    # Jensen's Alpha (Annualized)
    # Alpha = (R_p - R_f) - Beta * (R_m - R_f)
    alpha = (fund_cagr - risk_free_rate_ann) - beta * (bench_cagr - risk_free_rate_ann)

    # Tracking Error & Information Ratio
    diff_returns = r_fund - r_bench
    te_daily = np.std(diff_returns, ddof=1)
    tracking_error_ann = te_daily * np.sqrt(252.0)

    info_ratio = (fund_cagr - bench_cagr) / tracking_error_ann if tracking_error_ann > 1e-8 else 0.0

    # Treynor Ratio
    treynor = (fund_cagr - risk_free_rate_ann) / beta if abs(beta) > 1e-6 else 0.0

    # Up & Down Market Capture Ratios
    up_mask = r_bench > 0
    down_mask = r_bench < 0

    if np.sum(up_mask) > 0:
        up_fund_cum = np.prod(1.0 + r_fund[up_mask]) - 1.0
        up_bench_cum = np.prod(1.0 + r_bench[up_mask]) - 1.0
        up_capture = (up_fund_cum / up_bench_cum * 100.0) if abs(up_bench_cum) > 1e-6 else 100.0
    else:
        up_capture = 100.0

    if np.sum(down_mask) > 0:
        down_fund_cum = np.prod(1.0 + r_fund[down_mask]) - 1.0
        down_bench_cum = np.prod(1.0 + r_bench[down_mask]) - 1.0
        down_capture = (down_fund_cum / down_bench_cum * 100.0) if abs(down_bench_cum) > 1e-6 else 100.0
    else:
        down_capture = 100.0

    capture_ratio = (up_capture / down_capture) if abs(down_capture) > 1e-6 else np.nan

    return {
        "beta": round(beta, 4),
        "alpha_annualized_pct": round(alpha * 100.0, 4),
        "correlation": round(corr, 4),
        "r_squared": round(r_squared, 4),
        "tracking_error_pct": round(tracking_error_ann * 100.0, 4),
        "information_ratio": round(info_ratio, 4),
        "treynor_ratio": round(treynor, 4),
        "up_market_capture_pct": round(up_capture, 2),
        "down_market_capture_pct": round(down_capture, 2),
        "capture_ratio": round(capture_ratio, 2) if not np.isnan(capture_ratio) else None,
        "benchmark_cagr_pct": round(bench_cagr * 100.0, 4),
        "benchmark_vol_annualized_pct": round(bench_vol, 4),
        "excess_cagr_pct": round((fund_cagr - bench_cagr) * 100.0, 4),
        "common_trading_days": n_days,
        "regression_points": merged
    }


def compute_rolling_metrics(
    df: pd.DataFrame,
    window: int = 30,
    risk_free_rate_ann: float = 0.065
) -> pd.DataFrame:
    """
    Computes rolling annualized volatility, rolling Sharpe ratio, and rolling drawdown.
    """
    df_clean = df.dropna(subset=["daily_return"]).copy()
    if len(df_clean) < window:
        return pd.DataFrame()

    rf_daily = (1.0 + risk_free_rate_ann) ** (1.0 / 252.0) - 1.0

    roll_mean = df_clean["daily_return"].rolling(window).mean()
    roll_std = df_clean["daily_return"].rolling(window).std()

    df_clean["rolling_vol_ann"] = roll_std * np.sqrt(252.0) * 100.0
    df_clean["rolling_sharpe"] = np.where(
        roll_std > 1e-8,
        (roll_mean - rf_daily) / roll_std * np.sqrt(252.0),
        0.0
    )
    df_clean["rolling_return"] = (df_clean["nav"] / df_clean["nav"].shift(window) - 1.0) * 100.0

    return df_clean.dropna(subset=["rolling_vol_ann"])


def run_monte_carlo_simulation(
    latest_nav: float,
    returns: np.ndarray,
    n_simulations: int = 500,
    n_days: int = 252,
    initial_capital: float = 100000.0,
    seed: int = 42
) -> Dict[str, Any]:
    """
    Simulates forward-looking returns using Geometric Brownian Motion (GBM)
    calibrated on the scheme's empirical drift and volatility.
    Projects portfolio values for ₹100,000 initial capital over 252 trading days.
    """
    if len(returns) < 60:
        return {}

    mu = np.mean(returns)
    sigma = np.std(returns, ddof=1)

    # GBM drift parameter
    drift = mu - 0.5 * (sigma ** 2)

    rng = np.random.default_rng(seed)  # Deterministic seed for reproducible analysis
    shocks = rng.normal(0, 1, size=(n_simulations, n_days))
    daily_factors = np.exp(drift + sigma * shocks)

    # Cumulative trajectory starting at initial_capital
    start_col = np.ones((n_simulations, 1))
    cumulative_factors = np.cumprod(np.hstack([start_col, daily_factors]), axis=1)
    paths = initial_capital * cumulative_factors

    # Quantiles across all steps [0..252]
    p5 = np.percentile(paths, 5.0, axis=0)
    p25 = np.percentile(paths, 25.0, axis=0)
    p50 = np.percentile(paths, 50.0, axis=0)  # Median
    p75 = np.percentile(paths, 75.0, axis=0)
    p95 = np.percentile(paths, 95.0, axis=0)

    # Terminal outcomes
    terminals = paths[:, -1]
    prob_profit = (np.sum(terminals >= initial_capital) / n_simulations) * 100.0
    prob_beat_inf = (np.sum(terminals >= initial_capital * 1.06) / n_simulations) * 100.0
    prob_beat_12 = (np.sum(terminals >= initial_capital * 1.12) / n_simulations) * 100.0

    exp_terminal = np.mean(terminals)
    median_terminal = np.median(terminals)
    var_95_loss = initial_capital - np.percentile(terminals, 5.0)

    step_days = list(range(n_days + 1))

    return {
        "days": step_days,
        "p5": p5,
        "p25": p25,
        "p50": p50,
        "p75": p75,
        "p95": p95,
        "initial_capital": initial_capital,
        "expected_terminal": round(exp_terminal, 2),
        "median_terminal": round(median_terminal, 2),
        "prob_profit_pct": round(prob_profit, 2),
        "prob_beat_inflation_pct": round(prob_beat_inf, 2),
        "prob_beat_12pct": round(prob_beat_12, 2),
        "var_95_capital": round(max(0, var_95_loss), 2),
        "worst_case_p5": round(p5[-1], 2),
        "best_case_p95": round(p95[-1], 2),
        "n_simulations": n_simulations
    }


@cached(ttl=600)
def get_synthetic_category_benchmark(
    category: str,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
    exclude_scheme_code: Optional[int] = None,
) -> pd.DataFrame:
    """
    Synthesizes an institutional Category Benchmark by aggregating the daily average returns
    of up to 50 Direct-Growth schemes in the same AMFI category, excluding the analysed scheme.
    """
    if not category:
        return pd.DataFrame()

    con = get_connection()
    # A few days of lookback before start_date guarantee LAG() has a prior NAV to diff against
    # for the window's first trading day, without pulling the scheme's entire history.
    lookback_start = None
    if start_date:
        lookback_start = start_date - datetime.timedelta(days=10)
    exclude_clause = ""
    params: list = [category]
    if exclude_scheme_code is not None:
        exclude_clause = "AND scheme_code <> %s"
        params.append(int(exclude_scheme_code))
    sql = f"""
        WITH category_schemes AS (
            SELECT scheme_code
            FROM schemes
            WHERE category = %s
            {exclude_clause}
            ORDER BY
                CASE WHEN plan_type = 'Direct' THEN 0 ELSE 1 END,
                CASE WHEN option_type = 'Growth' THEN 0 ELSE 1 END
            LIMIT 50
        ),
        daily_diff AS (
            SELECT n.scheme_code, n.nav_date, n.nav,
                   (n.nav - LAG(n.nav) OVER (PARTITION BY n.scheme_code ORDER BY n.nav_date)) /
                   NULLIF(LAG(n.nav) OVER (PARTITION BY n.scheme_code ORDER BY n.nav_date), 0) as ret
            FROM nav_history n
            JOIN category_schemes cs ON n.scheme_code = cs.scheme_code
            WHERE (%s IS NULL OR n.nav_date >= %s) AND (%s IS NULL OR n.nav_date <= %s)
        )
        SELECT nav_date, AVG(ret) as daily_return
        FROM daily_diff
        WHERE ret IS NOT NULL
        GROUP BY nav_date
        ORDER BY nav_date ASC;
    """
    try:
        df_bench = fetchdf(con.execute(sql, params + [lookback_start, lookback_start, end_date, end_date]))
    except Exception:
        df_bench = pd.DataFrame()
    finally:
        con.close()

    if not df_bench.empty:
        df_bench["nav_date"] = pd.to_datetime(df_bench["nav_date"])
        if start_date and end_date:
            s_ts = pd.to_datetime(start_date)
            e_ts = pd.to_datetime(end_date)
            df_bench = df_bench[(df_bench["nav_date"] >= s_ts) & (df_bench["nav_date"] <= e_ts)].copy()
        df_bench = df_bench.reset_index(drop=True)
        if not df_bench.empty:
            df_bench["nav"] = 100.0 * np.cumprod(1.0 + df_bench["daily_return"])
            df_bench["cum_return"] = (df_bench["nav"] / df_bench["nav"].iloc[0]) - 1.0
            df_bench = compute_daily_returns(df_bench)
    return df_bench


def compute_tail_risk_metrics(
    df_fund: pd.DataFrame,
    df_bench: Optional[pd.DataFrame] = None,
    risk_free_rate_ann: float = 0.065,
) -> Dict[str, Any]:
    """
    Institutional tail risk analytics suite:
    - Parametric and Cornish-Fisher 95% & 99% VaR (daily & annualized)
    - 95% & 99% Expected Shortfall (CVaR) (daily & annualized)
    - Peak-to-trough-to-recovery drawdown metrics & underwater statistics
    - Downside deviation & Sortino ratio
    - Downside capture efficiency & capture ratios (if benchmark supplied)
    """
    if df_fund.empty:
        return {}

    df_clean = df_fund.dropna(subset=["daily_return"]).copy()
    if len(df_clean) < 3:
        return {}

    returns = df_clean["daily_return"].values.astype(float)
    navs = df_clean["nav"].values.astype(float)
    n_days = len(returns)

    rf_daily = (1.0 + risk_free_rate_ann) ** (1.0 / 252.0) - 1.0

    # Empirical VaR and CVaR
    var_95_daily = float(np.percentile(returns, 5.0))
    var_99_daily = float(np.percentile(returns, 1.0))
    var_95_ann = var_95_daily * np.sqrt(252.0)
    var_99_ann = var_99_daily * np.sqrt(252.0)

    tail_95 = returns[returns <= var_95_daily]
    cvar_95_daily = float(np.mean(tail_95)) if len(tail_95) > 0 else var_95_daily
    cvar_95_ann = cvar_95_daily * np.sqrt(252.0)

    tail_99 = returns[returns <= var_99_daily]
    cvar_99_daily = float(np.mean(tail_99)) if len(tail_99) > 0 else var_99_daily
    cvar_99_ann = cvar_99_daily * np.sqrt(252.0)

    # Cornish-Fisher adjusted VaR
    cf_var_95_daily = cornish_fisher_var(returns, 0.05)
    cf_var_99_daily = cornish_fisher_var(returns, 0.01)
    cf_var_95_ann = cf_var_95_daily * np.sqrt(252.0)
    cf_var_99_ann = cf_var_99_daily * np.sqrt(252.0)

    # Higher moments
    ret_series = pd.Series(returns)
    skew = float(ret_series.skew()) if not np.isnan(ret_series.skew()) else 0.0
    kurt = float(ret_series.kurtosis()) if not np.isnan(ret_series.kurtosis()) else 0.0

    # Downside deviation & Sortino
    downside_diff = returns[returns < rf_daily] - rf_daily
    if len(downside_diff) > 0:
        downside_dev_daily = float(np.sqrt(np.sum(downside_diff ** 2) / n_days))
        downside_dev_ann = downside_dev_daily * np.sqrt(252.0)
    else:
        downside_dev_ann = 1e-6

    total_ret = float((navs[-1] - navs[0]) / navs[0])
    cagr = calendar_day_cagr(total_ret, df_clean["nav_date"].iloc[0], df_clean["nav_date"].iloc[-1])
    sortino = (cagr - risk_free_rate_ann) / downside_dev_ann if downside_dev_ann > 1e-8 else 0.0

    # Drawdown duration & recovery metrics
    dd_metrics = compute_drawdown_duration_metrics(df_clean)

    # Benchmark relative downside capture
    bench_metrics = {}
    if df_bench is not None and not df_bench.empty:
        rel = compute_benchmark_relative_metrics(df_clean, df_bench, risk_free_rate_ann=risk_free_rate_ann)
        if rel:
            up_cap = rel.get("up_market_capture_pct", 100.0)
            dn_cap = rel.get("down_market_capture_pct", 100.0)
            cap_ratio = rel.get("capture_ratio")
            dce = (up_cap / dn_cap) if dn_cap and abs(dn_cap) > 1e-6 else None
            bench_metrics = {
                "up_market_capture_pct": up_cap,
                "down_market_capture_pct": dn_cap,
                "capture_ratio": cap_ratio,
                "downside_capture_efficiency": round(dce, 4) if dce is not None else None,
                "beta": rel.get("beta"),
                "tracking_error_pct": rel.get("tracking_error_pct"),
            }

    return {
        "n_trading_days": n_days,
        "skewness": round(skew, 4),
        "kurtosis": round(kurt, 4),
        "var_95_daily_pct": round(var_95_daily * 100.0, 4),
        "var_95_ann_pct": round(var_95_ann * 100.0, 4),
        "var_99_daily_pct": round(var_99_daily * 100.0, 4),
        "var_99_ann_pct": round(var_99_ann * 100.0, 4),
        "cvar_95_daily_pct": round(cvar_95_daily * 100.0, 4),
        "cvar_95_ann_pct": round(cvar_95_ann * 100.0, 4),
        "cvar_99_daily_pct": round(cvar_99_daily * 100.0, 4),
        "cvar_99_ann_pct": round(cvar_99_ann * 100.0, 4),
        "cf_var_95_daily_pct": round(cf_var_95_daily * 100.0, 4),
        "cf_var_95_ann_pct": round(cf_var_95_ann * 100.0, 4),
        "cf_var_99_daily_pct": round(cf_var_99_daily * 100.0, 4),
        "cf_var_99_ann_pct": round(cf_var_99_ann * 100.0, 4),
        "downside_dev_ann_pct": round(downside_dev_ann * 100.0, 4),
        "sortino_ratio": round(sortino, 4),
        "drawdown": dd_metrics,
        "benchmark_capture": bench_metrics,
    }


def compute_cornish_fisher_var(
    returns: np.ndarray,
    confidence_levels: List[float] = [0.95, 0.99],
) -> Dict[str, float]:
    """Cornish-Fisher expansion adjusting VaR for skewness and excess kurtosis."""
    clean = returns[~np.isnan(returns)]
    if len(clean) < 5:
        raise ValueError("Insufficient return data for Cornish-Fisher VaR calculation (need >= 5)")

    mu = float(np.mean(clean))
    sigma = float(np.std(clean, ddof=1))
    if sigma < 1e-12:
        return {f"var_{int(round(c * 100))}_cf_daily_pct": 0.0 for c in confidence_levels}

    ret_s = pd.Series(clean)
    s = float(ret_s.skew()) if not np.isnan(ret_s.skew()) else 0.0
    k = float(ret_s.kurtosis()) if not np.isnan(ret_s.kurtosis()) else 0.0

    out = {
        "skewness": round(s, 4),
        "kurtosis": round(k, 4),
        "mean_daily_pct": round(mu * 100.0, 4),
        "vol_daily_pct": round(sigma * 100.0, 4),
    }

    for alpha_conf in confidence_levels:
        tag = int(round(alpha_conf * 100))
        p = 1.0 - alpha_conf
        z = float(stats.norm.ppf(p))
        var_cf_raw = cornish_fisher_var(clean, alpha=p)
        var_cf_daily = -var_cf_raw
        var_cf_ann = var_cf_daily * math.sqrt(252.0)
        var_gaussian = -(mu + z * sigma)

        out[f"var_{tag}_cf_daily_pct"] = round(float(var_cf_daily * 100.0), 4)
        out[f"var_{tag}_cf_ann_pct"] = round(float(var_cf_ann * 100.0), 4)
        out[f"var_{tag}_gaussian_daily_pct"] = round(float(var_gaussian * 100.0), 4)
        out[f"var_{tag}_conservative_premium_pct"] = round(float((var_cf_daily - var_gaussian) * 100.0), 4)

    return out


def compute_expected_shortfall_and_capture(
    fund_returns: np.ndarray,
    bench_returns: Optional[np.ndarray] = None,
    nav_series: Optional[pd.Series] = None,
    confidence_level: float = 0.99,
) -> Dict[str, Any]:
    """Calculates 99% CVaR (Expected Shortfall), drawdown duration/recovery, and downside capture efficiency."""
    clean_f = fund_returns[~np.isnan(fund_returns)]
    if len(clean_f) < 5:
        raise ValueError("Insufficient data points for CVaR")

    # 1. CVaR / Expected Shortfall
    p_cutoff = (1.0 - confidence_level) * 100.0
    var_threshold = float(np.percentile(clean_f, p_cutoff))
    tail = clean_f[clean_f <= var_threshold]
    cvar_daily = float(np.mean(tail)) if len(tail) > 0 else var_threshold
    cvar_ann = cvar_daily * math.sqrt(252.0)

    # 2. Maximum Drawdown duration & recovery if nav_series given
    dd_metrics = {
        "max_drawdown_pct": 0.0,
        "drawdown_duration_days": 0,
        "recovery_duration_days": None,
        "peak_date": None,
        "trough_date": None,
    }
    if nav_series is not None and len(nav_series) > 2:
        df_nav = pd.DataFrame({"nav_date": pd.to_datetime(nav_series.index), "nav": nav_series.values})
        dd_res = compute_drawdown_duration_metrics(df_nav)
        dd_metrics = {
            "max_drawdown_pct": dd_res["max_drawdown_pct"],
            "drawdown_duration_days": dd_res["drawdown_decline_days"],
            "recovery_duration_days": dd_res["drawdown_recovery_days"],
            "peak_date": dd_res["max_drawdown_peak_date"],
            "trough_date": dd_res["max_drawdown_trough_date"],
        }

    # 3. Downside capture efficiency
    capture_metrics = {
        "downside_capture_ratio": 1.0,
        "upside_capture_ratio": 1.0,
        "capture_efficiency": 1.0,
    }
    if bench_returns is not None and len(bench_returns) == len(clean_f):
        df_cb = pd.DataFrame({"fund": clean_f, "bench": bench_returns}).dropna()
        down_days = df_cb[df_cb["bench"] < 0]
        up_days = df_cb[df_cb["bench"] > 0]

        if len(down_days) > 0:
            fund_down_comp = float(np.prod(1.0 + down_days["fund"]) - 1.0)
            bench_down_comp = float(np.prod(1.0 + down_days["bench"]) - 1.0)
            if abs(bench_down_comp) > 1e-8:
                capture_metrics["downside_capture_ratio"] = round((fund_down_comp / bench_down_comp) * 100.0, 2)

        if len(up_days) > 0:
            fund_up_comp = float(np.prod(1.0 + up_days["fund"]) - 1.0)
            bench_up_comp = float(np.prod(1.0 + up_days["bench"]) - 1.0)
            if abs(bench_up_comp) > 1e-8:
                capture_metrics["upside_capture_ratio"] = round((fund_up_comp / bench_up_comp) * 100.0, 2)

        if capture_metrics["downside_capture_ratio"] > 1e-4:
            capture_metrics["capture_efficiency"] = round(
                capture_metrics["upside_capture_ratio"] / capture_metrics["downside_capture_ratio"], 4
            )

    return {
        "cvar_99_daily_pct": round(float(cvar_daily * 100.0), 4),
        "cvar_99_ann_pct": round(float(cvar_ann * 100.0), 4),
        "var_99_daily_pct": round(float(var_threshold * 100.0), 4),
        "drawdown_metrics": dd_metrics,
        "capture_metrics": capture_metrics,
    }

