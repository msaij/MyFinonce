"""Holdings Phase 4: SIP mandates, goals, insights and alerts."""

import datetime
from decimal import Decimal

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.db import connection
from app.db import queries as db
from app.main import app
from app.services import holdings_planning as planning
from app.services import plan_matcher
from tests.holdings_support import add_ok, business_days, new_portfolio, seed

d = datetime.date
DIRECT, REGULAR, LIQUID, DEAD = 4001, 4002, 4003, 4004


@pytest.fixture()
def client(pg_db):
    rng = np.random.default_rng(11)
    days = list(business_days(d(2022, 1, 3), d(2024, 12, 31)))
    eq = rng.normal(0.0005, 0.009, len(days))
    nav_path = lambda r: list(zip(days, 50.0 * np.cumprod(1.0 + r)))  # noqa: E731
    seed(
        [(DIRECT, "Alpha Flexi Cap Fund - Direct Plan - Growth", "Alpha Mutual Fund", "Equity Scheme - Flexi Cap Fund", "Direct", "Growth", 0.60, "official"),
         (REGULAR, "Alpha Flexi Cap Fund - Regular Plan - Growth", "Alpha Mutual Fund", "Equity Scheme - Flexi Cap Fund", "Regular", "Growth", 1.60, "official"),
         (LIQUID, "Beta Liquid Fund - Direct Plan - Growth", "Beta Mutual Fund", "Debt Scheme - Liquid Fund", "Direct", "Growth", 0.20, "official"),
         (DEAD, "Gamma Old Fund - Direct Plan - Growth", "Gamma Mutual Fund", "Equity Scheme - Large Cap Fund", "Direct", "Growth", 1.00, "official")],
        {DIRECT: nav_path(eq), REGULAR: nav_path(eq - 0.00004), LIQUID: nav_path(np.full(len(days), 0.00025)),
         DEAD: [(day, 10.0 + 0.01 * i) for i, day in enumerate(days[: len(days) // 2])]},   # stops publishing mid-2023
    )
    plan_matcher._cached_pairs_dict = None
    yield TestClient(app)
    plan_matcher._cached_pairs_dict = None


def _buy(c, pid, code, date, amount):
    add_ok(c, portfolio_id=pid, scheme_code=code, txn_type="BUY", trade_date=date, amount=amount)

# --- SIP schedule (pure) ----------------------------------------------------------------

def test_schedule_monthly_with_anniversary_step_up_and_end_date():
    m = {"day_of_month": 5, "start_date": d(2023, 1, 10), "end_date": d(2024, 3, 31), "amount": Decimal("1000"), "step_up_pct": Decimal("10")}
    s = planning.schedule(m, d(2030, 1, 1))
    assert s[0][0] == d(2023, 2, 5)                    # the 5th of January was before the start
    assert len(s) == 14 and s[-1][0] == d(2024, 3, 5)  # stops at end_date
    assert s[11][1] == Decimal("1000.00") and s[12][1] == Decimal("1100.00")


# --- SIP mandates via the API ------------------------------------------------------------------

def test_generate_previews_then_writes_once(client):
    pid = new_portfolio(client)
    m = client.post("/api/holdings/sip-mandates", json={"portfolio_id": pid, "scheme_code": DIRECT, "amount": 5000,
                                                       "day_of_month": 6, "start_date": "2024-01-01", "end_date": "2024-06-30"}).json()
    preview = client.post(f"/api/holdings/sip-mandates/{m['id']}/generate").json()
    assert preview["count"] == 6 and preview["written"] is False
    assert client.get(f"/api/holdings/portfolios/{pid}/transactions").json() == []
    # 2024-01-06 was a Saturday: allotted at the next business day's NAV (Monday the 8th).
    assert preview["rows"][0]["trade_date"] == "2024-01-08"
    written = client.post(f"/api/holdings/sip-mandates/{m['id']}/generate", params={"confirm": True}).json()
    assert written["written"] and written["count"] == 6
    txns = client.get(f"/api/holdings/portfolios/{pid}/transactions").json()
    assert len(txns) == 6 and all(t["sip_mandate_id"] == m["id"] and t["txn_type"] == "SIP" for t in txns)
    # Idempotent: nothing left to generate.
    assert client.post(f"/api/holdings/sip-mandates/{m['id']}/generate", params={"confirm": True}).json()["count"] == 0
    # Deleting one instalment makes exactly that one due again.
    client.delete(f"/api/holdings/transactions/{txns[2]['id']}")
    again = client.post(f"/api/holdings/sip-mandates/{m['id']}/generate").json()
    assert again["count"] == 1 and again["rows"][0]["trade_date"] == txns[2]["trade_date"]
    view = client.get(f"/api/holdings/portfolios/{pid}/sip-mandates").json()[0]
    assert view["instalments_recorded"] == 5 and view["instalments_pending"] == 1


def test_mandate_cannot_start_before_nav_history(client):
    pid = new_portfolio(client)
    r = client.post("/api/holdings/sip-mandates", json={"portfolio_id": pid, "scheme_code": DIRECT, "amount": 5000,
                                                       "day_of_month": 6, "start_date": "2019-01-01"})
    assert r.status_code == 422


# --- Goals -------------------------------------------------------------------------------------

def test_goal_probability_and_required_sip_are_consistent(client):
    pid = new_portfolio(client)
    _buy(client, pid, DIRECT, "2022-02-01", 200000)
    target_date = (datetime.date.today() + datetime.timedelta(days=365 * 10)).isoformat()
    g = client.post("/api/holdings/goals", json={"name": "Retirement", "target_amount": 5000000, "target_date": target_date,
                                                "inflation_pct": 6, "portfolio_ids": [pid]}).json()
    s = client.get(f"/api/holdings/goals/{g['id']}/status").json()
    assert s["state"] == "projected"
    assert s["target_future"] == pytest.approx(5000000 * 1.06 ** s["years_left"], rel=1e-9)
    req = s["required_sip"]
    assert req["p50"] <= req["p75"] <= req["p90"]
    # Plugging the 75%-odds SIP back in must give ~75% odds: same paths, closed form.
    at75 = client.get(f"/api/holdings/goals/{g['id']}/status", params={"sip": req["p75"]}).json()
    assert at75["probability_pct"] == pytest.approx(75.0, abs=0.5)
    more = client.get(f"/api/holdings/goals/{g['id']}/status", params={"sip": req["p75"] * 2}).json()
    assert more["probability_pct"] > at75["probability_pct"]
    p = at75["projection"]
    assert p["month"][0] == 0 and len(p["p50"]) == len(p["contributed"]) == len(p["month"])


def test_goal_edge_states(client):
    pid = new_portfolio(client)
    _buy(client, pid, DIRECT, "2022-02-01", 100000)
    future = (datetime.date.today() + datetime.timedelta(days=900)).isoformat()
    lonely = client.post("/api/holdings/goals", json={"name": "Car", "target_amount": 800000, "target_date": future}).json()
    assert client.get(f"/api/holdings/goals/{lonely['id']}/status").json()["state"] == "no_portfolios"
    past = client.post("/api/holdings/goals", json={"name": "Old", "target_amount": 1000, "target_date": "2023-01-01",
                                                   "portfolio_ids": [pid]}).json()
    assert client.get(f"/api/holdings/goals/{past['id']}/status").json()["state"] == "reached"


# --- Insights --------------------------------------------------------------------------------

def test_insights_price_regular_plans_from_official_ter_and_flag_dead_funds(client):
    pid = new_portfolio(client)
    _buy(client, pid, REGULAR, "2022-02-01", 100000)
    _buy(client, pid, LIQUID, "2022-02-01", 50000)
    _buy(client, pid, DEAD, "2022-02-01", 20000)
    items = client.get(f"/api/holdings/portfolios/{pid}/insights").json()["insights"]
    kinds = [i["kind"] for i in items]
    assert kinds[0] == "stale_nav"                                   # danger sorts first
    reg = next(i for i in items if i["kind"] == "regular_plan_cost")
    fund = reg["numbers"]["funds"][0]
    assert fund["direct_code"] == DIRECT and fund["ter_gap_pct"] == pytest.approx(1.0)
    value = next(p for p in client.get(f"/api/holdings/portfolios/{pid}/summary").json()["positions"] if p["scheme_code"] == REGULAR)["current_value"]
    assert reg["numbers"]["annual_cost"] == pytest.approx(value * 0.01)
    assert "concentration" in kinds


def test_bottom_quartile_is_flagged_against_same_category_and_plan_peers(client):
    days = list(business_days(d(2022, 1, 3), d(2024, 12, 31)))
    con = connection.get_connection()
    rows = []
    for k in range(10):                          # 10 Direct flexi-cap peers, better drift each
        code = 5000 + k
        con.execute(
            "INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type) VALUES "
            "(%s, %s, 'Peer MF', 'Equity Scheme - Flexi Cap Fund', 'Direct', 'Growth')", (code, f"Peer {k} Flexi Cap Fund"))
        rows += [(code, day, round(10.0 * (1.0 + 0.0003 * (k + 2)) ** i, 4)) for i, day in enumerate(days)]
    con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s,%s,%s)", rows)
    # Make our DIRECT fund the clear laggard over the window.
    con.execute("UPDATE nav_history SET nav = 50 WHERE scheme_code = %s", (DIRECT,))
    con.close()
    db.refresh_summary_table()
    pid = new_portfolio(client)
    _buy(client, pid, DIRECT, "2022-02-01", 10000)
    items = client.get(f"/api/holdings/portfolios/{pid}/insights").json()["insights"]
    bq = next(i for i in items if i["kind"] == "bottom_quartile")
    assert bq["scheme_codes"] == [DIRECT] and bq["numbers"]["percentile"] <= 25 and bq["numbers"]["peers"] >= 8


# --- Alerts ------------------------------------------------------------------------------------

def test_alert_rules_fire_once_and_can_be_acknowledged(client):
    pid = new_portfolio(client)
    _buy(client, pid, REGULAR, "2022-02-01", 100000)
    rule = client.post("/api/holdings/alert-rules", json={"kind": "regular_plan", "portfolio_id": pid}).json()
    assert client.get("/api/holdings/alerts/count").json()["unacked"] == 1       # fired on creation
    assert client.post("/api/holdings/alerts/evaluate").json()["fired"] == 0     # dedupe: condition unchanged
    alerts = client.get("/api/holdings/alerts").json()["alerts"]
    assert alerts[0]["rule_id"] == rule["id"] and "Regular plan" in alerts[0]["message"]
    assert client.post("/api/holdings/alerts/ack", json={"ids": [alerts[0]["id"]]}).json()["acknowledged"] == 1
    assert client.get("/api/holdings/alerts/count").json()["unacked"] == 0
    assert client.delete(f"/api/holdings/alert-rules/{rule['id']}").status_code == 200


def test_stale_nav_alert(client):
    pid = new_portfolio(client)
    _buy(client, pid, DEAD, "2022-02-01", 20000)
    _buy(client, pid, DIRECT, "2022-02-01", 20000)
    client.post("/api/holdings/alert-rules", json={"kind": "stale_nav", "portfolio_id": pid, "threshold": 30})
    alerts = client.get("/api/holdings/alerts", params={"unacked": True}).json()["alerts"]
    assert len(alerts) == 1 and alerts[0]["severity"] == "danger" and "Gamma Old Fund" in alerts[0]["message"]


# --- Backup covers everything ----------------------------------------------------------------------

def test_backup_restores_mandates_goals_and_rules(client):
    pid = new_portfolio(client)
    _buy(client, pid, DIRECT, "2022-02-01", 10000)
    m = client.post("/api/holdings/sip-mandates", json={"portfolio_id": pid, "scheme_code": DIRECT, "amount": 1000,
                                                       "day_of_month": 10, "start_date": "2024-10-01"}).json()
    client.post(f"/api/holdings/sip-mandates/{m['id']}/generate", params={"confirm": True})
    client.post("/api/holdings/goals", json={"name": "House", "target_amount": 900000, "target_date": "2031-01-01", "portfolio_ids": [pid]})
    client.post("/api/holdings/alert-rules", json={"kind": "drawdown", "portfolio_id": pid, "threshold": 15})
    backup = client.get("/api/holdings/backup.json").json()
    con = connection.get_connection()
    for t in ("holding_alerts", "holding_alert_rules", "goal_portfolios", "goals", "holding_transactions",
              "sip_mandates", "portfolio_targets", "portfolios"):
        con.execute(f"DELETE FROM {t}")
    con.close()
    r = client.post("/api/holdings/restore", json=backup)
    assert r.status_code == 200, r.text
    again = client.get("/api/holdings/backup.json").json()
    for table in ("portfolios", "holding_transactions", "sip_mandates", "goals", "goal_portfolios", "holding_alert_rules"):
        assert again[table] == backup[table], table
    assert client.get("/api/holdings/goals").json()[0]["portfolio_ids"] == [pid]
