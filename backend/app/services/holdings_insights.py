"""Holdings Phase 4: deterministic insights and in-app alerts.

Every insight is a rule over numbers this app already computes, and says which
numbers it used -- the same "explainable, never a black box" stance as
quant_intelligence.py. They are observations, not recommendations: nothing here
says buy or sell, and the page shows the non-advice disclaimer beside them.

Alerts reuse the same detectors on a schedule (after every successful NAV sync once its
summary rebuild is done, see amfi_sync) and persist once per condition per period via
holding_alerts.dedupe_key, so a condition that stays true does not re-fire daily.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from app.db import holdings as hdb
from app.services import holdings_analytics as ha
from app.services import holdings_risk as risk_svc
from app.services import holdings_service as svc
from app.services.holdings_config import STALE_NAV_ALERT_DAYS

logger = logging.getLogger("holdings_insights")

CONCENTRATION_TOP1_PCT = 30.0
CONCENTRATION_TOP2_PCT = 50.0
BOTTOM_QUARTILE = 25.0
#: A category needs this many active peers before a percentile rank in it means anything.
MIN_PEERS = 8
MANY_FUNDS = 12
DEEP_DRAWDOWN_PCT = -10.0

SEVERITY_ORDER = {"danger": 0, "warning": 1, "info": 2}


def _insight(kind: str, severity: str, title: str, detail: str, codes: Optional[List[int]] = None, **numbers) -> Dict[str, Any]:
    return {"kind": kind, "severity": severity, "title": title, "detail": detail,
            "scheme_codes": codes or [], "numbers": numbers}


def _fmt_inr(v: float) -> str:
    return f"₹{v:,.0f}"


# --- Detectors (each returns a list of insights) -----------------------------------------

def regular_plan_costs(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Regular plans vs their Direct twin's TER. Uses only TERs we actually hold;
    a missing TER is stated, never estimated."""
    from app.services import plan_matcher

    out = []
    regulars = [p for p in positions if p["flags"]["regular_plan"]]
    if not regulars:
        return out
    twins = {p["scheme_code"]: plan_matcher.find_paired_scheme(p["scheme_code"]) for p in regulars}
    twin_meta = hdb.scheme_meta([c for c in twins.values() if c])
    total_cost, priced, unpriced = 0.0, [], []
    for p in regulars:
        twin = twins.get(p["scheme_code"])
        reg_ter = p.get("expense_ratio")
        dir_ter = twin_meta.get(twin, {}).get("expense_ratio") if twin else None
        if twin and reg_ter is not None and dir_ter is not None and reg_ter == reg_ter and dir_ter == dir_ter:
            gap = float(reg_ter) - float(dir_ter)
            cost = p["current_value"] * gap / 100.0
            total_cost += max(cost, 0.0)
            priced.append({"scheme_code": p["scheme_code"], "scheme_name": p["display_name"], "direct_code": twin,
                           "direct_name": twin_meta[twin]["display_name"], "ter_gap_pct": gap, "annual_cost": cost})
        else:
            unpriced.append(p)
    if priced:
        names = ", ".join(x["scheme_name"] for x in priced[:3]) + ("…" if len(priced) > 3 else "")
        out.append(_insight(
            "regular_plan_cost", "warning",
            f"{len(priced)} Regular plan{'s' if len(priced) > 1 else ''} cost about {_fmt_inr(total_cost)} a year more than Direct",
            f"At today's value, the expense-ratio gap to each fund's Direct plan works out to {_fmt_inr(total_cost)} a year ({names}). "
            "The gap is the distributor commission built into Regular plans. Switching is a redemption, so exit loads and capital-gains tax may apply.",
            [x["scheme_code"] for x in priced], annual_cost=total_cost, funds=priced,
        ))
    if unpriced:
        out.append(_insight(
            "regular_plan_unpriced", "info",
            f"{len(unpriced)} Regular plan{'s' if len(unpriced) > 1 else ''} without a comparable Direct TER",
            "We couldn't find an official TER for both the Regular plan and its Direct twin, so we don't estimate the cost: "
            + ", ".join(p["display_name"] for p in unpriced[:3]) + ".",
            [p["scheme_code"] for p in unpriced],
        ))
    return out


def concentration(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Concentration among MARKET-LINKED money only. A large slice in a liquid,
    overnight or arbitrage fund is parking, not a bet on one manager, so cash
    equivalents are left out (their weights are re-based over what remains)."""
    at_risk = [p for p in positions if p.get("current_value", 0) > 0
               and ha.classify(p.get("category"), p.get("broad_category"), p.get("scheme_name")) != "Cash & Liquid"]
    total = sum(p["current_value"] for p in at_risk)
    w = sorted(({**p, "risk_weight": p["current_value"] / total * 100.0} for p in at_risk), key=lambda p: -p["risk_weight"])
    out = []
    if len(w) >= 2:
        top1, top2 = w[0]["risk_weight"], w[0]["risk_weight"] + w[1]["risk_weight"]
        if top1 >= CONCENTRATION_TOP1_PCT or top2 >= CONCENTRATION_TOP2_PCT:
            out.append(_insight(
                "concentration", "info",
                f"Your top {'fund is' if top1 >= CONCENTRATION_TOP1_PCT else 'two funds are'} "
                f"{top1 if top1 >= CONCENTRATION_TOP1_PCT else top2:.0f}% of your market-linked money",
                f"{w[0]['display_name']} ({top1:.1f}%) and {w[1]['display_name']} ({w[1]['risk_weight']:.1f}%), excluding "
                "liquid, overnight and arbitrage funds. Concentration isn't wrong in itself, but one fund's manager or "
                "mandate then drives most of the outcome.",
                [w[0]["scheme_code"], w[1]["scheme_code"]], top1_pct=top1, top2_pct=top2,
            ))
    if len(w) >= MANY_FUNDS:
        out.append(_insight(
            "many_funds", "info", f"{len(w)} funds held",
            "Beyond roughly a dozen funds, extra funds mostly overlap. See Risk, where 'effective independent bets' shows how many distinct exposures you really have.",
            fund_count=len(w),
        ))
    return out


def peer_ranking(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    codes = [p["scheme_code"] for p in positions]
    ranks3 = hdb.category_percentiles(codes, "return_3y_pct")
    ranks1 = hdb.category_percentiles(codes, "return_1y_pct")
    out = []
    for p in positions:
        r, horizon = (ranks3.get(p["scheme_code"]), "3-year") if p["scheme_code"] in ranks3 else (ranks1.get(p["scheme_code"]), "1-year")
        if not r or r["peers"] < MIN_PEERS:
            continue
        if r["percentile"] <= BOTTOM_QUARTILE:
            out.append(_insight(
                "bottom_quartile", "warning",
                f"{p['display_name']} is in the bottom quarter of its category",
                f"Its {horizon} return of {r['value']:+.2f}% ranks at the {r['percentile']:.0f}th percentile among {r['peers']} active "
                f"{p.get('plan_type') or ''} {svc.sebi_category(p.get('category')) or ''} schemes. One period says little about skill; worth a look, not a verdict.",
                [p["scheme_code"]], percentile=r["percentile"], peers=r["peers"], horizon=horizon,
            ))
    return out


def stale_navs(positions: List[Dict[str, Any]], as_of: Optional[datetime.date], days: int = STALE_NAV_ALERT_DAYS) -> List[Dict[str, Any]]:
    """Holdings whose last NAV is `days`+ older than the view's latest NAV date
    (or that summary_table already marks inactive)."""
    out = []
    ref = as_of or datetime.date.today()
    for p in positions:
        d = p.get("latest_date")
        if d is None:
            continue
        age = (ref - d).days
        if age >= days or p["flags"]["stale_nav"]:
            out.append(_insight(
                "stale_nav", "danger",
                f"{p['display_name']} hasn't published a NAV since {d.isoformat()}",
                f"{age} days without a NAV. The scheme may have been merged, wound up or renamed to a new AMFI code. "
                "Your value for it is frozen at the last NAV. Check your latest statement.",
                [p["scheme_code"]], days=age, latest_date=d.isoformat(),
            ))
    return out


def realised_drawdown(r: Dict[str, Any]) -> Optional[float]:
    """Current drawdown from the portfolio's ACTUAL track record, or None. Never from
    the current-mix replay a young portfolio's Risk tab falls back to: a drawdown
    that never happened to you must not raise an insight or an alert."""
    if r.get("basis") != "realised":
        return None
    return (r.get("metrics") or {}).get("current_drawdown_pct")


def risk_signals(pid: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    r = risk_svc.risk(pid)
    if r.get("empty") or r.get("insufficient"):
        return out
    dd = realised_drawdown(r)
    if dd is not None and dd <= DEEP_DRAWDOWN_PCT:
        out.append(_insight(
            "drawdown", "info", f"Portfolio is {abs(dd):.1f}% below its peak",
            f"Time-weighted, from the highest point in the last {r['window']['n_trading_days']} trading days. "
            f"The deepest fall in that window was {abs(r['metrics']['max_drawdown_pct']):.1f}%.",
            current_drawdown_pct=dd,
        ))
    for pair in (r.get("holdings") or {}).get("redundant_pairs") or []:
        out.append(_insight(
            "overlap", "info", f"{pair['a_name']} and {pair['b_name']} move almost identically",
            f"Both are in the {pair['category']} category, with a daily-return correlation of {pair['correlation']:.2f}. "
            "Holding both adds paperwork, not diversification.",
            [pair["a"], pair["b"]], correlation=pair["correlation"],
        ))
    return out


# --- Public -----------------------------------------------------------------------------------

TER_SEVERITY = {"high": "warning", "inconsistent": "warning", "low": "info", "unverified": "info", "missing": "info"}


def ter_quality(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Data quality: expense ratios that look wrong or unverified, judged against
    the fund's own category (svc.ter_context). A TER that is simply high but in
    line with its peers is not flagged -- that's the category, not bad data."""
    ctx = svc.ter_context(hdb.scheme_meta([p["scheme_code"] for p in positions]))
    names = {p["scheme_code"]: p["display_name"] for p in positions}
    return [
        _insight("ter_check", TER_SEVERITY[c["status"]], f"Check {names[code]}'s expense ratio",
                 c["reason"] + (f" Source date: {c['as_of']}." if c.get("as_of") else ""),
                 [code], status=c["status"], ter_pct=c["ter_pct"], category_spread=c["category_spread"])
        for code, c in ctx.items() if c["status"] in TER_SEVERITY
    ]


def insights(pid: str) -> Dict[str, Any]:
    """The insights for a view, plus `unavailable`: the checks that could not run this time
    (so a missing drawdown note means "no deep drawdown", never "the check broke")."""
    summ = svc.summary(pid)
    positions = [p for p in summ["positions"] if not p["is_closed"]]
    if not positions:
        return {"insights": [], "as_of": summ["as_of"], "unavailable": []}
    unavailable: List[str] = []
    try:
        risk_items = risk_signals(pid)
    except Exception:
        logger.exception("risk() failed while building insights")
        risk_items, unavailable = [], ["drawdown", "overlap"]
    items = (
        stale_navs(positions, summ["as_of"])
        + ter_quality(positions)
        + regular_plan_costs(positions)
        + peer_ranking(positions)
        + concentration(positions)
        + risk_items
    )
    items.sort(key=lambda i: SEVERITY_ORDER.get(i["severity"], 9))
    return {"insights": items, "as_of": summ["as_of"], "unavailable": unavailable}


# The kinds a rule can have. The table still allows "drift" (an old backup may restore
# one), but drift needs per-portfolio targets that nothing can set, so it never fires.
RULE_KINDS = {
    "drawdown": "Portfolio falls this % below its peak",
    "stale_nav": "A holding stops publishing NAVs",
    "regular_plan": "A Regular plan is held",
}

#: The alert level when a drawdown rule has no threshold of its own.
DEFAULT_DRAWDOWN_ALERT_PCT = abs(DEEP_DRAWDOWN_PCT)


def _threshold(rule: Dict[str, Any], default: float) -> float:
    """The rule's threshold, or `default` when it has none. Zero counts as none: a drawdown
    of 0% or a NAV 0 days old is every day, which no one means to be alerted about."""
    t = rule.get("threshold")
    return float(t) if t is not None and float(t) > 0 else default


def evaluate_rule(rule: Dict[str, Any]) -> int:
    """Fires (idempotently) every current occurrence of one rule. Returns new alerts."""
    if not rule["active"]:
        return 0
    pid = str(rule["portfolio_id"]) if rule["portfolio_id"] else "all"
    try:
        summ = svc.summary(pid)
    except LookupError:
        return 0
    positions = [p for p in summ["positions"] if not p["is_closed"]]
    if not positions:
        return 0
    period = datetime.date.today().strftime("%Y-%m")
    fired = 0
    kind = rule["kind"]
    if kind == "drawdown":
        threshold = -abs(_threshold(rule, DEFAULT_DRAWDOWN_ALERT_PCT))
        dd = realised_drawdown(risk_svc.risk(pid))
        if dd is not None and dd <= threshold:
            fired += hdb.fire_alert(rule["id"], f"drawdown:{threshold}:{period}", "warning",
                                    f"Portfolio is {abs(dd):.1f}% below its peak (alert at {abs(threshold):.0f}%)")
    elif kind == "stale_nav":
        for i in stale_navs(positions, summ["as_of"], int(_threshold(rule, STALE_NAV_ALERT_DAYS))):
            code, since = i["scheme_codes"][0], i["numbers"]["latest_date"]
            fired += hdb.fire_alert(rule["id"], f"stale:{code}:{since}", "danger", i["title"])
    elif kind == "regular_plan":
        for p in positions:
            if p["flags"]["regular_plan"]:
                fired += hdb.fire_alert(rule["id"], f"regular:{p['scheme_code']}", "info",
                                        f"{p['display_name']} is a Regular plan")
    return fired


def evaluate_all() -> int:
    fired = 0
    for rule in hdb.list_alert_rules():
        try:
            fired += evaluate_rule(rule)
        except Exception:
            logger.exception("Evaluating holdings alert rule %s failed", rule.get("id"))
    if fired:
        logger.info("Holdings alerts: %d new", fired)
    return fired
