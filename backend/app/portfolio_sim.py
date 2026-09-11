"""Multi-fund portfolio backtesting engine: SIP/lump-sum contribution simulation with
optional periodic rebalancing, built entirely on official AMFI NAV history already in
DuckDB. No forward-looking projection here — this is a historical backtest only.

Two distinct, complementary return figures are computed, deliberately kept separate
because they answer different questions and conflating them is a common source of
investor confusion:

- Money-weighted return (XIRR): what the actual investor experienced, given exactly when
  each contribution went in. This is the number a SIP investor cares about.
- Time-weighted return (TWR / CAGR): how the underlying fund mix performed independent of
  contribution timing — computed via a synthetic index series and handed to
  quant_analytics.compute_risk_adjusted_metrics so Sharpe/Sortino/Vol/Max-Drawdown are
  computed with the exact same, already-tested formulas used on the single-fund Quant page.
"""

import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app import quant_analytics

REBALANCE_NONE = "None"
REBALANCE_MONTHLY = "Monthly"
REBALANCE_QUARTERLY = "Quarterly"
REBALANCE_ANNUALLY = "Annually"


def xirr(cash_flows: List[Tuple[datetime.date, float]], guess: float = 0.1) -> Optional[float]:
    """Money-weighted annualized return via Newton-Raphson on the NPV function, with a
    bisection fallback. Returns None if the flows don't converge to a sane, well-defined
    rate rather than ever displaying a wild or wrong number."""
    flows = [(d, cf) for d, cf in cash_flows if cf != 0]
    if len(flows) < 2:
        return None
    if not (any(cf < 0 for _, cf in flows) and any(cf > 0 for _, cf in flows)):
        return None  # no sign change: not a solvable investment cash-flow series

    t0 = flows[0][0]
    years = [(d - t0).days / 365.25 for d, _ in flows]
    amounts = [cf for _, cf in flows]

    def npv(rate: float) -> float:
        return sum(cf / ((1.0 + rate) ** t) for cf, t in zip(amounts, years))

    def npv_prime(rate: float) -> float:
        return sum(-t * cf / ((1.0 + rate) ** (t + 1)) for cf, t in zip(amounts, years) if t > 0)

    rate = guess
    for _ in range(100):
        try:
            f = npv(rate)
        except (OverflowError, ZeroDivisionError):
            break
        if abs(f) < 1e-6:
            return rate
        fp = npv_prime(rate)
        if abs(fp) < 1e-12:
            break
        new_rate = rate - f / fp
        if new_rate <= -0.999:
            new_rate = (rate - 0.999) / 2.0
        if abs(new_rate - rate) < 1e-9:
            return new_rate
        rate = new_rate

    # Newton didn't converge cleanly — bisection over a wide, sane range as a robust fallback.
    lo, hi = -0.99, 10.0
    try:
        f_lo, f_hi = npv(lo), npv(hi)
    except (OverflowError, ZeroDivisionError):
        return None
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def _month_starts(dates: pd.DatetimeIndex) -> set:
    """First available trading date in each calendar month present in `dates`."""
    s = pd.Series(dates, index=dates)
    return set(s.groupby([dates.year, dates.month]).min().values)


def _quarter_starts(dates: pd.DatetimeIndex) -> set:
    s = pd.Series(dates, index=dates)
    return set(s.groupby([dates.year, dates.quarter]).min().values)


def _year_starts(dates: pd.DatetimeIndex) -> set:
    s = pd.Series(dates, index=dates)
    return set(s.groupby([dates.year]).min().values)


def run_backtest(
    nav_wide: pd.DataFrame,
    weights: Dict[int, float],
    mode: str,
    lump_sum_amount: float,
    sip_amount: float,
    rebalance_freq: str,
    lookback_row: Optional[pd.Series] = None,
) -> Dict[str, Any]:
    """
    nav_wide: DataFrame indexed by date (ascending, no gaps within the window — already
    forward-filled by the caller), one column per scheme_code, values = NAV.
    weights: {scheme_code: target_weight_fraction}, must sum to ~1.0.
    mode: "Lump Sum" or "SIP".
    lookback_row: optional NAV row (Series indexed by scheme_code) for the trading day just
    before nav_wide starts, keyed by the same date as its own .name. When supplied, the
    time-weighted index is seeded one day earlier so the *first* day of the displayed window
    gets a real day-over-day return instead of being dropped as NaN — without this, a
    lump-sum backtest's Time-Weighted CAGR can disagree with its own XIRR (which does use the
    full window) purely from losing that first day, even though both describe the same
    single investment.
    Returns a dict with the portfolio value series, invested-capital series, per-fund
    holdings, cash flows, and headline metrics — or {"error": "..."} if the window has
    too little data to simulate.
    """
    if nav_wide.empty or len(nav_wide) < 2:
        return {"error": "Not enough overlapping NAV history for the selected funds in this window."}

    dates = nav_wide.index
    codes = list(weights.keys())

    if mode == "SIP":
        contrib_dates = _month_starts(dates)
    else:
        contrib_dates = {dates[0]}

    if rebalance_freq == REBALANCE_MONTHLY:
        rebalance_dates = _month_starts(dates)
    elif rebalance_freq == REBALANCE_QUARTERLY:
        rebalance_dates = _quarter_starts(dates)
    elif rebalance_freq == REBALANCE_ANNUALLY:
        rebalance_dates = _year_starts(dates)
    else:
        rebalance_dates = set()

    units = {c: 0.0 for c in codes}
    portfolio_values = []
    invested_series = []
    cash_flows: List[Tuple[datetime.date, float]] = []
    total_invested = 0.0
    holdings_over_time = {c: [] for c in codes}

    for dt in dates:
        navs_today = nav_wide.loc[dt]
        d_py = dt.date() if hasattr(dt, "date") else dt

        if dt in contrib_dates:
            contribution = lump_sum_amount if mode != "SIP" else sip_amount
            if contribution > 0:
                for c in codes:
                    nav_val = navs_today[c]
                    if nav_val and nav_val > 0:
                        units[c] += (contribution * weights[c]) / nav_val
                total_invested += contribution
                cash_flows.append((d_py, -contribution))

        port_val = sum(units[c] * navs_today[c] for c in codes if pd.notna(navs_today[c]))

        if dt in rebalance_dates and port_val > 0:
            for c in codes:
                nav_val = navs_today[c]
                if nav_val and nav_val > 0:
                    units[c] = (port_val * weights[c]) / nav_val

        for c in codes:
            holdings_over_time[c].append(units[c] * navs_today[c])

        portfolio_values.append(port_val)
        invested_series.append(total_invested)

    df_result = pd.DataFrame({
        "nav_date": dates,
        "portfolio_value": portfolio_values,
        "total_invested": invested_series,
    })
    for c in codes:
        df_result[f"holding_{c}"] = holdings_over_time[c]

    final_value = portfolio_values[-1]
    cash_flows_with_terminal = cash_flows + [(dates[-1].date(), final_value)]
    money_weighted_return = xirr(cash_flows_with_terminal)

    # Time-weighted index: ₹100 tracking the SAME target weights and rebalancing schedule,
    # but with no new contributions — isolates strategy performance from contribution timing.
    has_lookback = lookback_row is not None and all(c in lookback_row.index and pd.notna(lookback_row[c]) for c in codes)
    seed_row = lookback_row if has_lookback else nav_wide.iloc[0]
    twr_dates = ([lookback_row.name] + list(dates)) if has_lookback else list(dates)

    twr_units = {c: (100.0 * weights[c]) / seed_row[c] for c in codes}
    twr_values = []
    for dt in twr_dates:
        navs_today = lookback_row if (has_lookback and dt == lookback_row.name) else nav_wide.loc[dt]
        val = sum(twr_units[c] * navs_today[c] for c in codes if pd.notna(navs_today[c]))
        if dt in rebalance_dates and val > 0:
            for c in codes:
                nav_val = navs_today[c]
                if nav_val and nav_val > 0:
                    twr_units[c] = (val * weights[c]) / nav_val
        twr_values.append(val)

    df_twr = pd.DataFrame({"nav_date": twr_dates, "nav": twr_values})
    df_twr_returns = quant_analytics.compute_daily_returns(df_twr)
    if has_lookback:
        # Drop the seeded lookback day now that it's done its job of giving day 1 a real return.
        df_twr_returns = df_twr_returns[df_twr_returns["nav_date"] >= pd.Timestamp(dates[0])].reset_index(drop=True)
    twr_metrics = quant_analytics.compute_risk_adjusted_metrics(df_twr_returns) if len(df_twr_returns) >= 3 else {}

    return {
        "df_result": df_result,
        "df_twr": df_twr_returns,
        "final_value": final_value,
        "total_invested": total_invested,
        "absolute_gain": final_value - total_invested,
        "absolute_return_pct": ((final_value / total_invested) - 1.0) * 100.0 if total_invested > 0 else None,
        "money_weighted_xirr_pct": (money_weighted_return * 100.0) if money_weighted_return is not None else None,
        "twr_metrics": twr_metrics,
        "n_contributions": len(cash_flows),
        "codes": codes,
    }
