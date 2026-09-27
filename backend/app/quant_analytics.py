import datetime
import threading
import numpy as np
import pandas as pd
import scipy.stats as stats
from typing import Dict, Any, Optional, Tuple
from app.core.cache import cached
from app.db.connection import get_connection, fetchdf

_ts_cache: Dict[Tuple, Tuple[pd.DataFrame, Dict[str, Any]]] = {}
_ts_cache_lock = threading.Lock()

# --- Annualization base -------------------------------------------------------------------
#
# sqrt(252) is only correct for a series sampled on *trading* days. Liquid, overnight and
# arbitrage funds publish a NAV on every calendar day, so their return series carries ~365
# observations a year; annualizing those on 252 understates volatility by ~17% and drags
# every ratio built on it (Sharpe read -14 on the owner's portfolio purely from this).
#
# The base therefore follows the data. It is measured ONCE, where the series is born, and
# carried on the pandas object's `.attrs` so every consumer downstream reads the same
# number instead of re-guessing from whatever rows it happens to be holding. Re-deriving
# from a slice is the fallback, not the mechanism.

TRADING_DAYS_PER_YEAR = 252.0
CALENDAR_DAYS_PER_YEAR = 365.25
OBS_PER_YEAR_ATTR = "obs_per_year"


def infer_obs_per_year(dates, default: float = TRADING_DAYS_PER_YEAR) -> float:
    """Measures how many observations a year a date index really carries.

    Counts the observations inside every trailing 365.25-day window and takes a high
    quantile of those counts. Dividing the row count by the calendar span (the naive
    ratio) is not good enough: a fund suspended for three months, or one whose NAVs
    start part-way into the window, reports an artificially low frequency and so gets
    its annualized volatility understated. Busy windows still report the true cadence,
    and a quantile ignores the dormant ones.

    Median gap between observations cannot do this job either: a trading-day series has
    a median gap of 1 day (the Fri->Mon 3-day gaps are the minority), which would misread
    it as calendar-daily.
    """
    t = pd.to_datetime(pd.Series(list(dates))).dropna().sort_values()
    if len(t) < 3:
        return default
    days = (t - t.iloc[0]).dt.total_seconds().values / 86400.0
    span = float(days[-1])
    if span <= 0:
        return default
    if span >= CALENDAR_DAYS_PER_YEAR:
        ends = np.searchsorted(days, days + CALENDAR_DAYS_PER_YEAR, side="left")
        complete = (days + CALENDAR_DAYS_PER_YEAR) <= span
        counts = (ends - np.arange(len(days)))[complete]
        if counts.size:
            return _clamp_obs_per_year(float(np.quantile(counts, 0.9)))
    # Under a year of history there is no complete window to count, so fall back to the
    # row density over the span -- a guess, but the only one available.
    return _clamp_obs_per_year(len(t) / (span / CALENDAR_DAYS_PER_YEAR))


def _clamp_obs_per_year(value: float) -> float:
    # NAV history is keyed by (scheme, date), so a series can never tick more than once
    # per calendar day; anything above that is a counting artefact, not a real cadence.
    if not np.isfinite(value) or value <= 0:
        return TRADING_DAYS_PER_YEAR
    return float(min(CALENDAR_DAYS_PER_YEAR, max(1.0, value)))


def _dates_of(obj) -> Optional[Any]:
    if isinstance(obj, pd.DataFrame) and "nav_date" in obj.columns:
        return obj["nav_date"]
    index = getattr(obj, "index", None)
    return index if isinstance(index, pd.DatetimeIndex) else None


def with_obs_per_year(obj, obs_per_year: float):
    """Attaches the annualization base to a DataFrame/Series so it travels with the data."""
    obj.attrs[OBS_PER_YEAR_ATTR] = _clamp_obs_per_year(float(obs_per_year))
    return obj


def get_obs_per_year(obj, default: float = TRADING_DAYS_PER_YEAR) -> float:
    """The base attached upstream, where the series was built and its whole history was
    visible. Only when nothing was attached is one re-derived from the object's own dates."""
    attached = getattr(obj, "attrs", {}).get(OBS_PER_YEAR_ATTR)
    if attached is not None and np.isfinite(attached) and attached > 0:
        return _clamp_obs_per_year(float(attached))
    dates = _dates_of(obj)
    return default if dates is None else infer_obs_per_year(dates, default)


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
    return with_obs_per_year(df, get_obs_per_year(df))


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
                return with_obs_per_year(df_res.copy(), cov[OBS_PER_YEAR_ATTR]), dict(cov)

    df = df_raw.copy()
    df["nav_date"] = pd.to_datetime(df["nav_date"])
    df["nav"] = df["nav"].astype(float)
    df = df.sort_values("nav_date").reset_index(drop=True)

    # The annualization base is the cadence the fund actually had around the window, over at
    # least a year (cadence_obs_per_year) -- a 3-month window of a daily fund still ticks 365
    # times a year, and a fund whose cadence changed years ago is not annualised on its old
    # one. The Compare tab uses the same base. A base attached upstream still wins.
    attached = getattr(df_raw, "attrs", {}).get(OBS_PER_YEAR_ATTR)
    if attached is not None and np.isfinite(attached) and attached > 0:
        obs_per_year = _clamp_obs_per_year(float(attached))
    else:
        obs_per_year = cadence_obs_per_year(df["nav_date"], start_date, end_date)

    # 1. Full-history continuous returns (guarantees day 1 in window has valid return)
    df["daily_return"] = df["nav"].pct_change()
    df["log_return"] = np.log(df["nav"] / df["nav"].shift(1))

    # 2. Full-history rolling risk metrics
    # Mean EXCESS return, each return net of Rf over its own span (see rf_per_interval).
    rf_daily = rf_per_interval(interval_days_before(df).values, risk_free_rate_ann, obs_per_year)
    roll_mean = (df["daily_return"] - rf_daily).rolling(rolling_window).mean()
    roll_std = df["daily_return"].rolling(rolling_window).std()

    df["rolling_vol_ann"] = roll_std * np.sqrt(obs_per_year) * 100.0
    df["rolling_sharpe"] = np.where(
        roll_std > 1e-8,
        roll_mean / roll_std * np.sqrt(obs_per_year),
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
        "is_partial": is_partial,
        OBS_PER_YEAR_ATTR: round(obs_per_year, 2),
    }
    with_obs_per_year(df_window, obs_per_year)

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


def interval_days_before(df: pd.DataFrame) -> pd.Series:
    """Calendar days each row's return spans (since the previous NAV), aligned to df's index."""
    dates = pd.to_datetime(df["nav_date"])
    return dates.diff().dt.days.astype(float)


def rf_per_interval(days: np.ndarray, risk_free_rate_ann: float, obs_per_year: float) -> np.ndarray:
    """Risk-free return over each interval's own length in calendar days. Where the length is
    unknown (NaN), falls back to one observation of the series' own clock."""
    days = np.asarray(days, dtype=float)
    per_obs = (1.0 + risk_free_rate_ann) ** (1.0 / obs_per_year) - 1.0
    by_days = (1.0 + risk_free_rate_ann) ** (np.nan_to_num(days, nan=0.0) / 365.25) - 1.0
    return np.where(np.isfinite(days) & (days > 0), by_days, per_obs)


def cadence_obs_per_year(dates, start, end, default: float = TRADING_DAYS_PER_YEAR) -> float:
    """NAVs a year the series actually published around the analysed period [start, end]:
    NAV dates in (lo, end] per year of (end - lo), where lo is the earlier of `start` and a
    year before `end` (clipped to the first NAV). A year at least, so a 30-day window's
    holidays do not set the base; the period itself when it is longer, so a multi-year
    period is annualised on exactly the returns it contains.

    infer_obs_per_year's high quantile over the WHOLE history is the wrong base for a
    period: it remembers a cadence the fund no longer has. Quant Liquid published ~355 NAVs
    a year in 2015-17 and ~305 since 2018, so its 1-year risk was annualised on 354 --
    volatility and Sharpe overstated by sqrt(354/307) = 7.4%, and its mean return scaled
    to a year by 354 against an Rf charged over 307 returns' worth of calendar days."""
    t = pd.DatetimeIndex(pd.to_datetime(pd.Series(list(dates))).dropna()).unique().sort_values()
    if len(t) < 2:
        return default
    end_ts = pd.Timestamp(end)
    upto = t[t <= end_ts]
    if len(upto) < 2:
        return infer_obs_per_year(t, default)
    end_ts = upto[-1]
    lo = min(pd.Timestamp(start), end_ts - pd.Timedelta(days=CALENDAR_DAYS_PER_YEAR))
    lo = max(lo, upto[0])
    span_days = (end_ts - lo).total_seconds() / 86400.0
    count = int(((upto > lo) & (upto <= end_ts)).sum())
    if span_days < 7 or count < 2:
        return infer_obs_per_year(upto, default)
    return _clamp_obs_per_year(count / (span_days / CALENDAR_DAYS_PER_YEAR))


def span_residuals(returns, gap_days) -> Tuple[np.ndarray, int]:
    """Each return minus the return expected for a return of ITS span: a + b x calendar
    days, fitted by least squares over the sample. Returns (residuals, degrees of freedom).

    A plain standard deviation measures every return against one mean, so a Monday return
    (three days of accrual) counts as a deviation. For liquid/overnight/target-maturity
    funds the accrual IS the return, and the plain figure measured how often a fund skips
    NAVs: Zerodha Overnight (missing ~13 NAVs a year) read 4.3x SBI Overnight's volatility.
    Fitting the slope rather than assuming calendar-day accrual lets the data decide: an
    accrual fund's slope is its daily accrual, an equity fund's (which does not drift over
    a weekend) is noise and the result is its plain deviation. When every span is equal the
    fit is just the mean."""
    r = np.asarray(returns, dtype=float)
    g = np.asarray(gap_days, dtype=float)
    X = np.column_stack([np.ones_like(g), g])
    rank = int(np.linalg.matrix_rank(X)) if len(r) else 0
    if len(r) - rank < 1:
        return r - (r.mean() if len(r) else 0.0), max(len(r) - 1, 0)
    coef, *_ = np.linalg.lstsq(X, r, rcond=None)
    return r - X @ coef, len(r) - rank


def period_risk_ratios(returns, gap_days, obs_per_year: float, risk_free_rate_ann: float) -> Dict[str, Any]:
    """Volatility, Sharpe and Sortino of NAV-to-NAV returns whose spans differ (weekends,
    holidays, skipped NAVs). One definition for the Compare tab and the Quant page.

    * Volatility: standard deviation of the returns around the return expected for their
      span (span_residuals), x sqrt(obs_per_year).
    * Sharpe: annualised mean excess return / that volatility, each return net of Rf over
      its own span (rf_per_interval). mean x obs_per_year is the period's summed excess
      return scaled linearly to a year.
    * Sortino: the SAME numerator over the annualised downside deviation (root mean square
      of the excess returns below 0, over all returns). It used to be (CAGR - Rf): a
      compounded CAGR on a 90-day window turned a +11% quarter into 52% a year, reading
      24% above the arithmetic figure Sharpe uses, and could disagree with Sharpe in sign.

    None where a ratio is undefined (no variation / no downside)."""
    r = np.asarray(returns, dtype=float)
    n = len(r)
    if n < 2:
        return {"vol_per_obs": None, "vol_ann": None, "mean_excess_ann": None,
                "downside_dev_ann": None, "sharpe": None, "sortino": None, "n": n}
    g = np.asarray(gap_days, dtype=float)
    one_obs = CALENDAR_DAYS_PER_YEAR / obs_per_year
    g = np.where(np.isfinite(g) & (g > 0), g, one_obs)
    sqrt_obs = float(np.sqrt(obs_per_year))
    resid, dof = span_residuals(r, g)
    sd = float(np.sqrt(np.sum(resid ** 2) / dof)) if dof > 0 else 0.0
    excess = r - rf_per_interval(g, risk_free_rate_ann, obs_per_year)
    mean_excess_ann = float(np.mean(excess)) * obs_per_year
    vol_ann = sd * sqrt_obs
    downside = np.minimum(excess, 0.0)
    dd_ann = float(np.sqrt(np.sum(downside ** 2) / n)) * sqrt_obs
    return {
        "vol_per_obs": sd,
        "vol_ann": vol_ann,
        "mean_excess_ann": mean_excess_ann,
        "downside_dev_ann": dd_ann,
        "sharpe": mean_excess_ann / vol_ann if sd > 1e-10 else None,
        "sortino": mean_excess_ann / dd_ann if dd_ann > 1e-10 else None,
        "n": n,
    }


def compute_risk_adjusted_metrics(
    df: pd.DataFrame,
    risk_free_rate_ann: float = 0.065
) -> Dict[str, Any]:
    """
    Computes institutional risk-adjusted return metrics, downside risk,
    and tail risk metrics (Sharpe, Sortino, Calmar, VaR, CVaR, Skewness, Kurtosis).
    """
    interval_days = interval_days_before(df)
    df_clean = df.dropna(subset=["daily_return"]).copy()
    if len(df_clean) < 3:
        return {}

    returns = df_clean["daily_return"].values
    navs = df_clean["nav"].values
    n_days = len(returns)

    # Every annualization below runs on the base the series carries, not on 252.
    obs_per_year = get_obs_per_year(df_clean)
    sqrt_obs = np.sqrt(obs_per_year)

    # The risk-free return each NAV-to-NAV return had to beat is charged over that return's
    # OWN span inside period_risk_ratios (see rf_per_interval): a flat per-observation rate
    # turned Quant Liquid's 6.10% year (below a 6.5% Rf) into a Sharpe of +2.98.

    # Return metrics
    total_ret = (navs[-1] - navs[0]) / navs[0]
    total_days = max(1, (df_clean["nav_date"].iloc[-1] - df_clean["nav_date"].iloc[0]).days)
    cagr = calendar_day_cagr(total_ret, df_clean["nav_date"].iloc[0], df_clean["nav_date"].iloc[-1])

    # Volatility, Sharpe, Sortino: one definition shared with the Compare tab
    # (period_risk_ratios) -- volatility around each return's expected growth over its own
    # span, Sharpe and Sortino on the same annualised mean excess return. This page keeps
    # its convention of 0.0 (not None) for an undefined ratio.
    ratios = period_risk_ratios(returns, interval_days.loc[df_clean.index].values, obs_per_year, risk_free_rate_ann)
    vol_daily = ratios["vol_per_obs"]
    vol_ann = ratios["vol_ann"]
    sharpe = ratios["sharpe"] if ratios["sharpe"] is not None and vol_daily > 1e-8 else 0.0
    downside_dev_ann = ratios["downside_dev_ann"] if ratios["downside_dev_ann"] else 1e-6
    sortino = ratios["sortino"] if ratios["sortino"] is not None and vol_daily > 1e-8 else 0.0

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
    var_95_ann = var_95_daily * sqrt_obs
    var_99_ann = var_99_daily * sqrt_obs

    tail_95 = returns[returns <= var_95_daily]
    cvar_95_daily = np.mean(tail_95) if len(tail_95) > 0 else var_95_daily
    cvar_95_ann = cvar_95_daily * sqrt_obs

    tail_99 = returns[returns <= var_99_daily]
    cvar_99_daily = np.mean(tail_99) if len(tail_99) > 0 else var_99_daily
    cvar_99_ann = cvar_99_daily * sqrt_obs

    # Cornish-Fisher Expansion Adjusted VaR
    cf_var_95_daily = cornish_fisher_var(returns, 0.05)
    cf_var_95_ann = cf_var_95_daily * sqrt_obs
    cf_var_99_daily = cornish_fisher_var(returns, 0.01)
    cf_var_99_ann = cf_var_99_daily * sqrt_obs

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
        OBS_PER_YEAR_ATTR: round(obs_per_year, 2),
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

    # The overlap only ticks as often as the *sparser* of the two series -- a daily liquid
    # fund merged against a trading-day index is a trading-day sample. Taken from the bases
    # the two frames carry rather than re-measured off the merge.
    obs_per_year = min(get_obs_per_year(df_fund), get_obs_per_year(df_bench))
    sqrt_obs = np.sqrt(obs_per_year)

    # Linear Regression (Beta and Alpha)
    cov_mat = np.cov(r_fund, r_bench)
    var_bench = cov_mat[1, 1]
    bench_vol = np.sqrt(var_bench) * sqrt_obs * 100.0 if var_bench > 0 else 0.0
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
    tracking_error_ann = te_daily * sqrt_obs

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
        OBS_PER_YEAR_ATTR: round(obs_per_year, 2),
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

    obs_per_year = get_obs_per_year(df_clean)
    rf_daily = rf_per_interval(interval_days_before(df).loc[df_clean.index].values, risk_free_rate_ann, obs_per_year)

    # Mean EXCESS return, each return net of Rf over its own span.
    roll_mean = (df_clean["daily_return"] - rf_daily).rolling(window).mean()
    roll_std = df_clean["daily_return"].rolling(window).std()

    df_clean["rolling_vol_ann"] = roll_std * np.sqrt(obs_per_year) * 100.0
    df_clean["rolling_sharpe"] = np.where(
        roll_std > 1e-8,
        roll_mean / roll_std * np.sqrt(obs_per_year),
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
    seed: int = 42,
    obs_per_year: float = TRADING_DAYS_PER_YEAR,
) -> Dict[str, Any]:
    """
    Simulates forward-looking returns using Geometric Brownian Motion (GBM)
    calibrated on the scheme's empirical drift and volatility.

    One step = one observation of `returns`, so `obs_per_year` is how many steps make a
    year for THIS series. It is echoed back in the result: the caller that chose n_days
    and the chart that turns steps into years must divide by the same number, or a
    "5 year" fan silently plots 3.45 years.
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
        "n_days": n_days,
        OBS_PER_YEAR_ATTR: _clamp_obs_per_year(obs_per_year),
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


# A peer's NAV that jumps this far and comes straight back (the next NAV within
# PEER_SPIKE_REVERT of the one before) is a mis-keyed price, not a return anyone earned.
PEER_SPIKE_MOVE = 0.15
PEER_SPIKE_REVERT = 0.02
# A closed scheme's final NAV that sits this far off its peers' move that day is a
# rebasing/wind-up artefact (Quant Liquid Fund 148511/148513 printed Rs 10.0000 after
# 13.5225 on 2025-12-15 and never priced again), not a -26% day for a liquid fund.
PEER_TERMINAL_OUTLIER = 0.10
PEER_DEAD_AFTER_DAYS = 7


def _peer_average_returns(w: pd.DataFrame, last_ever: pd.Series) -> pd.Series:
    """Equal-weighted peer return per date of a wide NAV frame (dates x schemes), on the
    union calendar of the peers, each scheme taking part from its second NAV to its last.

    A peer that did not price on a date is carried at its last NAV (a 0 return) and its
    accrual lands on the date it next prices. Averaging each scheme's own NAV-to-NAV return
    on the dates it happened to price instead mixed 1-day and 3-day returns on the same
    date whenever the peers' calendars differ (weekend-pricing liquid funds, FoFs on
    overseas holidays) and double-counted the weekend: over 2021-09..2026-09 that put the
    FoF Overseas peer index 9.9 points above the calendar-consistent one (103.0% vs 93.1%;
    the buy-and-hold mean of the peers that lived through the window was 91.7%)."""
    w = w.sort_index()
    # A "Growth" NAV that never moves is a daily-payout (IDCW) option AMFI labelled Growth
    # under the plain fund name (Invesco India Liquid 139388/139390 at 1000.0000 and JM Liquid
    # 148413/148414 at 41.4437 for the whole of 2025-26): it would add exact zeros.
    counts, spread = w.count(), w.max() - w.min()
    flat = [c for c in w.columns if counts[c] >= 5 and spread[c] <= 0]
    if flat:
        w = w.drop(columns=flat)
    for c in w.columns:
        s = w[c].dropna()
        if len(s) >= 3:
            r = s.pct_change()
            back = s.shift(-1) / s.shift(1) - 1.0
            spikes = s.index[(r.abs() > PEER_SPIKE_MOVE) & (back.abs() < PEER_SPIKE_REVERT)]
            if len(spikes):
                w.loc[spikes, c] = np.nan
    first = w.apply(lambda s: s.first_valid_index())
    last = w.apply(lambda s: s.last_valid_index())
    rets = w.ffill().pct_change()
    alive = pd.DataFrame({c: (w.index > first[c]) & (w.index <= last[c]) for c in w.columns}, index=w.index)
    rets = rets.where(alive)
    if len(w.index):
        med = rets.median(axis=1, skipna=True)
        dead_before = w.index[-1] - pd.Timedelta(days=PEER_DEAD_AFTER_DAYS)
        for c in w.columns:
            lc = last[c]
            if lc is None or lc > dead_before or c not in last_ever.index or pd.Timestamp(last_ever[c]) != lc:
                continue
            r_last = rets.at[lc, c]
            if pd.notna(r_last) and pd.notna(med.get(lc)) and abs(r_last - med[lc]) > PEER_TERMINAL_OUTLIER:
                rets.at[lc, c] = np.nan
    return rets.mean(axis=1, skipna=True).dropna()


@cached(ttl=600)
def get_synthetic_category_benchmark(
    category: str,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
    exclude_scheme_code: Optional[int] = None,
    asset_class: Optional[str] = None,
) -> pd.DataFrame:
    """
    Synthesizes a category benchmark: the equal-weighted average daily return of every
    Direct-Growth scheme in the same AMFI category, excluding the analysed scheme.

    The sample is Direct-Growth ONLY, and all of it. It used to be "up to 50, Direct and
    Growth sorted first" with no filter and no stable order, which let IDCW schemes fill the
    slots the category's Direct-Growth funds did not: a daily-IDCW liquid fund distributes
    its whole accrual every day, so its NAV never moves and it adds an exact 0 to the
    average. Measured over 30 days on the live data, that understated the liquid-fund
    category's return by 23-28% (0.40% against a true 0.52%), which made every liquid fund
    look better than its peers -- on the Holdings tiles, the Performance tab and here.
    The IDCW guard also checks the name, because AMFI's own Option column sometimes calls
    an IDCW scheme "Growth".

    A scheme contributes only on dates it has NAVs, so funds that have since closed still
    count for the periods they existed -- restricting to today's active schemes would
    instead bake survivorship bias into every historical window. If a category has no
    Direct plans at all (a pre-2013 legacy category), its Growth schemes of any plan stand
    in rather than returning nothing.

    Rules added 2026-09-25 (each one found in the live data):
    - The category is the SEBI category, not AMFI's raw label: "Income/Debt Oriented
      Schemes - Liquid Fund" and "Debt Scheme - Liquid Fund" are one peer group
      (classification.sebi_category), not two half-samples.
    - ETFs have a single plan and AMFI's plan label on them is noise ("Other ETFs" had just
      2 schemes marked Direct, so every ETF was benchmarked against a Sensex Next 50 and a
      Midcap 150 ETF): in an ETF category every Growth scheme is a peer.
    - Segregated portfolios (credit side pockets) are not investable funds: their NAVs are
      write-down/recovery accounting (0.1353 -> 0.5342 in one day, then 0), which put the
      Credit Risk peer index at +154% over five years against +76% for the funds.
    - NAVs <= 0, a NAV that never moves, a spike that reverts on the next NAV, and the
      outlying final NAV of a scheme that then stopped publishing are dropped (see
      _peer_average_returns).
    - The average is taken on the peers' common calendar (see _peer_average_returns).
    - `asset_class` (classification.classify) optionally narrows the peers to one asset
      class inside the category: "Index Funds", "Other ETFs" and "FoF Domestic" mix Nifty,
      gilt, Nasdaq, gold and silver schemes.
    """
    if not category:
        return pd.DataFrame()
    from app.classification import broad_category_of, classify, sebi_category

    lookback_start = start_date - datetime.timedelta(days=10) if start_date else None
    con = get_connection()
    try:
        target = sebi_category(category)
        labels = [r[0] for r in con.execute("SELECT DISTINCT category FROM schemes WHERE category IS NOT NULL").fetchall()]
        labels = sorted({lab for lab in labels if sebi_category(lab) == target} | {category})
        schemes = fetchdf(con.execute(
            """
            SELECT scheme_code, scheme_name, plan_type, category
            FROM schemes
            WHERE category = ANY(%s)
              AND option_type = 'Growth'
              AND scheme_name NOT ILIKE '%%IDCW%%'
              -- Not a bare '%%dividend%%': that also emptied the Dividend Yield category,
              -- whose Growth funds are named for the stocks they pick, not for a payout.
              AND scheme_name !~* 'dividend(?!\\s*yield)'
              AND scheme_name !~* 'segregat'
            """, (labels,)))
        if exclude_scheme_code is not None and not schemes.empty:
            schemes = schemes[schemes["scheme_code"] != int(exclude_scheme_code)]
        if asset_class and not schemes.empty:
            keep = [classify(r.category, broad_category_of(r.category), r.scheme_name) == asset_class
                    for r in schemes.itertuples()]
            schemes = schemes[keep]
        if not schemes.empty:
            is_etf = schemes["category"].str.contains("ETF", case=False, na=False)
            has_direct = (schemes["plan_type"] == "Direct").any()
            schemes = schemes[(schemes["plan_type"] == "Direct") | is_etf | (not has_direct)]
        codes = [int(c) for c in schemes["scheme_code"]] if not schemes.empty else []
        if not codes:
            return pd.DataFrame()
        navs = fetchdf(con.execute(
            "SELECT scheme_code, nav_date, nav FROM nav_history WHERE scheme_code = ANY(%s) AND nav > 0 "
            "AND (%s::date IS NULL OR nav_date >= %s::date) AND (%s::date IS NULL OR nav_date <= %s::date)",
            (codes, lookback_start, lookback_start, end_date, end_date)))
        last_ever = fetchdf(con.execute(
            "SELECT scheme_code, max(nav_date) AS last_nav FROM nav_history WHERE scheme_code = ANY(%s) AND nav > 0 GROUP BY 1",
            (codes,)))
    except Exception:
        return pd.DataFrame()
    finally:
        con.close()
    if navs.empty:
        return pd.DataFrame()
    navs["nav_date"] = pd.to_datetime(navs["nav_date"])
    wide = navs.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").astype(float)
    last_ever = pd.to_datetime(last_ever.set_index("scheme_code")["last_nav"]) if not last_ever.empty else pd.Series(dtype="datetime64[ns]")
    avg = _peer_average_returns(wide, last_ever)
    df_bench = pd.DataFrame({"nav_date": avg.index, "daily_return": avg.to_numpy()})

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

    obs_per_year = get_obs_per_year(df_clean)
    sqrt_obs = np.sqrt(obs_per_year)

    # Empirical VaR and CVaR
    var_95_daily = float(np.percentile(returns, 5.0))
    var_99_daily = float(np.percentile(returns, 1.0))
    var_95_ann = var_95_daily * sqrt_obs
    var_99_ann = var_99_daily * sqrt_obs

    tail_95 = returns[returns <= var_95_daily]
    cvar_95_daily = float(np.mean(tail_95)) if len(tail_95) > 0 else var_95_daily
    cvar_95_ann = cvar_95_daily * sqrt_obs

    tail_99 = returns[returns <= var_99_daily]
    cvar_99_daily = float(np.mean(tail_99)) if len(tail_99) > 0 else var_99_daily
    cvar_99_ann = cvar_99_daily * sqrt_obs

    # Cornish-Fisher adjusted VaR
    cf_var_95_daily = cornish_fisher_var(returns, 0.05)
    cf_var_99_daily = cornish_fisher_var(returns, 0.01)
    cf_var_95_ann = cf_var_95_daily * sqrt_obs
    cf_var_99_ann = cf_var_99_daily * sqrt_obs

    # Higher moments
    ret_series = pd.Series(returns)
    skew = float(ret_series.skew()) if not np.isnan(ret_series.skew()) else 0.0
    kurt = float(ret_series.kurtosis()) if not np.isnan(ret_series.kurtosis()) else 0.0

    # Downside deviation & Sortino -- the same definition as compute_risk_adjusted_metrics
    # and the Compare tab (period_risk_ratios), so the page never shows two Sortinos.
    ratios = period_risk_ratios(returns, interval_days_before(df_fund).loc[df_clean.index].values,
                                obs_per_year, risk_free_rate_ann)
    downside_dev_ann = ratios["downside_dev_ann"] if ratios["downside_dev_ann"] else 1e-6
    sortino = ratios["sortino"] if ratios["sortino"] is not None else 0.0

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
        OBS_PER_YEAR_ATTR: round(obs_per_year, 2),
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
