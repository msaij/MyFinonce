"""Multi-fund portfolio backtesting engine: SIP/lump-sum contribution simulation with
optional periodic rebalancing, built entirely on official AMFI NAV history already in
Postgres. No forward-looking projection here — this is a historical backtest only.

Two distinct, complementary return figures are computed, deliberately kept separate
because they answer different questions and conflating them is a common source of
investor confusion:

- Money-weighted return (XIRR): what the actual investor experienced, given exactly when
  each contribution went in. This is the number a SIP investor cares about.
- Time-weighted return (TWR / CAGR): how the fund mix performed with the effect of the
  size and timing of contributions removed. It is the chain-linked index of the simulated
  portfolio itself -- the same GIPS construction the Holdings page uses
  (services/holdings_analytics.py): a day that carries a contribution is two sub-periods,

      r1_t = pre_t / V_{t-1} - 1          the existing units, repriced at today's NAV
      r2_t = V_t / (pre_t + F_t) - 1      the day's purchase/rebalance frictions
      index_t = index_{t-1} * (1 + r1_t) * (1 + r2_t)

  so a contribution never shows up as a "return", while stamp duty and exit loads do.
  The index is handed to quant_analytics.compute_risk_adjusted_metrics so Sharpe/Vol/
  Max-Drawdown use the exact, already-tested formulas of the single-fund Quant page.

  (Until 2026-09 the TWR was a separate synthetic Rs 100 index that ignored contributions
  entirely. For a lump sum the two are identical; for a SIP the chain-linked index is the
  honest one, because each instalment bought at target weights really does pull the
  portfolio back towards target, and the old index did not see that.)

Trading rules (these are what an AMC actually does, and each one was a bug before):

- Orders execute only on `trade_dates` -- dates on which EVERY fund in the portfolio
  published its own NAV. The value series still runs on every date any fund published
  (a liquid fund prices weekends), with the others carried forward, but a purchase or a
  switch is never allotted at a carried-forward NAV. A SIP date that falls on a weekend or
  market holiday is allotted at the next trade date's NAV, as it would be in reality.
- SIP instalments fall on `sip_day` of every month (clamped to the month's last day).
- Stamp duty (`stamp_duty_rate`) comes out of every purchase on or after
  `stamp_duty_from`, contributions and rebalancing switch-ins alike, so fewer units are
  allotted -- the same rule the Holdings ledger applies.
- Rebalancing sells the overweight funds first-in-first-out; an optional exit load
  (`exit_load_pct`) is charged on units held under `exit_load_days`, and the proceeds,
  not the gross value sold, buy the underweight funds. Capital-gains tax is NOT modelled.
"""

import calendar
import collections
import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from app import quant_analytics

REBALANCE_NONE = "None"
REBALANCE_MONTHLY = "Monthly"
REBALANCE_QUARTERLY = "Quarterly"
REBALANCE_ANNUALLY = "Annually"
REBALANCE_CHOICES = (REBALANCE_NONE, REBALANCE_MONTHLY, REBALANCE_QUARTERLY, REBALANCE_ANNUALLY)

# XIRR/CAGR annualise; over a few days that turns a 1% move into a three-digit rate. Same
# floor as the Holdings page (holdings_service.MIN_XIRR_DAYS).
MIN_ANNUALISE_DAYS = 30

# Rebalancing legs smaller than this many rupees are floating-point noise, not trades.
TRADE_EPSILON = 0.005


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


def xirr_pct_or_reason(
    cash_flows: List[Tuple[datetime.date, float]], min_days: int = MIN_ANNUALISE_DAYS
) -> Tuple[Optional[float], Optional[str]]:
    """(annualised %, None) or (None, reason) -- "too_short" under `min_days` between the
    first and last flow, "no_solution" when the solver finds no sane rate."""
    dated = [d for d, cf in cash_flows if cf != 0]
    if len(dated) < 2:
        return None, "no_solution"
    if (max(dated) - min(dated)).days < min_days:
        return None, "too_short"
    rate = xirr(sorted(cash_flows, key=lambda x: x[0]))
    return (None, "no_solution") if rate is None else (rate * 100.0, None)


# --- Schedules ---------------------------------------------------------------------------


def period_starts(trade_dates: pd.DatetimeIndex, freq: str) -> set:
    """First trade date of every month / quarter / year present in `trade_dates`."""
    td = pd.DatetimeIndex(trade_dates)
    if td.empty:
        return set()
    if freq == REBALANCE_MONTHLY:
        keys = [td.year, td.month]
    elif freq == REBALANCE_QUARTERLY:
        keys = [td.year, td.quarter]
    elif freq == REBALANCE_ANNUALLY:
        keys = [td.year]
    else:
        return set()
    s = pd.Series(td, index=td)
    return set(s.groupby(keys).min())


def sip_schedule(
    trade_dates: pd.DatetimeIndex,
    sip_day: Optional[int] = None,
    schedule_from: Optional[datetime.date] = None,
    end: Optional[datetime.date] = None,
) -> List[pd.Timestamp]:
    """Allotment dates of a monthly SIP.

    With `sip_day`, the instalment is due on that day of every month from `schedule_from`
    (day clamped to the month's length, so 31 means "month end") and is allotted on the
    first trade date on or after the due date -- next business day, as an AMC does. With
    no `sip_day`, it is simply the first trade date of every month (the pre-2026-09
    behaviour, kept for callers that pass none)."""
    td = pd.DatetimeIndex(sorted(pd.DatetimeIndex(trade_dates)))
    if td.empty:
        return []
    lo = pd.Timestamp(schedule_from) if schedule_from is not None else td[0]
    hi = pd.Timestamp(end) if end is not None else td[-1]
    if sip_day is None:
        in_range = td[(td >= lo) & (td <= hi)]
        return sorted(period_starts(in_range, REBALANCE_MONTHLY))

    out: List[pd.Timestamp] = []
    y, m = lo.year, lo.month
    while (y, m) <= (hi.year, hi.month):
        due = pd.Timestamp(y, m, min(int(sip_day), calendar.monthrange(y, m)[1]))
        if lo.normalize() <= due <= hi:
            pos = td.searchsorted(due, side="left")
            if pos < len(td) and td[pos] <= hi and (not out or td[pos] > out[-1]):
                out.append(td[pos])
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _consume_fifo(lots: "collections.deque", units: float, today: pd.Timestamp, young_days: int) -> float:
    """Removes `units` from the oldest lots first; returns how many of them had been held
    for fewer than `young_days` calendar days (the part an exit load would apply to)."""
    young, remaining = 0.0, units
    while remaining > 1e-12 and lots:
        lot = lots[0]
        take = min(lot[1], remaining)
        if (today - lot[0]).days < young_days:
            young += take
        lot[1] -= take
        remaining -= take
        if lot[1] <= 1e-12:
            lots.popleft()
    return young


def calendar_year_returns(levels: pd.Series) -> List[Dict[str, Any]]:
    """Point-to-point return of a level series (an index or a NAV) for every calendar year
    it touches. A year's base is the last level of the previous year when the series has
    one, otherwise its own first level; `partial` marks a year the series does not span
    end to end (it starts after the first week of January or ends before 24 December)."""
    s = levels.dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if len(s) < 2:
        return []
    out = []
    years = sorted(set(s.index.year))
    for y in years:
        in_year = s[s.index.year == y]
        before = s[s.index.year < y]
        if before.empty:
            base_date, base = in_year.index[0], float(in_year.iloc[0])
            starts_late = in_year.index[0] > pd.Timestamp(y, 1, 7)
        else:
            base_date, base = before.index[-1], float(before.iloc[-1])
            starts_late = False
        end_date, end_level = in_year.index[-1], float(in_year.iloc[-1])
        if base <= 0 or end_date <= base_date:
            continue
        out.append({
            "year": int(y),
            "return_pct": (end_level / base - 1.0) * 100.0,
            "from": base_date.strftime("%Y-%m-%d"),
            "to": end_date.strftime("%Y-%m-%d"),
            "partial": bool(starts_late or end_date < pd.Timestamp(y, 12, 24)),
        })
    return out


# --- Engine ------------------------------------------------------------------------------


def run_backtest(
    nav_wide: pd.DataFrame,
    weights: Dict[int, float],
    mode: str,
    lump_sum_amount: float,
    sip_amount: float,
    rebalance_freq: str,
    *,
    trade_dates: Optional[Sequence] = None,
    sip_day: Optional[int] = None,
    schedule_from: Optional[datetime.date] = None,
    stamp_duty_rate: float = 0.0,
    stamp_duty_from: Optional[datetime.date] = None,
    exit_load_pct: float = 0.0,
    exit_load_days: int = 365,
    obs_per_year: Optional[float] = None,
    risk_free_rate_ann: float = 0.065,
    min_annualise_days: int = MIN_ANNUALISE_DAYS,
) -> Dict[str, Any]:
    """
    nav_wide: DataFrame indexed by date (ascending), one column per scheme_code, values =
    NAV, already forward-filled by the caller and with no leading gaps.
    weights: {scheme_code: target_weight_fraction}, must sum to ~1.0.
    mode: "Lump Sum" or "SIP".
    trade_dates: dates on which orders may execute (default: every row). See module doc.
    Returns a dict with the portfolio value series, the TWR index, per-fund results, the
    trade list and headline metrics -- or {"error": "..."} if it cannot simulate.
    """
    if nav_wide.empty or len(nav_wide) < 2:
        return {"error": "Not enough overlapping NAV history for the selected funds in this window."}

    nav_wide = nav_wide.sort_index()
    nav_wide.index = pd.DatetimeIndex(pd.to_datetime(nav_wide.index))
    codes = list(weights.keys())
    w = np.array([float(weights[c]) for c in codes])
    amount = float(sip_amount if mode == "SIP" else lump_sum_amount)
    if not np.isfinite(amount) or amount <= 0:
        return {"error": "Enter a positive investment amount."}

    all_dates = nav_wide.index
    if trade_dates is None:
        tradable = all_dates
    else:
        tradable = pd.DatetimeIndex(sorted(set(pd.to_datetime(list(trade_dates))) & set(all_dates)))
    if schedule_from is not None:
        tradable = tradable[tradable >= pd.Timestamp(schedule_from)]
    if tradable.empty:
        return {"error": "No date in this window on which every selected fund published a NAV."}

    if mode == "SIP":
        contrib_dates = sip_schedule(tradable, sip_day, schedule_from, all_dates[-1].date())
    else:
        contrib_dates = [tradable[0]]
    if not contrib_dates:
        return {"error": "No SIP instalment falls inside this window. Widen the time horizon."}

    start = contrib_dates[0]
    nav = nav_wide.loc[nav_wide.index >= start, codes]
    if len(nav) < 2:
        return {"error": "Not enough NAV history after the first investment date to simulate."}
    if nav.isna().any().any():
        return {"error": "A selected fund has no NAV at the start of the simulated window."}
    tradable = tradable[tradable >= start]
    contrib_set = set(contrib_dates)
    rebalance_set = period_starts(tradable, rebalance_freq) - {start}

    arr = nav.to_numpy(dtype=float)
    n_codes = len(codes)
    units = np.zeros(n_codes)
    lots = [collections.deque() for _ in codes]
    kind = "SIP" if mode == "SIP" else "Lump sum"

    values: List[float] = []
    invested_series: List[float] = []
    holdings = np.zeros((len(nav), n_codes))
    index_levels: List[float] = []
    level = 100.0
    cash_flows: List[Tuple[datetime.date, float]] = []
    fund_flows: Dict[Any, List[Tuple[datetime.date, float]]] = {c: [] for c in codes}
    contributed = np.zeros(n_codes)
    bought_rebal = np.zeros(n_codes)
    sold_rebal = np.zeros(n_codes)
    trades: List[Dict[str, Any]] = []
    total_invested = stamp_total = exit_load_total = turnover = sold_young = 0.0
    n_rebalances = 0

    for i, dt in enumerate(nav.index):
        p = arr[i]
        d = dt.date()
        duty = stamp_duty_rate if (stamp_duty_from is None or d >= stamp_duty_from) else 0.0
        pre = float(units @ p)
        flow = 0.0

        if dt in contrib_set:
            for j, c in enumerate(codes):
                gross = amount * w[j]
                if gross <= 0:
                    continue
                net = gross / (1.0 + duty)
                u = net / p[j]
                units[j] += u
                lots[j].append([dt, u])
                contributed[j] += gross
                stamp_total += gross - net
                fund_flows[c].append((d, -gross))
                trades.append({"date": d, "type": kind, "scheme_code": c, "amount": gross, "nav": p[j],
                               "units": u, "stamp_duty": gross - net, "exit_load": 0.0})
            total_invested += amount
            cash_flows.append((d, -amount))
            flow = amount

        val = float(units @ p)
        if dt in rebalance_set and val > 0:
            diff = units * p - val * w
            sells = [j for j in range(n_codes) if diff[j] > TRADE_EPSILON]
            buys = [j for j in range(n_codes) if diff[j] < -TRADE_EPSILON]
            if sells and buys:
                proceeds_total = 0.0
                for j in sells:
                    gross = float(diff[j])
                    u = gross / p[j]
                    young_units = _consume_fifo(lots[j], u, dt, exit_load_days)
                    load = (exit_load_pct / 100.0) * young_units * p[j]
                    units[j] -= u
                    proceeds_total += gross - load
                    sold_rebal[j] += gross - load
                    exit_load_total += load
                    sold_young += young_units * p[j]
                    turnover += gross
                    fund_flows[codes[j]].append((d, gross - load))
                    trades.append({"date": d, "type": "Rebalance sell", "scheme_code": codes[j], "amount": -gross,
                                   "nav": p[j], "units": -u, "stamp_duty": 0.0, "exit_load": load,
                                   "held_under_exit_load_days": young_units * p[j]})
                deficit_total = float(-sum(diff[j] for j in buys))
                for j in buys:
                    gross = float(-diff[j]) * proceeds_total / deficit_total
                    net = gross / (1.0 + duty)
                    u = net / p[j]
                    units[j] += u
                    lots[j].append([dt, u])
                    bought_rebal[j] += gross
                    stamp_total += gross - net
                    fund_flows[codes[j]].append((d, -gross))
                    trades.append({"date": d, "type": "Rebalance buy", "scheme_code": codes[j], "amount": gross,
                                   "nav": p[j], "units": u, "stamp_duty": gross - net, "exit_load": 0.0})
                n_rebalances += 1
                val = float(units @ p)

        prev = values[-1] if values else 0.0
        r1 = pre / prev - 1.0 if prev > 0 else 0.0
        r2 = val / (pre + flow) - 1.0 if (pre + flow) > 0 else 0.0
        level *= (1.0 + r1) * (1.0 + r2)

        holdings[i] = units * p
        values.append(val)
        invested_series.append(total_invested)
        index_levels.append(level)

    dates = nav.index
    end_d = dates[-1].date()
    final_value = values[-1]
    final_holdings = holdings[-1]

    # --- Time-weighted index -> risk metrics --------------------------------------------
    # A seed row at 100 the day before the first investment gives day one its own return
    # (the entry stamp duty), so the CAGR runs from the pre-investment level exactly like
    # the XIRR does; it is dropped again before anything is reported.
    seed = pd.DataFrame({"nav_date": [dates[0] - pd.Timedelta(days=1)], "nav": [100.0]})
    df_seeded = pd.concat([seed, pd.DataFrame({"nav_date": dates, "nav": index_levels})], ignore_index=True)
    df_seeded = quant_analytics.compute_daily_returns(df_seeded)
    # Drawdown from the post-entry level (the entry stamp duty is a cost, not a fall).
    peak = df_seeded["nav"].iloc[1:].cummax()
    df_seeded["peak_nav"] = peak.reindex(df_seeded.index).fillna(df_seeded["nav"])
    df_seeded["drawdown"] = df_seeded["nav"] / df_seeded["peak_nav"] - 1.0
    df_seeded.loc[0, "drawdown"] = 0.0
    df_seeded["drawdown_pct"] = df_seeded["drawdown"] * 100.0
    opy = obs_per_year if obs_per_year else quant_analytics.infer_obs_per_year(dates)
    quant_analytics.with_obs_per_year(df_seeded, opy)
    df_twr = df_seeded.iloc[1:].reset_index(drop=True)
    quant_analytics.with_obs_per_year(df_twr, opy)
    # The metrics get the seed row too: its daily_return is NaN, so it is not a return, but
    # it gives day one's return its real one-day span. Without it that span was unknown and
    # Rf / expected growth for it fell back to a guess derived from obs_per_year.
    twr_metrics = quant_analytics.compute_risk_adjusted_metrics(df_seeded, risk_free_rate_ann) if len(df_twr) >= 3 else {}
    twr_metrics = dict(twr_metrics or {})
    span_days = (end_d - dates[0].date()).days
    twr_total_pct = (index_levels[-1] / 100.0 - 1.0) * 100.0
    annualise_withheld = span_days < min_annualise_days
    if twr_metrics:
        # The metrics' own total return starts at day one's post-entry level; report the one
        # measured from the seed (100), which includes the entry costs, like the XIRR.
        twr_metrics["total_return_pct"] = twr_total_pct
        # Recompute the CAGR from the seed too, for the same reason.
        if span_days > 0:
            twr_metrics["cagr_pct"] = quant_analytics.calendar_day_cagr(twr_total_pct / 100.0, dates[0], dates[-1]) * 100.0
        if annualise_withheld:
            for k in ("cagr_pct", "sortino_ratio", "calmar_ratio"):
                twr_metrics[k] = None
    twr_metrics["annualise_withheld"] = annualise_withheld
    twr_metrics["span_days"] = span_days
    twr_metrics["risk_free_rate_pct"] = risk_free_rate_ann * 100.0

    xirr_pct, xirr_note = xirr_pct_or_reason(cash_flows + [(end_d, final_value)], min_annualise_days)

    # --- Per fund -----------------------------------------------------------------------
    funds = []
    total_gain = final_value - total_invested
    for j, c in enumerate(codes):
        net_in = contributed[j] + bought_rebal[j] - sold_rebal[j]
        gain = final_holdings[j] - net_in
        f_xirr, f_note = xirr_pct_or_reason(fund_flows[c] + [(end_d, float(final_holdings[j]))], min_annualise_days)
        funds.append({
            "scheme_code": c,
            "target_weight_pct": w[j] * 100.0,
            "final_weight_pct": (final_holdings[j] / final_value * 100.0) if final_value > 0 else None,
            "contributed": contributed[j],
            "rebalance_bought": bought_rebal[j],
            "rebalance_sold": sold_rebal[j],
            "net_invested": net_in,
            "final_value": final_holdings[j],
            "units": units[j],
            "gain": gain,
            "gain_share_pct": (gain / total_gain * 100.0) if abs(total_gain) > 1e-9 else None,
            "xirr_pct": f_xirr,
            "xirr_note": f_note,
            "nav_start": arr[0][j],
            "nav_end": arr[-1][j],
            "nav_return_pct": (arr[-1][j] / arr[0][j] - 1.0) * 100.0,
        })

    df_result = pd.DataFrame({
        "nav_date": dates,
        "portfolio_value": values,
        "total_invested": invested_series,
        "twr_index": index_levels,
        "drawdown_pct": df_twr["drawdown_pct"].to_numpy(),
    })
    for j, c in enumerate(codes):
        df_result[f"holding_{c}"] = holdings[:, j]

    return {
        "df_result": df_result,
        "df_twr": df_twr,
        "final_value": final_value,
        "total_invested": total_invested,
        "absolute_gain": total_gain,
        "absolute_return_pct": ((final_value / total_invested) - 1.0) * 100.0 if total_invested > 0 else None,
        "money_weighted_xirr_pct": xirr_pct,
        "xirr_note": xirr_note,
        "twr_metrics": {
            **twr_metrics,
            "costs_model": "nav_net_of_ter_plus_stamp_duty_and_optional_exit_load_no_tax",
        },
        "n_contributions": len(cash_flows),
        "contribution_dates": [c.date() for c in contrib_dates],
        "first_date": dates[0].date(),
        "last_date": end_d,
        "codes": codes,
        "funds": funds,
        "trades": trades,
        "cash_flows": cash_flows,
        "costs": {
            "stamp_duty": stamp_total,
            "exit_load": exit_load_total,
            "rebalance_count": n_rebalances,
            "rebalance_turnover": turnover,
            "rebalance_sold_under_exit_load_days": sold_young,
        },
        "calendar_years": calendar_year_returns(pd.Series(index_levels, index=dates)),
    }
