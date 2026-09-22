"""Holdings ledger: pure replay rules, then the API end to end on the test DB."""

import datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import portfolio_sim
from app.db import connection
from app.db import migrate
from app.db import queries as db
from app.main import app
from app.services import holdings_ledger as ledger
from app.services import holdings_service as svc
from tests.holdings_support import add_txn, business_days, nav_on, new_portfolio, seed

d = datetime.date


def txn(id, code, ttype, date, amount, units, scale=1):
    return {"id": id, "scheme_code": code, "txn_type": ttype, "trade_date": date,
            "amount": Decimal(str(amount)), "units": Decimal(str(units)), "units_scale": Decimal(str(scale))}


# --- Pure ledger ------------------------------------------------------------------

def test_fifo_partial_redemption_realises_gain_on_oldest_lot():
    res = ledger.replay([
        txn(1, 7, "BUY", d(2023, 1, 2), 1000, 100),     # cost 10/unit
        txn(2, 7, "BUY", d(2023, 6, 1), 1500, 100),     # cost 15/unit
        txn(3, 7, "REDEEM", d(2024, 1, 2), 2400, 120),  # sells 100 @10 + 20 @15 = 1300 cost
    ])
    assert res.ok
    pos = res.positions[7]
    assert pos.units == Decimal("80")
    assert pos.realised_gain == Decimal("1100")
    assert pos.cost_basis == Decimal("1200")          # 80 remaining @ 15
    assert pos.avg_cost_nav == Decimal("15")


def test_over_redemption_is_an_error_not_a_crash():
    res = ledger.replay([
        txn(1, 7, "BUY", d(2023, 1, 2), 1000, 100),
        txn(2, 7, "REDEEM", d(2023, 2, 1), 1500, 100.5),
    ])
    assert not res.ok
    assert res.errors[0].txn_id == 2
    assert "only 100" in res.errors[0].message


def test_redemption_before_the_purchase_date_is_rejected():
    res = ledger.replay([
        txn(1, 7, "REDEEM", d(2023, 1, 1), 100, 10),
        txn(2, 7, "BUY", d(2023, 1, 2), 1000, 100),
    ])
    assert not res.ok and res.errors[0].txn_id == 1


def test_same_day_buy_then_sell_replays_inflow_first():
    res = ledger.replay([
        txn(2, 7, "REDEEM", d(2023, 1, 2), 500, 50),
        txn(1, 7, "BUY", d(2023, 1, 2), 1000, 100),
    ])
    assert res.ok and res.positions[7].units == Decimal("50")


def test_statement_rounding_crumb_counts_as_full_redemption():
    res = ledger.replay([
        txn(1, 7, "BUY", d(2023, 1, 2), 1000, "100.0004"),
        txn(2, 7, "REDEEM", d(2023, 2, 1), 1100, 100),
    ])
    assert res.ok and res.positions[7].is_closed and res.positions[7].cost_basis == 0


def test_dividends_reinvest_adds_units_payout_adds_income_and_a_flow():
    res = ledger.replay([
        txn(1, 7, "BUY", d(2023, 1, 2), 1000, 100),
        txn(2, 7, "DIVIDEND_REINVEST", d(2023, 3, 1), 50, 4),
        txn(3, 7, "DIVIDEND_PAYOUT", d(2023, 6, 1), 30, 0),
    ])
    pos = res.positions[7]
    assert pos.units == Decimal("104")
    assert pos.dividend_income == Decimal("80")
    assert pos.total_invested == Decimal("1000")          # reinvest is not new money
    assert [cf for _, cf in res.portfolio_cash_flows] == [Decimal("-1000"), Decimal("30")]


def test_switch_is_a_holding_flow_but_not_a_portfolio_flow():
    res = ledger.replay([
        txn(1, 7, "BUY", d(2023, 1, 2), 1000, 100),
        txn(2, 7, "SWITCH_OUT", d(2023, 6, 1), 1200, 100),
        txn(3, 8, "SWITCH_IN", d(2023, 6, 1), 1200, 60),
    ])
    assert res.ok
    assert res.positions[7].is_closed and res.positions[7].realised_gain == Decimal("200")
    assert res.positions[8].units == Decimal("60")
    assert [cf for _, cf in res.positions[8].cash_flows] == [Decimal("-1200")]
    assert [cf for _, cf in res.portfolio_cash_flows] == [Decimal("-1000")]


def test_units_scale_puts_pre_split_units_on_amfi_scale():
    res = ledger.replay([txn(1, 7, "BUY", d(2019, 1, 2), 1000, 10, scale=10)])
    assert res.positions[7].units == Decimal("100")


def test_split_scale_detects_clean_factor_and_flags_plain_deviation():
    scale, dev = svc.split_scale(Decimal("1000.0"), 100.0)       # Rs 1000 -> Rs 100 face value reset
    assert scale == Decimal(10) and dev is None
    scale, dev = svc.split_scale(Decimal("100.0"), 101.0)        # 1% off: a typo, not a split
    assert scale == 1 and dev == pytest.approx(0.01)


def test_split_constants_match_the_nav_normalizer():
    # holdings_service mirrors these rather than importing privates from the
    # frozen queries.py; this is the tripwire if either side changes.
    assert svc.SPLIT_CANDIDATES == db._SPLIT_CANDIDATES
    assert svc.SPLIT_TOLERANCE == db._SPLIT_TOLERANCE


def test_migrations_treat_everything_up_to_head_as_applied():
    head = migrate.MIGRATIONS[-1][0]
    assert migrate._applied_versions({head}) == {v for v, _ in migrate.MIGRATIONS}
    assert migrate._applied_versions({"0001_baseline"}) == {"0001_baseline"}
    assert migrate._applied_versions(set()) == set()


def test_xirr_convention_is_portfolio_sims_365_25_day_year():
    # Holdings reuses portfolio_sim.xirr (Compare & Simulate's solver), which uses
    # 365.25-day years. Excel's XIRR uses 365, so it would print exactly 10.0000% here.
    rate = portfolio_sim.xirr([(d(2023, 1, 1), -10000.0), (d(2024, 1, 1), 11000.0)])
    assert rate == pytest.approx(1.1 ** (365.25 / 365) - 1, abs=1e-6)


# --- API on the test database --------------------------------------------------------

@pytest.fixture()
def seeded(pg_db):
    """Two funds with a steady NAV path through 2024, and a summary_table built on them."""
    days = list(business_days(d(2023, 1, 2), d(2024, 12, 31)))
    seed(
        [(1001, "Alpha Flexi Cap Fund - Direct Plan - Growth", "Alpha MF", "Equity Scheme - Flexi Cap Fund", "Direct", "Growth"),
         (1002, "Beta Liquid Fund - Regular Plan - Growth", "Beta MF", "Debt Scheme - Liquid Fund", "Regular", "Growth")],
        {1001: [(day, 10.0 + 0.01 * i) for i, day in enumerate(days)],
         1002: [(day, 1000.0 + 0.2 * i) for i, day in enumerate(days)]},
    )
    return TestClient(app)

def test_buy_auto_fills_amfi_nav_and_stamp_duty(seeded):
    pid = new_portfolio(seeded)
    r = add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", amount=10000)
    assert r.status_code == 200, r.text
    row = r.json()["transactions"][0]
    nav = Decimal(str(nav_on(1001, d(2023, 3, 1))))
    assert Decimal(row["nav"]) == nav.quantize(Decimal("0.0001"))
    stamp = Decimal(row["stamp_duty"])
    assert stamp == (Decimal("10000") - Decimal("10000") / Decimal("1.00005")).quantize(Decimal("0.01"))
    assert Decimal(row["units"]) == ((Decimal("10000") - stamp) / Decimal(row["nav"])).quantize(Decimal("0.001"))
    assert row["nav_source"] == "amfi_auto"


def test_weekend_trade_uses_previous_nav_and_says_so(seeded):
    pid = new_portfolio(seeded)
    r = add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-04", amount=5000)
    assert r.status_code == 200
    assert any("2023-03-03" in w for w in r.json()["warnings"])


def test_future_and_pre_history_dates_are_rejected(seeded):
    pid = new_portfolio(seeded)
    future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    assert add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date=future, amount=1).status_code == 422
    r = add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2022-06-01", amount=1)
    assert r.status_code == 422 and "starts 2023-01-02" in r.json()["detail"]["message"]


def test_over_redemption_is_refused_with_the_offending_transaction(seeded):
    pid = new_portfolio(seeded)
    add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100, apply_stamp_duty=False)
    r = add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="REDEEM", trade_date="2023-04-03", units=150)
    assert r.status_code == 422
    assert "only 100" in r.json()["detail"]["message"]


def test_deleting_a_purchase_a_later_redemption_needs_is_refused(seeded):
    pid = new_portfolio(seeded)
    buy = add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01",
               units=100, apply_stamp_duty=False).json()["transactions"][0]
    add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="REDEEM", trade_date="2023-06-01", units=60)
    r = seeded.delete(f"/api/holdings/transactions/{buy['id']}")
    assert r.status_code == 422
    assert "later transaction" in r.json()["detail"]["message"]


def test_delete_and_restore_round_trip(seeded):
    pid = new_portfolio(seeded)
    buy = add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01",
               amount=10000).json()["transactions"][0]
    before = seeded.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]["current_value"]
    assert seeded.delete(f"/api/holdings/transactions/{buy['id']}").status_code == 200
    assert seeded.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]["current_value"] == 0
    assert seeded.post(f"/api/holdings/transactions/{buy['id']}/restore").status_code == 200
    assert seeded.get(f"/api/holdings/portfolios/{pid}/summary").json()["kpis"]["current_value"] == pytest.approx(before)


def test_switch_writes_a_linked_pair_and_deletes_as_one(seeded):
    pid = new_portfolio(seeded)
    add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", amount=10000)
    r = add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="SWITCH", trade_date="2023-09-01",
             redeem_all=True, switch_to_scheme_code=1002)
    assert r.status_code == 200, r.text
    legs = r.json()["transactions"]
    assert [l["txn_type"] for l in legs] == ["SWITCH_OUT", "SWITCH_IN"]
    assert legs[0]["switch_group"] == legs[1]["switch_group"]
    assert Decimal(legs[1]["amount"]) == Decimal(legs[0]["amount"])
    s = seeded.get(f"/api/holdings/portfolios/{pid}/summary").json()
    open_codes = [p["scheme_code"] for p in s["positions"] if not p["is_closed"]]
    assert open_codes == [1002]
    seeded.delete(f"/api/holdings/transactions/{legs[1]['id']}")
    txns = seeded.get(f"/api/holdings/portfolios/{pid}/transactions").json()
    assert [t["txn_type"] for t in txns] == ["BUY"]


def test_summary_values_units_at_latest_nav_and_household_is_the_sum(seeded):
    a = new_portfolio(seeded, "Self")
    b = new_portfolio(seeded, "Spouse")
    add_txn(seeded, portfolio_id=a, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100, apply_stamp_duty=False)
    add_txn(seeded, portfolio_id=b, scheme_code=1001, txn_type="BUY", trade_date="2023-05-02", units=50, apply_stamp_duty=False)
    add_txn(seeded, portfolio_id=b, scheme_code=1002, txn_type="SIP", trade_date="2023-05-02", amount=5000)
    latest_1001 = nav_on(1001, d(2024, 12, 31))

    sa = seeded.get(f"/api/holdings/portfolios/{a}/summary").json()
    assert sa["kpis"]["current_value"] == pytest.approx(100 * latest_1001)
    sb = seeded.get(f"/api/holdings/portfolios/{b}/summary").json()
    hh = seeded.get("/api/holdings/portfolios/all/summary").json()
    assert hh["kpis"]["current_value"] == pytest.approx(sa["kpis"]["current_value"] + sb["kpis"]["current_value"])
    merged = next(p for p in hh["positions"] if p["scheme_code"] == 1001)
    assert merged["units"] == pytest.approx(150)
    assert merged["portfolio_ids"] == [a, b]
    regular = next(p for p in hh["positions"] if p["scheme_code"] == 1002)
    assert regular["flags"]["regular_plan"] is True
    assert hh["kpis"]["xirr_pct"] is not None


def test_user_nav_deviation_warns_but_is_kept(seeded):
    pid = new_portfolio(seeded)
    amfi = float(nav_on(1001, d(2023, 3, 1)))
    r = seeded.post("/api/holdings/transactions/preview", json={
        "portfolio_id": pid, "scheme_code": 1001, "txn_type": "BUY", "trade_date": "2023-03-01",
        "amount": 1000, "nav": round(amfi * 1.02, 4)})
    body = r.json()
    assert body["ok"] and any("differs from AMFI" in w for w in body["warnings"])
    assert body["rows"][0]["nav_source"] == "user"


def test_archived_portfolio_rejects_new_transactions(seeded):
    pid = new_portfolio(seeded)
    seeded.delete(f"/api/holdings/portfolios/{pid}")
    r = add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", amount=100)
    assert r.status_code == 422 and "archived" in r.json()["detail"]["message"]


def test_duplicate_portfolio_name_is_a_conflict(seeded):
    new_portfolio(seeded, "Retirement")
    assert seeded.post("/api/holdings/portfolios", json={"name": "retirement"}).status_code == 409


def test_backup_restore_round_trip_is_exact(seeded):
    pid = new_portfolio(seeded)
    add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", amount=12345.67)
    add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="SWITCH", trade_date="2023-09-01",
         units=100, switch_to_scheme_code=1002)
    backup = seeded.get("/api/holdings/backup.json").json()
    before = seeded.get("/api/holdings/portfolios/all/summary").json()

    assert seeded.post("/api/holdings/restore", json=backup).status_code == 422   # ledger not empty

    con = connection.get_connection()
    con.execute("DELETE FROM holding_transactions")
    con.execute("DELETE FROM portfolios")
    con.close()
    r = seeded.post("/api/holdings/restore", json=backup)
    assert r.status_code == 200, r.text
    assert seeded.get("/api/holdings/backup.json").json()["holding_transactions"] == backup["holding_transactions"]
    after = seeded.get("/api/holdings/portfolios/all/summary").json()
    assert after["kpis"] == before["kpis"]
    # Sequences moved past restored ids: a new portfolio must not collide.
    assert seeded.post("/api/holdings/portfolios", json={"name": "After restore"}).status_code == 200


def test_csv_export_lists_every_transaction(seeded):
    pid = new_portfolio(seeded)
    add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", amount=1000)
    r = seeded.get(f"/api/holdings/portfolios/{pid}/export.csv")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("portfolio,trade_date,type") and len(lines) == 2


def test_position_detail_has_lots_running_balance_and_nav_series(seeded):
    pid = new_portfolio(seeded)
    add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="BUY", trade_date="2023-03-01", units=100, apply_stamp_duty=False)
    add_txn(seeded, portfolio_id=pid, scheme_code=1001, txn_type="REDEEM", trade_date="2023-06-01", units=40)
    body = seeded.get(f"/api/holdings/portfolios/{pid}/positions/1001").json()
    assert [t["balance_units"] for t in body["transactions"]] == [100, 60]
    assert len(body["lots"]) == 1 and body["lots"][0]["units"] == pytest.approx(60)
    assert body["nav_series"] and body["position"]["units"] == pytest.approx(60)
