"""Tests for app/data_audit.py: the row-level audit that names contradictory rows.

Each check is proven twice over: a deliberately broken row it must catch, and near-miss
rows it must not (a "Dividend Yield" fund's Growth option, "Regular Savings Fund - Direct
Plan", a segregated portfolio's zero NAV). A check that flags genuine data teaches the
reader to ignore it, which is worse than having no check.
"""

import datetime
import json

import pytest

from app import data_audit
from app.db import connection
from app.db import queries as db

TODAY = datetime.date.today()
RECENT = TODAY - datetime.timedelta(days=1)


def _scheme(code, name, plan="Direct", option="Growth", isin=None, isin_re=None,
            ter=None, ter_status=None, as_of=None, parts=None):
    base, brokerage, transaction, levies = parts or (None, None, None, None)
    return (code, name, "AMC", "Equity Scheme - Large Cap Fund", plan, option, isin, isin_re,
            ter, ter_status, as_of, base, brokerage, transaction, levies)


def _seed(schemes, navs=(), ter_rows=()):
    """Inserts rows, gives every scheme without an explicit NAV a positive one yesterday so
    it counts as active, then builds summary_table (which is what defines "active")."""
    con = connection.get_connection()
    try:
        con.executemany(
            """INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type,
                   option_type, isin, isin_reinvestment, expense_ratio, ter_status, ter_as_of_date,
                   ter_base_expense_ratio, ter_brokerage_cost_pct, ter_transaction_cost_pct,
                   ter_statutory_levies_pct)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            list(schemes),
        )
        explicit = {code for code, _, _ in navs}
        rows = list(navs) + [(s[0], RECENT, 10.0) for s in schemes if s[0] not in explicit]
        con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", rows)
        if ter_rows:
            con.executemany(
                "INSERT INTO ter_history (scheme_code, ter_date, valid_to, total_ter_pct) VALUES (%s, %s, %s, %s)",
                list(ter_rows))
    finally:
        con.close()
    db.refresh_summary_table()


def _clean_schemes():
    fresh = TODAY - datetime.timedelta(days=5)
    return [
        _scheme(101, "Clean Equity Fund", "Direct", "Growth", "INF000A01011", None, 0.5, "official", fresh,
                (0.3, 0.1, 0.05, 0.05)),
        _scheme(102, "Clean Equity Fund", "Regular", "Growth", "INF000A01029", None, 1.5, "official", fresh),
        _scheme(103, "Clean Equity Fund", "Direct", "IDCW", "INF000A01037", "INF000A01045", 0.5, "official", fresh),
        _scheme(104, "Clean Equity Fund", "Regular", "IDCW", "INF000A01052", "INF000A01060", 1.5, "official", fresh),
        # Near misses: every one of these is genuine and must not be flagged.
        _scheme(105, "Tata Dividend Yield Fund", "Direct", "Growth", "INF000A01078"),
        _scheme(106, "IIFL Dividend Opportunities Index Fund", "Regular", "Growth", "INF000A01086"),
        _scheme(107, "Nippon India Growth Fund", "Direct", "IDCW", "INF000A01094", "INF000A01102"),
        _scheme(108, "Sundaram Regular Savings Fund - Direct Plan - Growth Option", "Direct", "Growth", "INF000A01110"),
        _scheme(109, "PGIM Banking and PSU Debt Fund - Direct Plan - Regular Dividend", "Direct", "IDCW",
                "INF000A01128", "INF000A01136"),
        _scheme(110, "Indiabulls Liquid Fund - Direct Plan - Growth - Unclaimed Dividend > 3 Yrs", "Direct",
                "Growth", "INF000A01144"),
        _scheme(111, "UTI Credit Risk Fund (Segregated - 06032020)", "Regular", "Growth", "INF000A01151"),
    ]


def _clean_navs():
    # A segregated portfolio of written-off paper publishes exactly 0: true, not corrupt.
    return [(111, RECENT, 0.0)]


def _by_name(run):
    return {c["name"]: c for c in run["checks"]}


def _codes(check):
    return {s["scheme_code"] for s in check["samples"]}


def test_latest_is_empty_before_any_run(pg_db):
    assert data_audit.latest_audit() == {"empty": True}


def test_clean_dataset_has_no_errors_and_no_warnings(pg_db):
    _seed(_clean_schemes(), _clean_navs())
    run = data_audit.run_data_audit(trigger="test")
    checks = _by_name(run)

    assert run["summary"]["errors"] == 0 and run["summary"]["error_rows"] == 0
    assert run["summary"]["check_failures"] == 0
    for c in run["checks"]:
        if c["severity"] == "error":
            assert c["status"] == "pass" and c["count"] == 0, c
    flagged_warnings = [c["name"] for c in run["checks"] if c["severity"] == "warning" and c["status"] == "flagged"]
    assert flagged_warnings == []
    assert checks["bad_recent_nav"]["detail"]["segregated_zero_nav_schemes_excluded"] == 1


def test_every_broken_row_is_caught(pg_db):
    fresh = TODAY - datetime.timedelta(days=5)
    stale = TODAY - datetime.timedelta(days=60)
    old = TODAY - datetime.timedelta(days=200)
    broken = [
        # 1. reinvestment ISIN on a scheme labelled Growth
        _scheme(201, "Broken ISIN Fund", "Regular", "Growth", "INF000B01011", "INF000B01029"),
        # 3. plan contradicts name, both ways
        _scheme(202, "Mislabel Fund - Direct Plan - Growth", "Regular", "Growth", "INF000B01037"),
        _scheme(203, "Other Fund - Regular Plan - Growth", "Direct", "Growth", "INF000B01045"),
        # 4. Direct dearer than its Regular twin (names differ only by the plan words)
        _scheme(204, "Swapped Fund - Direct Plan", "Direct", "Growth", "INF000B01052", None, 2.0, "official", fresh),
        _scheme(205, "Swapped Fund - Regular Plan", "Regular", "Growth", "INF000B01060", None, 1.0, "official", fresh),
        # 7. two active codes sharing an ISIN, and an ISIN that is another's reinvestment ISIN
        _scheme(206, "Twin Identity Fund A", "Direct", "Growth", "INF000C01011"),
        _scheme(207, "Twin Identity Fund B", "Direct", "Growth", "INF000C01011"),
        _scheme(208, "Crossed ISIN Fund A", "Direct", "Growth", "INF000C01029"),
        _scheme(209, "Crossed ISIN Fund B", "Direct", "IDCW", "INF000C01037", "INF000C01029"),
        # 9. zero, NaN and NULL NAVs on active schemes
        _scheme(210, "Zero NAV Fund", "Direct", "Growth", "INF000B01078"),
        _scheme(211, "NaN NAV Fund", "Direct", "Growth", "INF000B01086"),
        _scheme(212, "Null NAV Fund", "Direct", "Growth", "INF000B01094"),
        _scheme(213, "Negative Segregated Fund (Segregated - 01012020)", "Direct", "Growth", "INF000B01102"),
        # 2. name says Dividend, option says Growth; and a spelled-out IDCW labelled Other
        _scheme(214, "Sundaram Growth Fund-Dividend", "Regular", "Growth", "INF000B01110"),
        _scheme(215, "Principal Cash Fund - Monthly Income Distribution CUM Capital Withdrawal", "Regular",
                "Other", "INF000B01128"),
        # 5. TER out of bounds, both ends
        _scheme(216, "Pricey Fund", "Regular", "Growth", "INF000B01136", None, 4.5, "official", fresh),
        _scheme(217, "Negative TER Fund", "Regular", "Growth", "INF000B01144", None, -0.1, "official", fresh),
        # TER parts that do not add up to the total
        _scheme(218, "Bad Parts Fund", "Regular", "Growth", "INF000B01151", None, 1.0, "official", fresh,
                (0.5, 0.1, 0.1, 0.1)),
        # 6. official TER two months old on an active scheme
        _scheme(219, "Stale TER Fund", "Regular", "Growth", "INF000B01169", None, 1.2, "official", stale),
        # malformed ISIN placeholder
        _scheme(220, "Redeemed Placeholder Fund", "Regular", "Growth", "Redeemed"),
        # re-coded scheme: retired code and successor share an ISIN (info, not error)
        _scheme(221, "Old Name Fund", "Regular", "Other", "INF000D01011"),
        _scheme(222, "New Name Fund (Formerly Old Name Fund)", "Regular", "Growth", "INF000D01011"),
        # 10. active with no plan or option
        _scheme(223, "Unlabelled ETF", None, "", "INF000B01177"),
    ]
    navs = [
        (210, RECENT - datetime.timedelta(days=1), 0.0), (210, RECENT, 11.0),
        (211, RECENT, float("nan")),
        (212, RECENT, None),
        (213, RECENT, -1.0),
        (221, old, 10.0),
    ]
    ter_rows = [(999002, fresh, fresh, 1.0)]
    _seed(_clean_schemes() + broken, _clean_navs() + navs, ter_rows)
    con = connection.get_connection()
    try:
        # 8. orphans: history for scheme codes with no schemes row
        con.execute("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (999001, %s, 5.0), (999001, %s, 5.1)",
                    (RECENT - datetime.timedelta(days=1), RECENT))
    finally:
        con.close()

    run = data_audit.run_data_audit(trigger="test")
    c = _by_name(run)

    assert _codes(c["option_vs_isin"]) == {201}
    assert _codes(c["plan_vs_name"]) == {202, 203}
    assert c["direct_ter_above_regular"]["count"] == 1 and _codes(c["direct_ter_above_regular"]) == {204}
    pair = c["direct_ter_above_regular"]["samples"][0]["values"]
    assert pair["direct_ter_pct"] == 2.0 and pair["regular_ter_pct"] == 1.0 and "[205]" in pair["regular_twin"]
    assert c["duplicate_isin"]["count"] == 2 and _codes(c["duplicate_isin"]) == {206, 208}
    assert c["orphan_history"]["detail"] == {"nav_history_codes": 1, "ter_history_codes": 1}
    orphan = {s["scheme_code"]: s["values"] for s in c["orphan_history"]["samples"]}
    assert orphan[999001]["rows"] == 2 and orphan[999002]["table"] == "ter_history"
    assert _codes(c["bad_recent_nav"]) == {210, 211, 212, 213}
    assert c["bad_recent_nav"]["detail"]["segregated_zero_nav_schemes_excluded"] == 1
    assert _codes(c["option_vs_name"]) == {214, 215}
    assert c["option_vs_name"]["samples"][0]["scheme_code"] == 214, "Growth contradictions list first"
    assert _codes(c["ter_out_of_bounds"]) == {216, 217}
    assert _codes(c["ter_components_mismatch"]) == {218}
    assert _codes(c["stale_official_ter"]) == {219}
    assert _codes(c["malformed_isin"]) == {220}
    assert c["isin_recoded"]["count"] == 1 and _codes(c["isin_recoded"]) == {221}
    assert c["isin_recoded"]["detail"]["labels_disagree"] == 1
    assert _codes(c["unlabelled_active"]) == {223}
    assert c["unlabelled_active"]["samples"][0]["values"]["missing"] == "plan and option"

    for name in ("option_vs_isin", "plan_vs_name", "direct_ter_above_regular", "duplicate_isin",
                 "orphan_history", "bad_recent_nav"):
        assert c[name]["severity"] == "error" and c[name]["status"] == "flagged", name
    assert run["summary"]["errors"] == 6

    # Labels are always the app-wide format, never a bare name.
    assert c["option_vs_isin"]["samples"][0]["label"] == "Broken ISIN Fund (Regular - Growth) [201]"
    # The stored run reads back identically.
    assert data_audit.latest_audit() == json.loads(json.dumps(run))


def test_samples_are_capped(pg_db, monkeypatch):
    monkeypatch.setattr(data_audit, "SAMPLE_LIMIT", 3)
    _seed([_scheme(300 + i, f"Unlabelled {i}", None, None) for i in range(5)])
    check = _by_name(data_audit.run_data_audit())["unlabelled_active"]
    assert check["count"] == 5 and len(check["samples"]) == 3


def test_restatement_not_yet_run_is_reported_not_failed(pg_db):
    _seed(_clean_schemes())
    check = _by_name(data_audit.run_data_audit())["nav_restatement"]
    assert check["status"] == "not_run" and check["count"] is None


def test_restatement_suspects_are_reported_with_labels(pg_db):
    _seed(_clean_schemes())
    db.set_sync_meta_value("nav_restatement_last", json.dumps({
        "ran_at": "2026-09-24T23:40:00", "window_days": 14, "checked": 1000, "restated": 3,
        "gap_filled": 1, "split_scaled": 0, "suspect_count": 2,
        "suspect": [{"scheme_code": 101, "nav_date": "2026-09-20", "stored": 10.0, "amfi": 100.0, "ratio": 10.0},
                    {"scheme_code": 424242, "nav_date": "2026-09-21", "stored": 5.0, "amfi": 5.5, "ratio": 1.1}],
    }))
    check = _by_name(data_audit.run_data_audit())["nav_restatement"]
    assert check["status"] == "flagged" and check["count"] == 2 and check["severity"] == "warning"
    assert check["samples"][0]["label"] == "Clean Equity Fund (Direct - Growth) [101]"
    assert check["samples"][0]["values"]["ratio"] == 10.0
    assert check["samples"][1]["scheme_code"] == 424242  # unknown code still shown, not dropped
    assert check["detail"]["checked"] == 1000


def test_malformed_restatement_json_is_a_failed_check_not_a_crash(pg_db):
    _seed(_clean_schemes())
    db.set_sync_meta_value("nav_restatement_last", "{not json")
    run = data_audit.run_data_audit()
    assert _by_name(run)["nav_restatement"]["status"] == "error"
    assert run["summary"]["check_failures"] == 1


def _drop_provenance_columns():
    con = connection.get_connection()
    try:
        con.execute("ALTER TABLE schemes DROP COLUMN IF EXISTS plan_source, DROP COLUMN IF EXISTS option_source")
    finally:
        con.close()


def test_missing_provenance_columns_are_skipped(pg_db):
    _seed(_clean_schemes())
    _drop_provenance_columns()
    check = _by_name(data_audit.run_data_audit())["legacy_provenance"]
    assert check["status"] == "not_run" and check["count"] is None


def test_provenance_counts_values_of_unknown_origin(pg_db):
    _seed(_clean_schemes() + [_scheme(400, "No Plan Fund", None, None)])
    con = connection.get_connection()
    try:
        con.execute("ALTER TABLE schemes ADD COLUMN IF NOT EXISTS plan_source TEXT, "
                    "ADD COLUMN IF NOT EXISTS option_source TEXT")
        con.execute("UPDATE schemes SET plan_source = 'amfi', option_source = 'amfi'")
        con.execute("UPDATE schemes SET plan_source = NULL WHERE scheme_code = 101")
        con.execute("UPDATE schemes SET option_source = NULL WHERE scheme_code = 102")
        # A NULL source on a NULL value is an absent label, not one of unknown origin.
        con.execute("UPDATE schemes SET plan_source = NULL, option_source = NULL WHERE scheme_code = 400")
    finally:
        con.close()
    check = _by_name(data_audit.run_data_audit())["legacy_provenance"]
    assert check["status"] == "flagged" and _codes(check) == {101, 102}


def test_history_is_bounded(pg_db, monkeypatch):
    monkeypatch.setattr(data_audit, "MAX_RUNS_KEPT", 3)
    _seed(_clean_schemes())
    runs = [data_audit.run_data_audit(trigger=f"t{i}") for i in range(5)]
    con = connection.get_connection()
    try:
        kept = [r[0] for r in con.execute("SELECT DISTINCT run_id FROM data_audit_runs ORDER BY 1").fetchall()]
        per_run = con.execute("SELECT count(*) FROM data_audit_runs WHERE run_id = %s", (kept[-1],)).fetchone()[0]
    finally:
        con.close()
    assert kept == [r["run_id"] for r in runs[-3:]]
    assert per_run == len(data_audit.CHECKS)
    latest = data_audit.latest_audit()
    assert latest["run_id"] == runs[-1]["run_id"] and latest["trigger"] == "t4"


def test_a_crashing_check_is_recorded_and_the_rest_still_run(pg_db, monkeypatch):
    def boom(con, ctx):
        raise RuntimeError("synthetic failure")

    patched = tuple((n, t, s, d, boom if n == "plan_vs_name" else f) for n, t, s, d, f in data_audit.CHECKS)
    monkeypatch.setattr(data_audit, "CHECKS", patched)
    _seed(_clean_schemes())
    run = data_audit.run_data_audit()
    c = _by_name(run)
    assert c["plan_vs_name"]["status"] == "error" and "synthetic failure" in c["plan_vs_name"]["detail"]["reason"]
    assert c["option_vs_isin"]["status"] == "pass"
    assert run["summary"]["check_failures"] == 1
    assert data_audit.latest_audit()["run_id"] == run["run_id"]


def test_runs_without_a_summary_table(pg_db):
    """summary_table is a rebuildable cache and can be absent; "active" is then empty
    rather than the audit crashing."""
    _seed(_clean_schemes())
    con = connection.get_connection()
    try:
        con.execute("DROP TABLE IF EXISTS summary_table")
    finally:
        con.close()
    run = data_audit.run_data_audit()
    assert run["summary"]["check_failures"] == 0
