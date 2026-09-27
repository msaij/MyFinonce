"""Portfolio backtest orchestration for Compare & Simulate: input validation, the common
data window, trade dates, the engine run (portfolio_sim.run_backtest), a benchmark run on
the same schedule, and the per-fund / calendar-year / warning read-outs around it.

Window. The simulation runs over the dates on which EVERY positive-weight fund has NAV
history inside the requested window: it starts at the youngest fund's first NAV and ends
at the earliest last NAV. Carrying a closed fund's last NAV forward (the old behaviour)
showed it as a flat, riskless asset for however long it had been gone; starting before a
fund existed is impossible. Both limits are reported, with the fund that set them.

Trade dates. Orders execute only on dates on which every fund published its own NAV; the
value series still runs on every date any fund priced (see portfolio_sim's docstring).
Before this, a SIP due on Saturday 1 June 2024 bought an equity fund at Friday's NAV next
to a liquid fund that priced Saturday -- and so pocketed Monday's +2.04% move (122639:
77.7696 on 31 May -> 79.3574 on 3 Jun) that no real investor could have bought before.

Dates. db.get_nav_history_dataframe() returns nav_date as plain datetime.date objects;
everything below converts to a real pandas DatetimeIndex first, because the engine's
schedule helpers use the vectorised .year/.month/.quarter accessors.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Optional

import pandas as pd

from app import portfolio_sim, quant_analytics
from app.classification import broad_category_of, classify, is_idcw, sebi_category
from app.db import backtest as bt_db
from app.db import holdings as hdb
from app.db import queries as db

MODE_LUMP_SUM = "Lump Sum"
MODE_SIP = "SIP (Monthly)"

# Stamp duty on mutual fund purchases (Indian Stamp Act amendment, effective 1 July 2020):
# 0.005% of the purchase amount, on purchases, SIP instalments and switch-ins. Same
# constants as the Holdings ledger (holdings_service.STAMP_DUTY_RATE / STAMP_DUTY_FROM).
STAMP_DUTY_RATE = 0.00005
STAMP_DUTY_FROM = datetime.date(2020, 7, 1)

RISK_FREE_RATE_ANN = 0.065  # the app-wide Sharpe/Sortino risk-free rate (quant_analytics default)
EXIT_LOAD_DAYS = 365
MAX_FUNDS = 20
MAX_EXIT_LOAD_PCT = 5.0
# A window start that moves by at most this many days is a weekend/holiday, not a fund
# that did not exist yet; the SIP calendar then still follows the requested start date.
HOLIDAY_SLACK_DAYS = 7
# A fund whose NAVs stop this many days before the requested end has stopped publishing
# (merged, wound up, or not yet synced) rather than just missing today's NAV.
STALE_END_DAYS = 7
# NAVs fetched before the window only to measure how often each fund prices (the
# annualisation base), never simulated.
FREQUENCY_LOOKBACK_DAYS = 370

BENCHMARK_NONE = "none"
BENCHMARK_CATEGORY = "category"
BENCHMARK_SCHEME = "scheme"


def _wide(df_long: pd.DataFrame) -> pd.DataFrame:
    if df_long.empty:
        return pd.DataFrame()
    df = df_long.copy()
    df["nav_date"] = pd.to_datetime(df["nav_date"])
    return df.pivot_table(index="nav_date", columns="scheme_code", values="nav", aggfunc="last").sort_index()


def _iso(d: Any) -> Optional[str]:
    if d is None:
        return None
    if isinstance(d, pd.Timestamp):
        d = d.date()
    return d.isoformat()


def _validate(scheme_codes, weights, mode, lump_sum_amount, sip_amount, rebalance_freq, start_date, end_date,
              sip_day, exit_load_pct) -> Optional[str]:
    if not scheme_codes:
        return "Select at least one fund."
    if len(set(scheme_codes)) > MAX_FUNDS:
        return f"At most {MAX_FUNDS} funds can be simulated together."
    if not (mode == MODE_LUMP_SUM or str(mode).startswith("SIP")):
        return f"Unknown investment mode {mode!r}."
    if rebalance_freq not in portfolio_sim.REBALANCE_CHOICES:
        return f"Unknown rebalancing frequency {rebalance_freq!r}."
    for c, w in weights.items():
        if w is None or not math.isfinite(float(w)) or float(w) < 0:
            return "Weights must be zero or positive numbers."
    amount = lump_sum_amount if mode == MODE_LUMP_SUM else sip_amount
    if amount is None or not math.isfinite(float(amount)) or float(amount) <= 0:
        return "Enter a positive investment amount."
    if start_date >= end_date:
        return "The time horizon's start date must be before its end date."
    if sip_day is not None and not (1 <= int(sip_day) <= 31):
        return "SIP day must be between 1 and 31."
    if not (0.0 <= float(exit_load_pct) <= MAX_EXIT_LOAD_PCT):
        return f"Exit load must be between 0% and {MAX_EXIT_LOAD_PCT:g}%."
    return None


def run_portfolio_backtest(
    scheme_codes: List[int],
    weights: Dict[int, float],
    mode: str,
    lump_sum_amount: float,
    sip_amount: float,
    rebalance_freq: str,
    start_date: datetime.date,
    end_date: datetime.date,
    sip_day: Optional[int] = None,
    apply_stamp_duty: bool = True,
    exit_load_pct: float = 0.0,
    benchmark: str = BENCHMARK_NONE,
    benchmark_code: Optional[int] = None,
) -> Dict[str, Any]:
    weights = {int(c): float(weights.get(c, weights.get(str(c), 0.0)) or 0.0) for c in scheme_codes}
    err = _validate(scheme_codes, weights, mode, lump_sum_amount, sip_amount, rebalance_freq, start_date, end_date,
                    sip_day, exit_load_pct)
    if err:
        return {"error": err}
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        return {"error": "At least one fund needs a positive weight."}
    # Zero-weight funds are left out before anything is loaded: otherwise a young fund the
    # user gave 0% could still cut the common window short.
    zero_weight_codes = [c for c, w in weights.items() if w <= 0]
    active = {c: w for c, w in weights.items() if w > 0}
    engine_mode = "SIP" if str(mode).startswith("SIP") else MODE_LUMP_SUM

    df_long = db.get_nav_history_dataframe(
        list(active), start_date=start_date - datetime.timedelta(days=FREQUENCY_LOOKBACK_DAYS), end_date=end_date
    )
    wide_all = _wide(df_long)
    if wide_all.empty:
        return {"error": "No historical NAV records for any selected fund in this time window.",
                "missing_codes": list(active)}
    window = wide_all[wide_all.index >= pd.Timestamp(start_date)]

    # --- Coverage and the common window ---------------------------------------------------
    coverage: Dict[int, Dict[str, Any]] = {}
    missing_codes = []
    for c in active:
        s = window[c].dropna() if c in window.columns else pd.Series(dtype=float)
        if s.empty:
            missing_codes.append(c)
            continue
        coverage[c] = {"first_nav_date": s.index[0], "last_nav_date": s.index[-1], "nav_count": int(len(s))}
    for c in missing_codes:
        active.pop(c)
    if not active:
        return {"error": "No selected fund has NAV history in this time window.", "missing_codes": missing_codes}
    total_w = sum(active.values())
    normalized_weights = {c: w / total_w for c, w in active.items()}
    codes = list(normalized_weights)

    eff_start = max(coverage[c]["first_nav_date"] for c in codes)
    eff_end = min(coverage[c]["last_nav_date"] for c in codes)
    start_limited_by = [c for c in codes if coverage[c]["first_nav_date"] == eff_start] \
        if (eff_start.date() - start_date).days > HOLIDAY_SLACK_DAYS else []
    latest_last = max(coverage[c]["last_nav_date"] for c in codes)
    end_limited_by = [c for c in codes if coverage[c]["last_nav_date"] == eff_end] \
        if (latest_last - eff_end).days > STALE_END_DAYS or (end_date - eff_end.date()).days > STALE_END_DAYS else []
    if eff_start >= eff_end:
        names = ", ".join(str(c) for c in codes)
        return {"error": "The selected funds have no NAV history in common inside this window "
                         f"(funds: {names}). Widen the time horizon or pick funds with overlapping history.",
                "missing_codes": missing_codes}

    nav_raw = wide_all[codes]
    nav_filled = nav_raw.ffill()
    in_window = (nav_filled.index >= eff_start) & (nav_filled.index <= eff_end)
    # Every date any selected fund priced, carried forward -- the valuation calendar.
    nav_win = nav_filled.loc[in_window & nav_raw.notna().any(axis=1).to_numpy()]
    trade_dates = nav_raw.loc[in_window].dropna().index
    # How often the simulated series (the union calendar) really ticks around this window --
    # the Compare tab's cadence_obs_per_year. The high quantile over the whole lookback
    # (infer_obs_per_year) remembered cadences the funds no longer have.
    obs_per_year = quant_analytics.cadence_obs_per_year(nav_raw.dropna(how="all").index, eff_start, eff_end)

    schedule_from = start_date if (eff_start.date() - start_date).days <= HOLIDAY_SLACK_DAYS else eff_start.date()
    resolved_sip_day = int(sip_day) if sip_day is not None else schedule_from.day
    duty = STAMP_DUTY_RATE if apply_stamp_duty else 0.0
    engine_kwargs = dict(
        sip_day=resolved_sip_day, schedule_from=schedule_from,
        stamp_duty_rate=duty, stamp_duty_from=STAMP_DUTY_FROM,
        exit_load_pct=float(exit_load_pct), exit_load_days=EXIT_LOAD_DAYS,
        risk_free_rate_ann=RISK_FREE_RATE_ANN,
    )

    result = portfolio_sim.run_backtest(
        nav_wide=nav_win, weights=normalized_weights, mode=engine_mode,
        lump_sum_amount=float(lump_sum_amount), sip_amount=float(sip_amount), rebalance_freq=rebalance_freq,
        trade_dates=trade_dates, obs_per_year=obs_per_year, **engine_kwargs,
    )
    if "error" in result:
        return {**result, "missing_codes": missing_codes}

    # --- Identity, TER, IDCW -------------------------------------------------------------
    meta = hdb.scheme_meta(list(scheme_codes) + ([benchmark_code] if benchmark_code else []))
    idcw_codes = [c for c in codes if is_idcw(meta.get(c, {}).get("option_type"), meta.get(c, {}).get("scheme_name"))]
    alternatives = bt_db.growth_alternatives(idcw_codes) if idcw_codes else {}

    def name_of(c: int) -> str:
        return meta[c]["display_name"] if c in meta else hdb.display_name({"scheme_code": c})

    fund_years = {c: {r["year"]: r for r in portfolio_sim.calendar_year_returns(nav_win[c].loc[nav_win.index >= pd.Timestamp(result["first_date"])])}
                  for c in codes}
    funds_out = []
    for f in result["funds"]:
        c = f["scheme_code"]
        m = meta.get(c, {})
        ter = m.get("expense_ratio")
        funds_out.append({
            **f,
            "display_name": name_of(c),
            "category": m.get("category"),
            "option_type": m.get("option_type"),
            "expense_ratio": None if ter is None or (isinstance(ter, float) and math.isnan(ter)) else float(ter),
            "ter_status": m.get("ter_status"),
            "is_idcw": c in idcw_codes,
            "growth_alternative": alternatives.get(c),
            "first_nav_date": _iso(coverage[c]["first_nav_date"]),
            "last_nav_date": _iso(coverage[c]["last_nav_date"]),
        })

    # --- Benchmark on the same schedule ----------------------------------------------------
    bench = _run_benchmark(
        benchmark, benchmark_code, codes, normalized_weights, meta, nav_win, trade_dates, wide_all.index,
        engine_mode, float(lump_sum_amount), float(sip_amount), rebalance_freq, engine_kwargs, obs_per_year,
        result["first_date"], start_date, end_date,
    )

    df_result = result.pop("df_result")
    for k in ("df_twr", "cash_flows", "contribution_dates"):
        result.pop(k, None)
    if bench.get("series") is not None:
        s = bench.pop("series")
        df_result = df_result.merge(s, on="nav_date", how="left")

    # --- Calendar years: portfolio TWR, each fund's NAV, benchmark TWR ---------------------
    bench_years = {r["year"]: r for r in bench.pop("calendar_years", [])}
    calendar_years = []
    for r in result.pop("calendar_years"):
        y = r["year"]
        portfolio_pct = r.pop("return_pct")
        calendar_years.append({
            **r,
            "portfolio_pct": portfolio_pct,
            "benchmark_pct": bench_years[y]["return_pct"] if y in bench_years else None,
            "funds": {str(c): fund_years[c][y]["return_pct"] if y in fund_years[c] else None for c in codes},
        })

    for t in result["trades"]:
        t["display_name"] = name_of(t["scheme_code"])

    warnings = _warnings(codes, coverage, start_limited_by, end_limited_by, eff_start, eff_end, start_date, end_date,
                         missing_codes, zero_weight_codes, idcw_codes, alternatives, name_of, result, bench)

    span = result["twr_metrics"].get("span_days")
    return {
        **result,
        "df_result": df_result,
        "funds": funds_out,
        "calendar_years": calendar_years,
        "benchmark": bench,
        "normalized_weights": normalized_weights,
        "missing_codes": missing_codes,
        "zero_weight_codes": zero_weight_codes,
        "actual_start": result["first_date"],
        "actual_end": result["last_date"],
        "window": {
            "requested_start": _iso(start_date),
            "requested_end": _iso(end_date),
            "data_start": _iso(eff_start),
            "data_end": _iso(eff_end),
            "simulated_start": _iso(result["first_date"]),
            "simulated_end": _iso(result["last_date"]),
            "span_days": span,
            "start_limited_by": start_limited_by,
            "end_limited_by": end_limited_by,
        },
        "assumptions": {
            "mode": MODE_SIP if engine_mode == "SIP" else MODE_LUMP_SUM,
            "sip_day": resolved_sip_day if engine_mode == "SIP" else None,
            "sip_day_defaulted": sip_day is None,
            "stamp_duty_applied": bool(apply_stamp_duty),
            "stamp_duty_rate_pct": STAMP_DUTY_RATE * 100.0,
            "stamp_duty_from": _iso(STAMP_DUTY_FROM),
            "exit_load_pct": float(exit_load_pct),
            "exit_load_days": EXIT_LOAD_DAYS,
            "risk_free_rate_pct": RISK_FREE_RATE_ANN * 100.0,
            "obs_per_year": round(float(obs_per_year), 2),
            "min_annualise_days": portfolio_sim.MIN_ANNUALISE_DAYS,
            "rebalance_freq": rebalance_freq,
            "tax_modelled": False,
        },
        "warnings": warnings,
    }


PEER_MAX_GAP_DAYS = 31


def _peer_coverage_gap(series: pd.Series, idx: pd.DatetimeIndex) -> Optional[str]:
    """Why a peer series cannot stand in for a fund over the whole simulated window, or None.
    Carrying a peer level across a hole would show the peers as a flat, riskless asset for
    that stretch (the same reason a closed fund is not carried forward)."""
    s = series.dropna()
    if s.empty:
        return "no peer series"
    if s.index[0] > idx[0] + pd.Timedelta(days=HOLIDAY_SLACK_DAYS):
        return f"peer series starts {_iso(s.index[0])}"
    if s.index[-1] < idx[-1] - pd.Timedelta(days=STALE_END_DAYS):
        return f"peer series ends {_iso(s.index[-1])}"
    inside = s[(s.index >= idx[0]) & (s.index <= idx[-1])].index
    if len(inside) > 1:
        gaps = pd.Series(inside[1:] - inside[:-1], index=inside[1:])
        if gaps.max() > pd.Timedelta(days=PEER_MAX_GAP_DAYS):
            at = gaps.idxmax()
            return f"no peer NAVs for {gaps.max().days} days before {_iso(at)}"
    return None


def _run_benchmark(kind, bench_code, codes, weights, meta, nav_win, trade_dates, all_index, engine_mode,
                   lump_sum_amount, sip_amount, rebalance_freq, engine_kwargs, obs_per_year, first_date,
                   start_date, end_date) -> Dict[str, Any]:
    """The same money, on the same dates, into a benchmark -- so the comparison is of the
    investments, not of the contribution schedule.

    "category": every fund is replaced by the equal-weighted average of the Direct-Growth
    schemes in its SEBI category AND asset class (quant_analytics.get_synthetic_category_
    benchmark with asset_class=classification.classify(...)) at the same weight and
    rebalancing. "Did my picks beat their peer groups?" A fund whose peer series is missing,
    starts after the window, stops before its end or has a hole of more than a month keeps
    its own NAV in that slot (with the reason), which is neutral: that slice can neither beat
    nor trail itself.
    "scheme": all of it into one scheme (an index fund, say), never rebalanced."""
    if kind not in (BENCHMARK_CATEGORY, BENCHMARK_SCHEME):
        return {"kind": BENCHMARK_NONE}
    idx = nav_win.index
    first_ts = pd.Timestamp(first_date)

    if kind == BENCHMARK_CATEGORY:
        peer = pd.DataFrame(index=idx)
        components, own_nav_slots = [], []
        cache: Dict[tuple, Optional[pd.Series]] = {}
        for c in codes:
            m = meta.get(c, {})
            cat = m.get("category")
            # Peers are the fund's SEBI category AND its asset class: "Index Funds", "Other
            # ETFs" and "FoF Domestic" each mix Nifty, gilt, Nasdaq, gold and silver schemes,
            # and a silver ETF's "peer average" was mostly equity ETFs.
            asset = classify(cat, broad_category_of(cat), m.get("scheme_name")) if cat else None
            series, gap_reason = None, None
            if cat:
                key = (sebi_category(cat), asset)
                if key not in cache:
                    # From a few days before the window, so the peer level on its first day is
                    # a real one rather than back-filled from a later date.
                    df = quant_analytics.get_synthetic_category_benchmark(
                        cat, idx[0].date() - datetime.timedelta(days=HOLIDAY_SLACK_DAYS), idx[-1].date(),
                        asset_class=asset)
                    cache[key] = None if df.empty else df.set_index("nav_date")["nav"].astype(float)
                series = cache[key]
            if series is None:
                gap_reason = "no peer series"
            else:
                gap_reason = _peer_coverage_gap(series, idx)
            if gap_reason is None:
                peer[c] = series.reindex(idx.union(series.index)).ffill().bfill().reindex(idx).to_numpy()
            else:
                peer[c] = nav_win[c].to_numpy()
                own_nav_slots.append(c)
            components.append({"scheme_code": c, "category": cat, "asset_class": asset, "weight_pct": weights[c] * 100.0,
                               "own_nav_used": c in own_nav_slots, "own_nav_reason": gap_reason})
        res = portfolio_sim.run_backtest(
            nav_wide=peer, weights=weights, mode=engine_mode, lump_sum_amount=lump_sum_amount,
            sip_amount=sip_amount, rebalance_freq=rebalance_freq, trade_dates=trade_dates,
            obs_per_year=obs_per_year, **engine_kwargs,
        )
        name = "Category peers (each fund's SEBI category and asset class, equal-weighted; same weights)"
        out: Dict[str, Any] = {"kind": BENCHMARK_CATEGORY, "name": name, "components": components}
    else:
        if not bench_code:
            return {"kind": BENCHMARK_SCHEME, "error": "Pick a benchmark scheme."}
        bench_code = int(bench_code)
        m = meta.get(bench_code)
        name = m["display_name"] if m else hdb.display_name({"scheme_code": bench_code})
        out = {"kind": BENCHMARK_SCHEME, "name": name, "scheme_code": bench_code,
               "is_idcw": bool(m) and is_idcw(m.get("option_type"), m.get("scheme_name"))}
        df_b = db.get_nav_history_dataframe([bench_code], start_date=idx[0].date() - datetime.timedelta(days=15),
                                            end_date=idx[-1].date())
        wb = _wide(df_b)
        if wb.empty or bench_code not in wb.columns:
            return {**out, "error": "The benchmark scheme has no NAV history in this window."}
        raw = wb[bench_code]
        real = raw[raw.index >= idx[0]].dropna()
        if real.empty or real.index[0] > first_ts + pd.Timedelta(days=HOLIDAY_SLACK_DAYS) \
                or real.index[-1] < idx[-1] - pd.Timedelta(days=STALE_END_DAYS):
            first = _iso(real.index[0]) if not real.empty else None
            last = _iso(real.index[-1]) if not real.empty else None
            return {**out, "error": f"The benchmark's NAV history ({first} to {last}) does not cover the simulated "
                                    f"window ({_iso(idx[0])} to {_iso(idx[-1])})."}
        filled = raw.reindex(idx.union(raw.index)).ffill().reindex(idx)
        b_trade = idx[idx.isin(real.index)]
        res = portfolio_sim.run_backtest(
            nav_wide=pd.DataFrame({bench_code: filled.to_numpy()}, index=idx), weights={bench_code: 1.0},
            mode=engine_mode, lump_sum_amount=lump_sum_amount, sip_amount=sip_amount,
            rebalance_freq=portfolio_sim.REBALANCE_NONE, trade_dates=b_trade, obs_per_year=obs_per_year,
            **engine_kwargs,
        )

    if "error" in res:
        return {**out, "error": res["error"]}
    df = res["df_result"]
    tm = res["twr_metrics"]
    return {
        **out,
        "final_value": res["final_value"],
        "total_invested": res["total_invested"],
        "absolute_gain": res["absolute_gain"],
        "money_weighted_xirr_pct": res["money_weighted_xirr_pct"],
        "xirr_note": res["xirr_note"],
        "twr_cagr_pct": tm.get("cagr_pct"),
        "twr_total_return_pct": tm.get("total_return_pct"),
        "vol_annualized_pct": tm.get("vol_annualized_pct"),
        "max_drawdown_pct": tm.get("max_drawdown_pct"),
        "sharpe_ratio": tm.get("sharpe_ratio"),
        "first_date": _iso(res["first_date"]),
        "calendar_years": res["calendar_years"],
        "series": pd.DataFrame({
            "nav_date": df["nav_date"],
            "benchmark_value": df["portfolio_value"],
            "benchmark_index": df["twr_index"],
            "benchmark_drawdown_pct": df["drawdown_pct"],
        }),
    }


def _warnings(codes, coverage, start_limited_by, end_limited_by, eff_start, eff_end, start_date, end_date,
              missing_codes, zero_weight_codes, idcw_codes, alternatives, name_of, result, bench) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if start_limited_by:
        who = "; ".join(name_of(c) for c in start_limited_by)
        first_trade = result.get("first_date")
        tail = (f" The first investment is allotted on {_iso(first_trade)}, the first scheduled date from then on."
                if first_trade is not None and first_trade != eff_start.date() else "")
        out.append({"code": "start_limited", "level": "warning",
                    "message": f"The simulation cannot start before {_iso(eff_start)} (you asked for {_iso(start_date)}): "
                               f"that is the first NAV of {who} in this window, and a portfolio cannot hold a fund "
                               f"before it exists.{tail}"})
    if end_limited_by:
        who = "; ".join(f"{name_of(c)} (last NAV {_iso(coverage[c]['last_nav_date'])})" for c in end_limited_by)
        out.append({"code": "end_limited", "level": "warning",
                    "message": f"The simulation ends on {_iso(eff_end)}, not {_iso(end_date)}, because NAVs stop there "
                               f"for {who}. A fund that stopped publishing (merged, wound up, or not yet synced) is not "
                               "carried forward as if its value had frozen."})
    if missing_codes:
        out.append({"code": "missing", "level": "warning",
                    "message": "No NAV history inside this window for " + "; ".join(name_of(c) for c in missing_codes)
                               + ". Left out; the remaining weights were scaled back up to 100%."})
    if zero_weight_codes:
        out.append({"code": "zero_weight", "level": "info",
                    "message": "Not simulated (0% weight): " + "; ".join(name_of(c) for c in zero_weight_codes) + "."})
    for c in idcw_codes:
        alt = alternatives.get(c)
        tail = (f" The Growth option of the same fund is {alt['display_name']}." if alt else "")
        out.append({"code": "idcw", "level": "warning",
                    "message": f"{name_of(c)} is an IDCW option. Its NAV drops by every payout, and this backtest does "
                               "not add the payouts back, so its return here is understated by everything it "
                               f"distributed.{tail}"})
    if result["twr_metrics"].get("annualise_withheld"):
        out.append({"code": "short_window", "level": "info",
                    "message": f"The simulated window is {result['twr_metrics'].get('span_days')} days. XIRR and CAGR are "
                               f"withheld under {portfolio_sim.MIN_ANNUALISE_DAYS} days, because annualising a few days "
                               "turns a small move into a huge rate; the plain (not annualised) returns are shown instead."})
    if bench.get("error"):
        out.append({"code": "benchmark", "level": "info", "message": f"Benchmark not shown: {bench['error']}"})
    elif bench.get("is_idcw"):
        out.append({"code": "benchmark_idcw", "level": "warning",
                    "message": "The benchmark scheme is an IDCW option, so its NAV return is understated by its payouts."})
    elif bench.get("kind") == BENCHMARK_CATEGORY and any(c.get("own_nav_used") for c in bench.get("components", [])):
        who = "; ".join(f"{name_of(c['scheme_code'])} ({c.get('own_nav_reason') or 'no peer series'})"
                        for c in bench["components"] if c.get("own_nav_used"))
        out.append({"code": "benchmark_partial", "level": "info",
                    "message": f"No category peer series covers the whole window for {who}; the fund's own NAV stands "
                               "in for its peers in the benchmark, so that slice is neutral."})
    return out
