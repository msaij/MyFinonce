"""Portfolio backtest orchestration -- the lookback-stitch (10-day pre-window
fetch + pivot, so the Time-Weighted return series gets a valid first-day
return) that lived inline in fetcher/pages/2_Compare_&_Simulate.py's page
code, moved server-side per the migration plan.

IMPORTANT date-dtype note (new, not present in the original DuckDB-era page):
portfolio_sim.run_backtest()'s rebalance-date helpers (_month_starts/
_quarter_starts/_year_starts) are typed `dates: pd.DatetimeIndex` and call
.year/.month/.quarter -- vectorized accessors that only exist on a real
pandas DatetimeIndex, not a plain object-dtype Index of datetime.date
values. db.get_nav_history_dataframe() (SQLite-backed) returns nav_date as
plain datetime.date objects (via the sqlite3 DATE converter), NOT
pd.Timestamp -- unlike whatever the original DuckDB client happened to
produce. Skipping the pd.to_datetime() conversion below would make any SIP
or rebalanced backtest crash with AttributeError the first time a
rebalance-date helper ran (a plain Lump-Sum + no-rebalance backtest would
superficially "work" and mask the bug, since it never calls those helpers)
-- confirmed by reading portfolio_sim.py's actual type hints and accessor
calls, not assumed.
"""

import datetime
from typing import Any, Dict, List

import pandas as pd

from app import portfolio_sim
from app.db import queries as db


def run_portfolio_backtest(
    scheme_codes: List[int],
    weights: Dict[int, float],
    mode: str,
    lump_sum_amount: float,
    sip_amount: float,
    rebalance_freq: str,
    start_date: datetime.date,
    end_date: datetime.date,
) -> Dict[str, Any]:
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        return {"error": "At least one fund needs a positive weight."}
    normalized_weights = {c: w / weight_sum for c, w in weights.items()}

    df_hist = db.get_nav_history_dataframe(scheme_codes, start_date=start_date, end_date=end_date)
    if df_hist.empty:
        return {"error": "No historical NAV records for any selected fund in this time window."}

    df_hist = df_hist.copy()
    df_hist["nav_date"] = pd.to_datetime(df_hist["nav_date"])  # see module docstring -- required, not cosmetic
    nav_wide = df_hist.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").sort_index()
    nav_wide = nav_wide.ffill().dropna()

    missing_codes = [c for c in normalized_weights if c not in nav_wide.columns]
    if missing_codes:
        normalized_weights = {c: w for c, w in normalized_weights.items() if c not in missing_codes}
        w_total = sum(normalized_weights.values())
        if w_total <= 0:
            return {"error": "No usable overlapping NAV data for any selected fund.", "missing_codes": missing_codes}
        normalized_weights = {c: w / w_total for c, w in normalized_weights.items()}

    if nav_wide.empty or len(nav_wide) < 2:
        return {
            "error": "Not enough overlapping NAV history across the selected funds in this window. "
            "Widen the time horizon or pick funds with more common history."
        }

    actual_start = nav_wide.index[0].date()

    # One extra trading day just before the window starts -- see portfolio_sim.run_backtest's
    # own docstring for why (a valid first-day TWR return instead of losing that day).
    lookback_row = None
    df_lookback = db.get_nav_history_dataframe(
        list(normalized_weights.keys()),
        start_date=actual_start - datetime.timedelta(days=10),
        end_date=actual_start - datetime.timedelta(days=1),
    )
    if not df_lookback.empty:
        df_lookback = df_lookback.copy()
        df_lookback["nav_date"] = pd.to_datetime(df_lookback["nav_date"])
        lb_wide = (
            df_lookback.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last")
            .sort_index()
            .ffill()
            .reindex(columns=list(normalized_weights.keys()))
        )
        if not lb_wide.empty and not lb_wide.iloc[-1].isna().any():
            lookback_row = lb_wide.iloc[-1]

    result = portfolio_sim.run_backtest(
        nav_wide=nav_wide[list(normalized_weights.keys())],
        weights=normalized_weights,
        mode="SIP" if mode.startswith("SIP") else "Lump Sum",
        lookback_row=lookback_row,
        lump_sum_amount=float(lump_sum_amount),
        sip_amount=float(sip_amount),
        rebalance_freq=rebalance_freq,
    )
    if "error" in result:
        return result

    result["normalized_weights"] = normalized_weights
    result["missing_codes"] = missing_codes
    result["actual_start"] = actual_start
    return result
