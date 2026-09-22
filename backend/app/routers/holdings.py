"""Holdings: the owner's own portfolios and transactions.

Thin HTTP surface over the holdings services. Two domain errors map to status
codes here and nowhere else (see `_call`):
  LookupError      -> 404
  LedgerRejected   -> 422, body {"detail": {"message", "errors"}} so the form can
                      point at the exact transaction a change would break.

`{pid}` in the view routes is a portfolio id, a comma list, or "all" (the
household view). Not behind ADMIN_TOKEN: that token guards shared ops actions on
/api/admin; this is the owner's data, and docker-compose.yml publishes the API on
127.0.0.1 only.
"""

import datetime
import json
from typing import Any, Callable, Dict, List, Optional, TypeVar

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import Response

from app.core.serialize import sanitize_floats
from app.db import holdings as hdb
from app.schemas.holdings import (
    AlertRuleCreate,
    AlertRuleUpdate,
    GoalSave,
    PortfolioCreate,
    PortfolioUpdate,
    PreviewRequest,
    SipMandateCreate,
    SipMandateUpdate,
    TargetsUpdate,
    TransactionDraft,
)
from app.services import holdings_analytics as analytics
from app.services import holdings_insights as insights_svc
from app.services import holdings_planning as planning
from app.services import holdings_risk as risk_svc
from app.services import holdings_service as svc

router = APIRouter(prefix="/api/holdings", tags=["holdings"])
to_json = svc.to_json
T = TypeVar("T")


def _call(fn: Callable[..., Any], *args: Any) -> Any:
    """Runs a service call, maps its domain errors to HTTP, and makes the result
    JSON-safe (numpy scalars, NaN/Inf)."""
    try:
        return sanitize_floats(fn(*args))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except svc.LedgerRejected as e:
        raise HTTPException(status_code=422, detail={"message": str(e), "errors": e.errors}) from None


def _found(value: Optional[T], what: str) -> T:
    if value is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return value


def _portfolio(portfolio_id: int) -> Dict[str, Any]:
    return _found(hdb.get_portfolio(portfolio_id), f"Portfolio {portfolio_id}")


def _unique_portfolio_name(name: str, exclude_id: Optional[int] = None) -> str:
    name = name.strip()
    if any(p["name"].lower() == name.lower() and p["id"] != exclude_id for p in hdb.list_portfolios()):
        raise HTTPException(status_code=409, detail=f"A portfolio named {name!r} already exists")
    return name


def _download(body: str, media_type: str, filename: str) -> Response:
    return Response(content=body, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# --- Portfolios ---------------------------------------------------------------
# Portfolio writes change what "all" covers, so each one bumps the ledger version.

@router.get("/portfolios")
def list_portfolios(include_archived: bool = False) -> list:
    return [to_json(p) for p in hdb.list_portfolios(include_archived=include_archived)]


@router.post("/portfolios")
def create_portfolio(body: PortfolioCreate) -> dict:
    fields = body.model_dump(exclude_none=True)
    fields["name"] = _unique_portfolio_name(fields["name"])
    created = hdb.create_portfolio(fields)
    svc.bump_ledger_version()
    return to_json(created)


@router.patch("/portfolios/{portfolio_id}")
def update_portfolio(portfolio_id: int, body: PortfolioUpdate) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if fields.get("name"):
        fields["name"] = _unique_portfolio_name(fields["name"], exclude_id=portfolio_id)
    updated = _found(hdb.update_portfolio(portfolio_id, fields), f"Portfolio {portfolio_id}")
    svc.bump_ledger_version()
    return to_json(updated)


@router.delete("/portfolios/{portfolio_id}")
def archive_portfolio(portfolio_id: int) -> dict:
    """Archives -- never hard-deletes. Its transactions stay in the ledger and in
    backups; PATCH {"archived": false} brings it back."""
    updated = _found(hdb.update_portfolio(portfolio_id, {"archived": True}), f"Portfolio {portfolio_id}")
    svc.bump_ledger_version()
    return to_json(updated)


@router.get("/portfolios/{portfolio_id}/targets")
def get_targets(portfolio_id: int) -> dict:
    _portfolio(portfolio_id)
    return {"targets": hdb.get_targets(portfolio_id), "asset_classes": analytics.ASSET_CLASSES}


@router.put("/portfolios/{portfolio_id}/targets")
def put_targets(portfolio_id: int, body: TargetsUpdate) -> dict:
    _portfolio(portfolio_id)
    saved = hdb.replace_targets(portfolio_id, _call(analytics.validate_targets, body.targets))
    svc.bump_ledger_version()
    return {"targets": saved, "asset_classes": analytics.ASSET_CLASSES}


# --- Views (pid = id | "1,3" | "all") ------------------------------------------------

@router.get("/portfolios/{pid}/summary")
def portfolio_summary(pid: str) -> dict:
    return _call(svc.summary, pid)


@router.get("/portfolios/{pid}/positions/{scheme_code}")
def position_detail(pid: str, scheme_code: int) -> dict:
    return _call(svc.position_detail, pid, scheme_code)


@router.get("/portfolios/{pid}/transactions")
def list_transactions(pid: str, include_deleted: bool = False) -> list:
    txns = hdb.list_transactions(_call(svc.resolve_portfolio_ids, pid), include_deleted=include_deleted)
    meta = hdb.scheme_meta({t["scheme_code"] for t in txns})
    return [
        {**to_json(t), **{k: meta.get(int(t["scheme_code"]), {}).get(k) for k in ("scheme_name", "plan_type", "option_type")}}
        for t in txns
    ]


@router.get("/portfolios/{pid}/export.csv")
def export_csv(pid: str) -> Response:
    return _download(_call(svc.export_csv, pid), "text/csv", f"holdings-{pid}-transactions.csv")


@router.get("/portfolios/{pid}/performance")
def performance(pid: str, benchmark: Optional[int] = Query(None, description="Benchmark scheme code override")) -> dict:
    return _call(analytics.performance, pid, benchmark)


@router.get("/portfolios/{pid}/allocation")
def allocation(pid: str) -> dict:
    return _call(analytics.allocation, pid)


@router.get("/portfolios/{pid}/rebalance")
def rebalance(pid: str, new_money: float = Query(..., gt=0)) -> dict:
    return _call(analytics.rebalance_with_new_money, pid, new_money)


@router.get("/portfolios/{pid}/risk")
def portfolio_risk(pid: str, rf: float = Query(6.5, ge=0, le=20)) -> dict:
    return _call(risk_svc.risk, pid, rf)


@router.get("/portfolios/{pid}/factors")
def portfolio_factors(pid: str, rf: float = Query(6.5, ge=0, le=20)) -> dict:
    return _call(risk_svc.factors, pid, rf)


@router.get("/portfolios/{pid}/stress")
def portfolio_stress(
    pid: str,
    shock_market: float = Query(-15.0, ge=-100, le=100),
    shock_size: float = Query(-5.0, ge=-100, le=100),
    shock_value: float = Query(2.0, ge=-100, le=100),
    shock_momentum: float = Query(-8.0, ge=-100, le=100),
) -> dict:
    """Shocks are in percent (e.g. -15 = market down 15%)."""
    shocks = {"market": shock_market, "size": shock_size, "value": shock_value, "momentum": shock_momentum}
    return _call(risk_svc.stress, pid, shocks)


@router.get("/portfolios/{pid}/monte-carlo")
def portfolio_monte_carlo(pid: str, years: float = Query(1.0, gt=0, le=40), sims: int = Query(1000, ge=100, le=5000)) -> dict:
    return _call(risk_svc.monte_carlo, pid, years, sims)


@router.get("/portfolios/{pid}/insights")
def portfolio_insights(pid: str) -> dict:
    return _call(insights_svc.insights, pid)


@router.get("/portfolios/{pid}/sip-mandates")
def list_sip_mandates(pid: str) -> list:
    return _call(lambda: [planning.mandate_view(m) for m in hdb.list_sip_mandates(svc.resolve_portfolio_ids(pid))])


# --- Transactions -----------------------------------------------------------------

@router.post("/transactions/preview")
def preview_transaction(body: PreviewRequest) -> dict:
    return _call(svc.preview, body.model_dump(exclude={"replace_txn_id"}), body.replace_txn_id)


@router.post("/transactions")
def add_transaction(body: TransactionDraft) -> dict:
    return _call(svc.add_transaction, body.model_dump())


@router.patch("/transactions/{txn_id}")
def edit_transaction(txn_id: int, body: TransactionDraft) -> dict:
    return _call(svc.edit_transaction, txn_id, body.model_dump())


@router.delete("/transactions/{txn_id}")
def delete_transaction(txn_id: int) -> dict:
    return _call(svc.delete_transaction, txn_id)


@router.post("/transactions/{txn_id}/restore")
def restore_transaction(txn_id: int) -> dict:
    return _call(svc.restore_transaction, txn_id)


# --- SIP mandates ---------------------------------------------------------------------

@router.post("/sip-mandates")
def create_sip_mandate(body: SipMandateCreate) -> dict:
    fields = body.model_dump()
    _call(planning.validate_mandate, fields)
    return _call(planning.mandate_view, hdb.create_sip_mandate(fields))


@router.patch("/sip-mandates/{mandate_id}")
def update_sip_mandate(mandate_id: int, body: SipMandateUpdate) -> dict:
    m = _found(hdb.update_sip_mandate(mandate_id, body.model_dump(exclude_unset=True)), f"SIP mandate {mandate_id}")
    return _call(planning.mandate_view, m)


@router.post("/sip-mandates/{mandate_id}/generate")
def generate_sip_instalments(mandate_id: int, confirm: bool = False) -> dict:
    """confirm=false previews the instalments that would be written; confirm=true writes them."""
    return _call(planning.generate_instalments, mandate_id, confirm)


# --- Goals ------------------------------------------------------------------------------

def _save_goal(goal_id: Optional[int], body: GoalSave) -> dict:
    for pid in body.portfolio_ids:
        _portfolio(pid)
    fields = body.model_dump(exclude={"portfolio_ids"})
    fields["name"] = fields["name"].strip()
    return to_json(hdb.save_goal(goal_id, fields, body.portfolio_ids))


@router.get("/goals")
def list_goals(include_archived: bool = False) -> list:
    return [to_json(g) for g in hdb.list_goals(include_archived)]


@router.post("/goals")
def create_goal(body: GoalSave) -> dict:
    return _save_goal(None, body)


@router.put("/goals/{goal_id}")
def update_goal(goal_id: int, body: GoalSave) -> dict:
    _found(hdb.get_goal(goal_id), f"Goal {goal_id}")
    return _save_goal(goal_id, body)


@router.get("/goals/{goal_id}/status")
def goal_status(goal_id: int, sip: Optional[float] = Query(None, ge=0, description="What-if monthly SIP")) -> dict:
    return _call(planning.goal_status, _found(hdb.get_goal(goal_id), f"Goal {goal_id}"), sip)


# --- Alerts --------------------------------------------------------------------------------

@router.get("/alert-rules")
def list_alert_rules() -> dict:
    return {"rules": [to_json(r) for r in hdb.list_alert_rules()], "kinds": insights_svc.RULE_KINDS}


@router.post("/alert-rules")
def create_alert_rule(body: AlertRuleCreate) -> dict:
    if body.portfolio_id is not None:
        _portfolio(body.portfolio_id)
    rule = hdb.create_alert_rule(body.model_dump())
    insights_svc.evaluate_rule(rule)          # a new rule reports what is already true
    return to_json(rule)


@router.patch("/alert-rules/{rule_id}")
def update_alert_rule(rule_id: int, body: AlertRuleUpdate) -> dict:
    return to_json(_found(hdb.update_alert_rule(rule_id, body.model_dump(exclude_unset=True)), f"Alert rule {rule_id}"))


@router.delete("/alert-rules/{rule_id}")
def delete_alert_rule(rule_id: int) -> dict:
    if not hdb.delete_alert_rule(rule_id):
        raise HTTPException(status_code=404, detail=f"Alert rule {rule_id} not found")
    return {"deleted": rule_id}


@router.get("/alerts")
def list_alerts(unacked: bool = False) -> dict:
    return {"alerts": [to_json(a) for a in hdb.list_alerts(unacked_only=unacked)], "unacked": hdb.unacked_alert_count()}


@router.get("/alerts/count")
def alert_count() -> dict:
    return {"unacked": hdb.unacked_alert_count()}


@router.post("/alerts/ack")
def ack_alerts(ids: Optional[List[int]] = Body(None, embed=True)) -> dict:
    """Acknowledge the given alert ids, or all unacknowledged ones when ids is omitted."""
    return {"acknowledged": hdb.ack_alerts(ids)}


@router.post("/alerts/evaluate")
def evaluate_alerts() -> dict:
    return {"fired": insights_svc.evaluate_all()}


# --- Lookup / backup ----------------------------------------------------------------

@router.get("/nav-lookup")
def nav_lookup(scheme_code: int, date: datetime.date) -> dict:
    nav_date, nav = _found(hdb.nav_on_or_before(scheme_code, date), "NAV on or before that date")
    return {"scheme_code": scheme_code, "requested_date": date.isoformat(), "nav_date": nav_date.isoformat(),
            "nav": nav, "is_fallback": nav_date != date}


@router.get("/backup.json")
def backup() -> Response:
    return _download(json.dumps(svc.backup(), indent=1), "application/json", "myfinonce-holdings-backup.json")


@router.post("/restore")
def restore(payload: Dict[str, Any] = Body(...)) -> dict:
    return _call(svc.restore, payload)
