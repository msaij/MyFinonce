"""Holdings integrity: the rules the 2026-10-22 review found unpinned.

Splits that land after a row was entered, rows the ledger can't replay, restoring a backup
file, edits, the ledger lock, and the guards around writes and downloads. Everything runs
on the isolated test database.
"""

import datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

from app import amfi_sync, portfolio_sim
from app.db import connection
from app.db import holdings as hdb
from app.db import queries as db
from app.main import app
from app.routers import holdings as holdings_router
from app.services import holdings_insights as insights_svc
from app.services import holdings_risk as risk_svc
from app.services import holdings_service as svc
from tests.holdings_support import add_ok, add_txn, business_days, nav_on, new_portfolio, seed

d = datetime.date
EQ, LIQ = 1001, 1002


@pytest.fixture()
def client(pg_db):
    days = list(business_days(d(2023, 1, 2), d(2024, 12, 31)))
    seed([(EQ, "Alpha Flexi Cap Fund - Direct Plan - Growth", "Alpha MF", "Equity Scheme - Flexi Cap Fund", "Direct", "Growth"),
          (LIQ, "Beta Liquid Fund - Direct Plan - Growth", "Beta MF", "Debt Scheme - Liquid Fund", "Direct", "Growth")],
         {EQ: [(day, 10.0 + 0.01 * i) for i, day in enumerate(days)],
          LIQ: [(day, 1000.0 + 0.2 * i) for i, day in enumerate(days)]})
    return TestClient(app)


def _sql(statement, params=()):
    con = connection.get_connection()
    try:
        con.execute(statement, params)
    finally:
        con.close()


def _summary(c, pid="all"):
    r = c.get(f"/api/holdings/portfolios/{pid}/summary")
    assert r.status_code == 200, r.text
    return r.json()


def _buy(c, pid, code=EQ, day="2023-03-01", **kw):
    kw.setdefault("apply_stamp_duty", False)
    if "units" not in kw and "amount" not in kw:
        kw["units"] = 100
    return add_ok(c, portfolio_id=pid, scheme_code=code, txn_type="BUY", trade_date=day, **kw)["transactions"][0]


def _empty_ledger():
    for table, _ in reversed(hdb.BACKUP_TABLES):
        _sql(f"DELETE FROM {table}")


# --- Splits -------------------------------------------------------------------------------

def test_a_split_after_entry_rescales_auto_priced_rows(client):
    """A 10:1 split AMFI publishes after the purchase: normalize_nav_splits divides every
    earlier NAV by 10, which leaves an auto-priced row's NAV and units on the old scale.
    Rows with a typed NAV were rescaled; these were not, and read a tenth of their value."""
    pid = new_portfolio(client)
    buy = _buy(client, pid, amount=10000, apply_stamp_duty=True, units=None)
    before = _summary(client, pid)
    # The history as the normaliser leaves it after a 10:1 split on the latest date.
    _sql("UPDATE nav_history SET nav = nav / 10 WHERE scheme_code = %s", (EQ,))
    db.refresh_summary_table()
    after = _summary(client, pid)
    pos = after["positions"][0]
    assert pos["units"] == pytest.approx(float(buy["units"]) * 10)
    assert pos["flags"]["split_adjusted"] is True
    assert after["kpis"]["current_value"] == pytest.approx(before["kpis"]["current_value"], rel=1e-6)
    # "Redeem everything" sells the real units, for the whole value.
    sold = add_ok(client, portfolio_id=pid, scheme_code=EQ, txn_type="REDEEM", trade_date="2024-12-31",
                  redeem_all=True)["transactions"][0]
    assert float(sold["amount"]) == pytest.approx(before["kpis"]["current_value"], rel=1e-4)


def test_a_typed_nav_far_from_amfis_must_be_confirmed(client):
    """45.10 typed for 15.10 is a clean 1:3 "split" ratio; 30% off is no split at all. Both
    used to go in with a yellow note -- the first multiplying the units by three."""
    pid = new_portfolio(client)
    amfi = nav_on(EQ, d(2023, 3, 1))
    draft = {"portfolio_id": pid, "scheme_code": EQ, "txn_type": "BUY", "trade_date": "2023-03-01",
             "amount": 10000, "nav": round(amfi * 3, 4)}
    preview = client.post("/api/holdings/transactions/preview", json=draft).json()
    assert preview["ok"] is False and preview["errors"][0]["code"] == "nav_unconfirmed"
    assert add_txn(client, **draft).status_code == 422
    off = client.post("/api/holdings/transactions/preview", json={**draft, "nav": round(amfi * 1.3, 4)}).json()
    assert off["errors"][0]["code"] == "nav_unconfirmed"
    # Confirmed against the statement (one from before a 1:3 split): kept, units on AMFI's scale.
    saved = add_ok(client, **draft, nav_confirmed=True)
    assert any("pre-split" in w for w in saved["warnings"])
    row = saved["transactions"][0]
    assert _summary(client, pid)["positions"][0]["units"] == pytest.approx(float(row["units"]) * 3, rel=1e-6)
    # Editing its note doesn't ask again: the NAV on record was already confirmed.
    edited = client.patch(f"/api/holdings/transactions/{row['id']}", json={**draft, "notes": "statement 2023"})
    assert edited.status_code == 200, edited.text


# --- Rows the ledger can't replay ------------------------------------------------------------

def test_rows_the_ledger_cant_replay_are_reported_and_left_out_everywhere(client):
    pid = new_portfolio(client)
    _buy(client, pid)
    # A redemption of more than was held, written past the API, as a script or a hand-edited
    # restore could.
    nav = nav_on(EQ, d(2023, 6, 1))
    _sql("INSERT INTO holding_transactions (portfolio_id, scheme_code, txn_type, trade_date, amount, units, nav) "
         "VALUES (%s, %s, 'REDEEM', '2023-06-01', %s, 150, %s)", (pid, EQ, round(150 * nav, 2), nav))
    svc.bump_ledger_version()
    body = _summary(client, pid)
    errors = body["kpis"]["ledger_errors"]
    assert len(errors) == 1 and errors[0]["portfolio_id"] == pid and "only 100" in errors[0]["message"]
    # The Performance series leaves the same row out, so the two tabs describe one ledger.
    perf = client.get(f"/api/holdings/portfolios/{pid}/performance").json()
    assert perf["series"]["value"][-1] == pytest.approx(body["kpis"]["current_value"], abs=0.01)
    detail = client.get(f"/api/holdings/portfolios/{pid}/positions/{EQ}").json()
    assert detail["ledger_errors"][0]["txn_id"] == errors[0]["txn_id"]


# --- Restore ---------------------------------------------------------------------------------

def _two_txn_backup(client):
    pid = new_portfolio(client)
    _buy(client, pid)
    add_ok(client, portfolio_id=pid, scheme_code=EQ, txn_type="SWITCH", trade_date="2023-09-01", units=40,
           switch_to_scheme_code=LIQ)
    backup = client.get("/api/holdings/backup.json").json()
    _empty_ledger()
    return backup


def test_restore_refuses_a_ledger_that_cant_be_replayed_and_writes_nothing(client):
    backup = _two_txn_backup(client)
    out = next(t for t in backup["holding_transactions"] if t["txn_type"] == "SWITCH_OUT")
    out["units"] = "150.000000"
    r = client.post("/api/holdings/restore", json=backup)
    assert r.status_code == 422 and "can't be replayed" in r.json()["detail"]["message"]
    assert hdb.ledger_is_empty()


def test_restore_refuses_a_switch_leg_without_its_partner(client):
    backup = _two_txn_backup(client)
    backup["holding_transactions"] = [t for t in backup["holding_transactions"] if t["txn_type"] != "SWITCH_IN"]
    r = client.post("/api/holdings/restore", json=backup)
    assert r.status_code == 422 and "other half" in r.json()["detail"]["message"]
    assert hdb.ledger_is_empty()


@pytest.mark.parametrize("mutate,message", [
    # Column names come from the file: only the table's real columns are accepted.
    (lambda b: b["portfolios"][0].update({"name) VALUES ('x'); --": "x"}), "unknown column"),
    (lambda b: b["holding_transactions"][0].update(amount="NaN"), "finite"),
    (lambda b: b["portfolios"][0].update(id=2 ** 31), "valid row id"),
    (lambda b: b["holding_transactions"][0].update(trade_date="2023-02-30"), "row 1"),
    (lambda b: b["holding_transactions"][0].update(scheme_code=999999), "scheme master"),
    (lambda b: b.update(holding_transactions="not rows"), "not a list"),
    (lambda b: b.update(version=99), "format/version"),
])
def test_restore_refuses_a_malformed_file_with_a_reason(client, mutate, message):
    backup = _two_txn_backup(client)
    mutate(backup)
    r = client.post("/api/holdings/restore", json=backup)
    assert r.status_code == 422, r.text
    assert message in r.json()["detail"]["message"]
    assert hdb.ledger_is_empty()


# --- Edits -----------------------------------------------------------------------------------

def test_an_edit_that_breaks_a_later_redemption_is_refused_and_changes_nothing(client):
    pid = new_portfolio(client)
    buy = _buy(client, pid)
    add_ok(client, portfolio_id=pid, scheme_code=EQ, txn_type="REDEEM", trade_date="2023-06-01", units=80)
    before = client.get("/api/holdings/backup.json").json()["holding_transactions"]
    r = client.patch(f"/api/holdings/transactions/{buy['id']}", json={
        "portfolio_id": pid, "scheme_code": EQ, "txn_type": "BUY", "trade_date": "2023-03-01", "units": 50,
        "apply_stamp_duty": False})
    assert r.status_code == 422 and "only 50" in r.json()["detail"]["message"]
    assert client.get("/api/holdings/backup.json").json()["holding_transactions"] == before


def test_moving_a_row_checks_both_portfolios_and_the_preview_agrees_with_the_save(client):
    a, b = new_portfolio(client, "A"), new_portfolio(client, "B")
    buy = _buy(client, a)
    add_ok(client, portfolio_id=a, scheme_code=EQ, txn_type="REDEEM", trade_date="2023-06-01", units=60)
    draft = {"portfolio_id": b, "scheme_code": EQ, "txn_type": "BUY", "trade_date": "2023-03-01", "units": 100,
             "apply_stamp_duty": False}
    preview = client.post("/api/holdings/transactions/preview", json={**draft, "replace_txn_id": buy["id"]}).json()
    assert preview["ok"] is False and "No units of this fund were held" in preview["error"]   # A's redemption, left with nothing to sell
    assert client.patch(f"/api/holdings/transactions/{buy['id']}", json=draft).status_code == 422
    # A purchase nothing depends on moves, and both portfolios follow.
    other = _buy(client, a, day="2023-04-03", units=10)
    moved = client.patch(f"/api/holdings/transactions/{other['id']}", json={**draft, "units": 10, "trade_date": "2023-04-03"})
    assert moved.status_code == 200, moved.text
    assert _summary(client, b)["positions"][0]["units"] == pytest.approx(10)
    assert _summary(client, a)["positions"][0]["units"] == pytest.approx(40)


def test_editing_only_a_note_keeps_units_amount_and_nav_exactly(client):
    """The form re-enters a purchase by its amount and an AMFI-priced row with its NAV blank.
    Resolving that again re-derived the units to 3 decimals and re-priced the row at
    whatever AMFI's history said by then."""
    pid = new_portfolio(client)
    row = _buy(client, pid, units=123.4567, apply_stamp_duty=True)       # a 4-decimal statement
    _sql("UPDATE nav_history SET nav = nav * 1.01 WHERE scheme_code = %s AND nav_date = '2023-03-01'", (EQ,))
    form = {"portfolio_id": pid, "scheme_code": EQ, "txn_type": "BUY", "trade_date": "2023-03-01",
            "amount": float(row["amount"]), "nav": None, "apply_stamp_duty": True, "notes": "renamed"}
    preview = client.post("/api/holdings/transactions/preview", json={**form, "replace_txn_id": row["id"]}).json()
    assert preview["ok"] and preview["rows"][0]["units"] == row["units"]
    edited = client.patch(f"/api/holdings/transactions/{row['id']}", json=form)
    assert edited.status_code == 200, edited.text
    t = edited.json()["transaction"]
    assert (t["units"], t["amount"], t["nav"], t["nav_source"], t["stamp_duty"], t["notes"]) == \
           (row["units"], row["amount"], row["nav"], "amfi_auto", row["stamp_duty"], "renamed")


def test_a_switch_leg_cant_be_edited_or_sent_alone(client):
    pid = new_portfolio(client)
    _buy(client, pid)
    legs = add_ok(client, portfolio_id=pid, scheme_code=EQ, txn_type="SWITCH", trade_date="2023-09-01",
                  redeem_all=True, switch_to_scheme_code=LIQ)["transactions"]
    r = client.patch(f"/api/holdings/transactions/{legs[0]['id']}", json={
        "portfolio_id": pid, "scheme_code": EQ, "txn_type": "REDEEM", "trade_date": "2023-09-01", "units": 1})
    assert r.status_code == 422 and "re-entering" in r.json()["detail"]["message"]
    # A lone SWITCH_IN would add units with no money crossing the portfolio: pure "gain".
    assert add_txn(client, portfolio_id=pid, scheme_code=LIQ, txn_type="SWITCH_IN", trade_date="2023-03-01",
                   amount=100000).status_code == 422
    assert add_txn(client, portfolio_id=pid, scheme_code=EQ, txn_type="SWITCH", trade_date="2023-09-04", units=1,
                   switch_to_scheme_code=LIQ, switch_in_date="2023-09-01").status_code == 422


def test_an_archived_portfolios_transactions_are_read_only(client):
    pid = new_portfolio(client)
    buy = _buy(client, pid)
    client.delete(f"/api/holdings/portfolios/{pid}")
    r = client.patch(f"/api/holdings/transactions/{buy['id']}", json={
        "portfolio_id": pid, "scheme_code": EQ, "txn_type": "BUY", "trade_date": "2023-03-01", "units": 100,
        "apply_stamp_duty": False, "notes": "x"})
    assert r.status_code == 422 and "archived" in r.json()["detail"]["message"]
    assert client.delete(f"/api/holdings/transactions/{buy['id']}").status_code == 422
    # The preview says so first, so the drawer never offers a Save the server will refuse.
    preview = client.post("/api/holdings/transactions/preview", json={
        "portfolio_id": pid, "scheme_code": EQ, "txn_type": "BUY", "trade_date": "2023-03-01", "units": 100,
        "apply_stamp_duty": False, "replace_txn_id": buy["id"]}).json()
    assert preview["ok"] is False and "archived" in preview["error"]


def test_every_ledger_write_runs_under_the_ledger_lock(client, monkeypatch):
    """The lock is what makes validate-then-write safe; dropping it from any one write path
    used to pass every test."""
    seen = []
    for name in ("insert_transactions", "update_transaction", "set_deleted", "restore_all"):
        def spy(*args, _real=getattr(hdb, name), _name=name, **kwargs):
            seen.append((_name, svc._LEDGER_LOCK.locked()))
            return _real(*args, **kwargs)
        monkeypatch.setattr(hdb, name, spy)
    pid = new_portfolio(client)
    buy = _buy(client, pid)
    assert client.patch(f"/api/holdings/transactions/{buy['id']}", json={
        "portfolio_id": pid, "scheme_code": EQ, "txn_type": "BUY", "trade_date": "2023-03-01", "units": 90,
        "apply_stamp_duty": False}).status_code == 200
    assert client.delete(f"/api/holdings/transactions/{buy['id']}").status_code == 200
    assert client.post(f"/api/holdings/transactions/{buy['id']}/restore").status_code == 200
    m = client.post("/api/holdings/sip-mandates", json={"portfolio_id": pid, "scheme_code": EQ, "amount": 1000,
                                                       "day_of_month": 6, "start_date": "2024-01-01",
                                                       "end_date": "2024-02-29"}).json()
    assert client.post(f"/api/holdings/sip-mandates/{m['id']}/generate", params={"confirm": True}).json()["count"] == 2
    backup = client.get("/api/holdings/backup.json").json()
    _empty_ledger()
    assert client.post("/api/holdings/restore", json=backup).status_code == 200
    assert {n for n, _ in seen} == {"insert_transactions", "update_transaction", "set_deleted", "restore_all"}
    assert all(locked for _, locked in seen), seen


# --- Valuation edge cases ----------------------------------------------------------------------

def test_a_fund_switched_out_on_the_latest_date_keeps_its_move_in_the_tile(client):
    """The tile sums every fund's 1D move, a fund closed today included (its units were held
    through the move); the table's footer must count it too -- see lib/holdings.ts."""
    pid = new_portfolio(client)
    _buy(client, pid, day="2024-06-03")
    add_ok(client, portfolio_id=pid, scheme_code=EQ, txn_type="SWITCH", trade_date="2024-12-31", redeem_all=True,
           switch_to_scheme_code=LIQ)
    body = _summary(client, pid)
    closed = next(p for p in body["positions"] if p["scheme_code"] == EQ)
    assert closed["is_closed"]
    assert closed["day_change"] == pytest.approx(100 * (nav_on(EQ, d(2024, 12, 31)) - nav_on(EQ, d(2024, 12, 30))))
    assert body["kpis"]["day_change"] == pytest.approx(sum(p["day_change"] for p in body["positions"]))


def test_switch_money_in_transit_stays_in_the_book(client):
    """A full switch whose IN leg is allotted two days after the OUT leg. The money never left
    the portfolio; counted as gone, the index fell 100% and stayed at zero."""
    pid = new_portfolio(client)
    _buy(client, pid, day="2024-06-03", amount=10000, units=None)
    add_ok(client, portfolio_id=pid, scheme_code=EQ, txn_type="SWITCH", trade_date="2024-09-02", redeem_all=True,
           switch_to_scheme_code=LIQ, switch_in_date="2024-09-04")
    twr = client.get(f"/api/holdings/portfolios/{pid}/performance").json()["series"]["twr_index"]
    assert min(twr) > 90


def test_a_fully_exited_portfolio_still_has_an_xirr(client):
    pid = new_portfolio(client)
    buy = _buy(client, pid)
    sold = add_ok(client, portfolio_id=pid, scheme_code=EQ, txn_type="REDEEM", trade_date="2024-06-03",
                  redeem_all=True)["transactions"][0]
    k = _summary(client, pid)["kpis"]
    expected = portfolio_sim.xirr([(d(2023, 3, 1), -float(buy["amount"])), (d(2024, 6, 3), float(sold["amount"]))]) * 100
    assert k["xirr_note"] is None and k["xirr_pct"] == pytest.approx(expected, abs=1e-6)


def test_a_fund_with_no_nav_is_left_out_not_valued_at_zero(client):
    pid = new_portfolio(client)
    _buy(client, pid)
    liq = _buy(client, pid, code=LIQ, units=10)
    _sql("UPDATE summary_table SET latest_nav = NULL WHERE scheme_code = %s", (EQ,))
    svc.bump_ledger_version()
    body = _summary(client, pid)
    eq = next(p for p in body["positions"] if p["scheme_code"] == EQ)
    assert eq["flags"]["unpriced"] and eq["unrealised_gain"] is None
    k = body["kpis"]
    assert k["unpriced_funds"] == 1
    liq_cost = float(liq["units"]) * float(liq["nav"])
    assert k["invested"] == pytest.approx(liq_cost, abs=0.01)
    assert k["unrealised_gain"] == pytest.approx(k["current_value"] - liq_cost, abs=0.01)


def test_fund_info_falls_back_only_when_the_price_cache_is_missing(client, monkeypatch):
    pid = new_portfolio(client)
    _buy(client, pid)
    _sql("DROP TABLE summary_table CASCADE")
    svc.bump_ledger_version()
    body = _summary(client, pid)
    assert body["positions"][0]["flags"]["no_day_change"] is True
    assert body["kpis"]["funds_without_day_change"] == 1
    assert body["kpis"]["current_value"] == pytest.approx(100 * nav_on(EQ, d(2024, 12, 31)))
    # Anything else -- a lock timeout behind the rebuild, say -- fails the request as a 503
    # rather than a believable Rs 0.00 day that would then be cached.

    def timed_out(*_args, **_kwargs):
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    monkeypatch.setattr(hdb, "fetchdf", timed_out)
    svc.bump_ledger_version()
    assert client.get(f"/api/holdings/portfolios/{pid}/summary").status_code == 503


def test_a_failed_peer_query_is_flagged_and_not_cached(client, monkeypatch):
    """A category's peer returns that failed to load read as a zero peer return -- the funds
    "beat their peers" by their whole move -- and that was cached for 10 minutes, unsaid."""
    from app import quant_analytics

    pid = new_portfolio(client)
    _buy(client, pid, day="2024-06-03")
    real = quant_analytics.get_synthetic_category_benchmark

    def flaky(category, *args, **kwargs):
        if kwargs.get("raise_errors"):
            raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
        return real(category, *args, **kwargs)

    monkeypatch.setattr(quant_analytics, "get_synthetic_category_benchmark", flaky)
    k = _summary(client, pid)["kpis"]
    assert k["peer_comparison_incomplete"] == ["Equity Scheme - Flexi Cap Fund"]
    monkeypatch.undo()
    # Not kept: with the query back, the very next load is whole.
    assert _summary(client, pid)["kpis"]["peer_comparison_incomplete"] == []


def test_if_sold_now_profit_counts_a_reinvested_dividend_once(client):
    pid = new_portfolio(client)
    buy = _buy(client, pid, amount=10000, units=None)
    add_ok(client, portfolio_id=pid, scheme_code=EQ, txn_type="DIVIDEND_REINVEST", trade_date="2023-06-01", amount=500,
           apply_stamp_duty=False)
    pos = _summary(client, pid)["positions"][0]
    # Its units are already in what you'd get; it isn't money taken out as well.
    assert pos["sell_now_profit"] == pytest.approx(pos["sell_now_value"] - float(buy["amount"]), abs=0.01)


# --- The contract the page reads ----------------------------------------------------------------

# Hand-mirrored in frontend/lib/api/holdings.ts (HoldingsKpis, Position). A key renamed or
# dropped here would render "-" on the page rather than fail anywhere, so the set is pinned.
KPI_KEYS = {
    "current_value", "invested", "unrealised_gain", "unrealised_pct", "realised_gain", "dividend_income", "stamp_duty",
    "total_gain", "total_gain_pct", "net_contributed", "xirr_pct", "xirr_note", "xirr_available_on", "oldest_nav_date",
    "first_investment_date", "earning_now_pct", "earning_now_per_day", "earning_now_coverage_pct", "earning_now_days",
    "sell_now_value", "sell_now_stt", "sell_now_profit", "sell_now_excluded", "avg_days_invested",
    "days_since_first_investment", "twr_since_start_pct", "benchmark_since_start_pct", "excess_since_start_pp",
    "benchmark_name", "gain_vs_peers", "peer_gain", "day_change", "day_change_pct", "day_benchmark_pct",
    "weighted_ter_pct", "annual_fee", "ter_coverage_pct", "open_positions", "transactions", "ledger_errors",
    "unpriced_funds", "funds_without_day_change", "peer_comparison_incomplete",
}
POSITION_KEYS = {
    "scheme_code", "scheme_name", "fund_house", "category", "broad_category", "option_type", "expense_ratio", "ter_status",
    "latest_date", "sebi_category", "riskometer", "annual_fee", "plan_type", "display_name", "units", "avg_cost_nav",
    "cost_basis", "latest_nav", "current_value", "unrealised_gain", "unrealised_pct", "realised_gain", "dividend_income",
    "dividend_reinvested", "stamp_duty", "total_invested", "total_redeemed", "day_change", "day_base", "xirr_pct",
    "xirr_note", "xirr_available_on", "first_date", "txn_count", "portfolio_ids", "is_closed", "flags", "asset_class",
    "prev_nav_date", "weight_pct", "earning_now_pct", "earning_now_per_day", "earning_now_from", "earning_now_behind",
    "sell_now_value", "sell_now_stt", "sell_now_profit", "sell_now_note",
}


def test_the_summary_keeps_the_keys_the_page_reads(client):
    pid = new_portfolio(client)
    _buy(client, pid)
    body = _summary(client, pid)
    assert set(body["kpis"]) == KPI_KEYS
    assert set(body["positions"][0]) == POSITION_KEYS
    assert set(body["positions"][0]["flags"]) == {"stale_nav", "regular_plan", "split_adjusted", "unpriced", "no_day_change"}


# --- Drafts that can't be one trade ---------------------------------------------------------

def test_an_amount_and_units_that_disagree_are_refused(client):
    """Both may be given (a statement shows both), but they must be the same trade: 1 unit
    for Rs 10 lakh wrote a cash flow and a cost that disagreed by the whole amount."""
    pid = new_portfolio(client)
    nav = round(nav_on(EQ, d(2023, 3, 1)), 4)
    r = add_txn(client, portfolio_id=pid, scheme_code=EQ, txn_type="BUY", trade_date="2023-03-01", amount=1000000, units=1,
                apply_stamp_duty=False)
    assert r.status_code == 422 and "doesn't match" in r.json()["detail"]["message"]
    # A statement's own rounding (units to 3 decimals, the amount to the paisa) is fine.
    units = round(10000 / nav, 3)
    ok = add_txn(client, portfolio_id=pid, scheme_code=EQ, txn_type="BUY", trade_date="2023-03-01", amount=10000, units=units,
                 apply_stamp_duty=False)
    assert ok.status_code == 200, ok.text
    assert ok.json()["transactions"][0]["units"].startswith(f"{units:.3f}")


def test_a_redemption_from_a_fund_not_held_is_refused_however_small(client):
    """A crumb-sized redemption on an empty holding passed the rounding tolerance and booked
    its whole amount as realised gain."""
    pid = new_portfolio(client)
    _buy(client, pid, code=LIQ, units=10)
    r = add_txn(client, portfolio_id=pid, scheme_code=EQ, txn_type="REDEEM", trade_date="2023-06-01", units=0.0004,
                amount=100000)
    assert r.status_code == 422


# --- Guards ----------------------------------------------------------------------------------

def test_unarchiving_onto_a_name_now_in_use_is_a_conflict(client):
    old = new_portfolio(client, "Retirement")
    client.delete(f"/api/holdings/portfolios/{old}")
    new_portfolio(client, "Retirement")
    assert client.patch(f"/api/holdings/portfolios/{old}", json={"archived": False}).status_code == 409


def test_a_name_clash_the_check_misses_is_still_a_409(client, monkeypatch):
    """Two creates racing past the read-then-write name check: the database's unique index
    refuses the second, which must reach the page as a conflict, not a 500."""
    new_portfolio(client, "Twin")
    monkeypatch.setattr(holdings_router, "_unique_portfolio_name", lambda name, exclude_id=None: name.strip())
    r = client.post("/api/holdings/portfolios", json={"name": "twin"})
    assert r.status_code == 409 and "already active" in r.json()["detail"]


def test_amounts_a_column_cant_hold_are_refused_with_the_field(client):
    pid = new_portfolio(client)
    for bad in ({"amount": 1e16}, {"units": 1e14}):
        r = add_txn(client, portfolio_id=pid, scheme_code=EQ, txn_type="BUY", trade_date="2023-03-01", **bad)
        assert r.status_code == 422, (bad, r.text)
    # JSON's non-standard NaN / Infinity tokens, which Python's parser accepts.
    for token in ('"amount": Infinity', '"amount": 100, "nav": NaN'):
        body = f'{{"portfolio_id": {pid}, "scheme_code": {EQ}, "txn_type": "BUY", "trade_date": "2023-03-01", {token}}}'
        r = client.post("/api/holdings/transactions", content=body, headers={"content-type": "application/json"})
        assert r.status_code == 422, (token, r.text)


def test_the_ledger_is_never_left_in_the_browsers_cache(client):
    new_portfolio(client)
    for path in ("/api/holdings/portfolios", "/api/holdings/backup.json", "/api/holdings/portfolios/all/export.csv",
                 "/api/holdings/portfolios/all/summary"):
        assert client.get(path).headers["cache-control"] == "no-store", path


def test_backup_status_says_when_a_backup_is_due(client):
    status = lambda: client.get("/api/holdings/backup-status").json()  # noqa: E731
    s = status()
    assert s["last_backup_at"] is None and s["backup_due"] is False      # nothing to back up yet
    pid = new_portfolio(client)
    s = status()
    assert s["changed_since_backup"] and s["backup_due"]
    client.get("/api/holdings/backup.json")
    s = status()
    assert s["last_backup_at"] and not s["changed_since_backup"] and not s["backup_due"]
    _buy(client, pid)
    s = status()
    assert s["changed_since_backup"] and not s["backup_due"]           # changed, but the backup is a minute old


def test_monte_carlo_horizon_is_capped_at_what_the_page_offers(client):
    pid = new_portfolio(client)
    assert client.get(f"/api/holdings/portfolios/{pid}/monte-carlo", params={"years": 40, "sims": 5000}).status_code == 422


# --- Insights and alerts ---------------------------------------------------------------------------

def test_insights_say_which_checks_could_not_run(client, monkeypatch):
    pid = new_portfolio(client)
    _buy(client, pid)

    def broken(*_args, **_kwargs):
        raise RuntimeError("risk engine down")
    monkeypatch.setattr(risk_svc, "risk", broken)
    body = client.get(f"/api/holdings/portfolios/{pid}/insights").json()
    assert body["unavailable"] == ["drawdown", "overlap"]


def test_a_new_rule_is_kept_when_evaluating_it_fails(client, monkeypatch):
    def broken(_rule):
        raise RuntimeError("summary failed")
    monkeypatch.setattr(insights_svc, "evaluate_rule", broken)
    r = client.post("/api/holdings/alert-rules", json={"kind": "drawdown", "threshold": 10})
    assert r.status_code == 200 and r.json()["evaluated"] is False
    assert len(client.get("/api/holdings/alert-rules").json()["rules"]) == 1


def test_drift_rules_are_no_longer_offered(client):
    assert "drift" not in client.get("/api/holdings/alert-rules").json()["kinds"]
    assert client.post("/api/holdings/alert-rules", json={"kind": "drift"}).status_code == 422


def test_a_zero_threshold_means_the_default_not_every_day():
    assert insights_svc._threshold({"threshold": 0}, 10.0) == 10.0
    assert insights_svc._threshold({"threshold": None}, 30.0) == 30.0
    assert insights_svc._threshold({"threshold": 15}, 10.0) == 15.0


def test_alerts_wait_for_the_full_syncs_rebuild(monkeypatch):
    """sync_all runs the NAV sync with refresh_summary=False, then split normalisation and
    the summary rebuild. Alerts judged in between read half-old data -- an unadjusted split
    as a -90% fall -- so they run at the end instead."""
    calls = []
    monkeypatch.setattr(amfi_sync, "_sync_daily_nav_impl", lambda refresh_summary=True: (True, "ok"))
    monkeypatch.setattr(amfi_sync, "_record_sync_result", lambda *args: None)
    monkeypatch.setattr(amfi_sync, "_evaluate_holdings_alerts", lambda: calls.append("evaluated"))
    amfi_sync.sync_daily_nav(refresh_summary=False)
    assert calls == []
    amfi_sync.sync_daily_nav(refresh_summary=True)
    assert calls == ["evaluated"]


def test_backups_are_read_from_one_snapshot(client, monkeypatch):
    """Every table comes from one REPEATABLE READ snapshot, so a write landing mid-download
    can't leave child rows whose parent is missing."""
    statements = []
    real = connection.PooledCursor.execute

    def spy(self, query, params=None):
        statements.append(str(query))
        return real(self, query, params)
    monkeypatch.setattr(connection.PooledCursor, "execute", spy)
    hdb.dump_all()
    assert statements[0].startswith("BEGIN ISOLATION LEVEL REPEATABLE READ")


@pytest.mark.parametrize("method,path", [
    ("get", "/api/holdings/portfolios/1/targets"),
    ("put", "/api/holdings/portfolios/1/targets"),
    ("get", "/api/holdings/portfolios/1/rebalance?new_money=100"),
    ("patch", "/api/holdings/alert-rules/1"),
    ("post", "/api/holdings/alerts/evaluate"),
    ("get", "/api/holdings/nav-lookup?scheme_code=1&date=2024-01-01"),
])
def test_the_removed_routes_are_gone(client, method, path):
    """Nothing in the page called them; targets, rebalancing and drift had no editor."""
    assert getattr(client, method)(path).status_code in (404, 405)
