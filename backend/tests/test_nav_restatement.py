"""Tests for amfi_sync.restate_recent_navs -- re-verifying a trailing window of stored
NAVs against AMFI's history report.

The daily file only carries today, so without this a NAV AMFI later corrects stays wrong
forever. The hard part is not applying corrections, it is refusing the wrong ones:
nav_history is split-normalised in place, so a pre-split row legitimately differs from
AMFI's raw figure by a clean factor, and writing the raw value back would make the split
normaliser rescale everything before it a second time. Several tests below exist only to
pin that refusal.
"""

import datetime
import json

import pytest

from app import amfi_sync
from app.amfi_client import AmfiClient
from app.db import connection
from app.db import queries as db

TODAY = datetime.date(2026, 9, 24)
D = datetime.date


@pytest.fixture()
def db_con(pg_db):
    db.init_db()
    yield


def _report(rows):
    """AMFI's history-report layout, one row per (code, date, nav)."""
    lines = [
        "Open Ended Schemes ( Equity Scheme - Large Cap Fund )",
        "",
        "Test Mutual Fund",
        "",
        "Scheme Code;Scheme Name;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
        "Net Asset Value;Repurchase Price;Sale Price;Date",
    ]
    lines += [f"{code};Fund {code};-;-;{nav};0;0;{d.strftime('%d-%b-%Y')}" for code, d, nav in rows]
    return "\n".join(lines) + "\n"


def _amfi_serves(monkeypatch, payload):
    class FakeAmfi:
        def download_bulk_historical_report(self, from_date, to_date):
            return payload

        def parse_amfi_nav_lines(self, raw_text, default_amc=""):
            return AmfiClient().parse_amfi_nav_lines(raw_text, default_amc)

    monkeypatch.setattr(amfi_sync, "AmfiClient", FakeAmfi)


def _seed(schemes, navs):
    con = connection.get_connection()
    try:
        con.executemany("INSERT INTO schemes (scheme_code, scheme_name) VALUES (%s, %s)",
                        [(c, f"Fund {c}") for c in schemes])
        con.executemany("INSERT INTO nav_history (scheme_code, nav_date, nav) VALUES (%s, %s, %s)", navs)
    finally:
        con.close()


def _navs(code):
    con = connection.get_connection()
    try:
        return {d: n for d, n in con.execute(
            "SELECT nav_date, nav FROM nav_history WHERE scheme_code = %s ORDER BY nav_date", (code,)).fetchall()}
    finally:
        con.close()


def _meta():
    raw = db.get_sync_meta_values([amfi_sync.NAV_RESTATEMENT_META_KEY]).get(amfi_sync.NAV_RESTATEMENT_META_KEY)
    return json.loads(raw) if raw else None


# --- the three verdicts -----------------------------------------------------------------


def test_a_small_restatement_is_applied(db_con, monkeypatch):
    _seed([100001], [(100001, D(2026, 9, 10), 100.0)])
    _amfi_serves(monkeypatch, _report([(100001, D(2026, 9, 10), 100.5)]))  # 0.5%

    result = amfi_sync.restate_recent_navs(today=TODAY)

    assert (result["checked"], result["restated"]) == (1, 1)
    assert _navs(100001)[D(2026, 9, 10)] == pytest.approx(100.5)


def test_an_identical_nav_is_checked_but_not_counted_as_restated(db_con, monkeypatch):
    _seed([100001], [(100001, D(2026, 9, 10), 100.1234)])
    _amfi_serves(monkeypatch, _report([(100001, D(2026, 9, 10), 100.1234)]))

    result = amfi_sync.restate_recent_navs(today=TODAY)
    assert (result["checked"], result["restated"], result["suspect_count"]) == (1, 0, 0)


def test_a_split_scaled_row_is_classified_and_never_overwritten(db_con, monkeypatch):
    """Stored on the post-split basis (a tenth of AMFI's raw pre-split figure, as the
    normaliser leaves it). Overwriting it is the double-scaling hazard."""
    _seed([100002], [(100002, D(2026, 9, 10), 10.02)])
    _amfi_serves(monkeypatch, _report([(100002, D(2026, 9, 10), 100.2)]))

    result = amfi_sync.restate_recent_navs(today=TODAY)

    assert (result["split_scaled"], result["restated"], result["suspect_count"]) == (1, 0, 0)
    assert _navs(100002)[D(2026, 9, 10)] == pytest.approx(10.02)


def test_an_unexplained_large_difference_is_suspect_and_never_overwritten(db_con, monkeypatch):
    _seed([100003], [(100003, D(2026, 9, 10), 100.0)])
    _amfi_serves(monkeypatch, _report([(100003, D(2026, 9, 10), 107.53)]))  # ~7%

    result = amfi_sync.restate_recent_navs(today=TODAY)

    assert (result["suspect_count"], result["restated"], result["split_scaled"]) == (1, 0, 0)
    assert _navs(100003)[D(2026, 9, 10)] == pytest.approx(100.0)
    sample = _meta()["suspect"][0]
    assert sample == {"scheme_code": 100003, "nav_date": "2026-09-10", "stored": 100.0,
                      "amfi": 107.53, "ratio": pytest.approx(100.0 / 107.53, abs=1e-6)}


def test_a_ratio_just_off_a_clean_factor_is_suspect_not_split_scaled(db_con, monkeypatch):
    """Split scaling explains a *clean* factor only; 1% off 10x is not one."""
    _seed([100004], [(100004, D(2026, 9, 10), 10.1)])
    _amfi_serves(monkeypatch, _report([(100004, D(2026, 9, 10), 100.0)]))

    result = amfi_sync.restate_recent_navs(today=TODAY)
    assert (result["suspect_count"], result["split_scaled"]) == (1, 0)


# --- gap fill ---------------------------------------------------------------------------


def test_a_missing_day_inside_the_window_is_filled(db_con, monkeypatch):
    _seed([100005], [(100005, D(2026, 9, 9), 100.0), (100005, D(2026, 9, 11), 100.4)])
    _amfi_serves(monkeypatch, _report([(100005, D(2026, 9, 9), 100.0), (100005, D(2026, 9, 10), 100.2),
                                       (100005, D(2026, 9, 11), 100.4)]))

    result = amfi_sync.restate_recent_navs(today=TODAY)

    assert result["gap_filled"] == 1
    assert _navs(100005)[D(2026, 9, 10)] == pytest.approx(100.2)


def test_a_day_outside_the_window_or_for_an_unknown_scheme_is_not_filled(db_con, monkeypatch):
    """Outside the window is not this step's business; a scheme the schemes table has never
    seen belongs to the daily sync, which brings its metadata with it."""
    _seed([100006], [])
    _amfi_serves(monkeypatch, _report([(100006, D(2026, 8, 1), 90.0), (777777, D(2026, 9, 10), 50.0)]))

    result = amfi_sync.restate_recent_navs(today=TODAY)

    assert result["gap_filled"] == 0
    assert _navs(100006) == {} and _navs(777777) == {}


# --- the double-scaling hazard ------------------------------------------------------------

#: AMFI's raw series for a fund that splits 10:1 on 2026-09-04. Two days sit outside the
#: 30-day window; 09-02 will be missing from the database.
SPLIT_FUND = 300001
RAW = [
    (SPLIT_FUND, D(2026, 8, 10), 100.00),
    (SPLIT_FUND, D(2026, 8, 11), 100.10),
    (SPLIT_FUND, D(2026, 9, 1), 100.20),
    (SPLIT_FUND, D(2026, 9, 2), 100.30),
    (SPLIT_FUND, D(2026, 9, 3), 100.40),
    (SPLIT_FUND, D(2026, 9, 4), 10.06),
    (SPLIT_FUND, D(2026, 9, 5), 10.07),
]
OUTSIDE = [D(2026, 8, 10), D(2026, 8, 11)]


def _seed_normalised_split_fund():
    """The state the app is really in: raw rows ingested (minus 09-02), then the real
    normaliser has rescaled everything before the split onto the new basis."""
    _seed([SPLIT_FUND], [r for r in RAW if r[1] != D(2026, 9, 2)])
    assert db.normalize_nav_splits() > 0
    before = _navs(SPLIT_FUND)
    assert before[D(2026, 9, 1)] == pytest.approx(10.04, abs=1e-3), "fixture: pre-split rows are rescaled"
    return before


def test_restatement_then_normalisation_does_not_double_scale(db_con, monkeypatch):
    before = _seed_normalised_split_fund()
    _amfi_serves(monkeypatch, _report([r for r in RAW if r[1] not in OUTSIDE]))

    result = amfi_sync.restate_recent_navs(today=TODAY)
    adjusted = db.normalize_nav_splits()
    after = _navs(SPLIT_FUND)

    assert result["split_scaled"] == 2, "09-01 and 09-03 differ from AMFI by the split factor"
    assert result["restated"] == 0
    assert adjusted == 0, "nothing restatement wrote gave the normaliser a new event"
    for d in OUTSIDE:
        assert after[d] == before[d], f"{d} is outside the window and must be untouched"
    for d in (D(2026, 9, 1), D(2026, 9, 3)):
        assert after[d] == before[d]


def test_a_missing_pre_split_day_is_not_filled_with_a_raw_value(db_con, monkeypatch):
    """09-02 is missing, and AMFI's figure for it is on the pre-split basis while its
    stored neighbours are not. Inserting it would plant two artificial splits whose product
    is not exactly 1 -- and the normaliser would rescale all earlier history by it."""
    _seed_normalised_split_fund()
    _amfi_serves(monkeypatch, _report([r for r in RAW if r[1] not in OUTSIDE]))

    result = amfi_sync.restate_recent_navs(today=TODAY)

    assert (result["gap_filled"], result["gap_skipped"]) == (0, 1)
    assert D(2026, 9, 2) not in _navs(SPLIT_FUND)


def test_the_hazard_is_real_naively_rewriting_the_window_corrupts_older_history(db_con, monkeypatch):
    """Control for the test above: what the old "just re-merge the window" approach does.
    If this ever stops failing the history, the fixture no longer exercises the hazard."""
    before = _seed_normalised_split_fund()

    amfi_sync._merge_amfi_payload({SPLIT_FUND: {
        "scheme_code": SPLIT_FUND, "scheme_name": f"Fund {SPLIT_FUND}", "fund_house": "", "category": "",
        "plan_type": None, "option_type": None, "isin": None}},
        [r for r in RAW if r[1] not in OUTSIDE], label="naive")
    db.normalize_nav_splits()
    after = _navs(SPLIT_FUND)

    assert any(after[d] != before[d] for d in OUTSIDE), "rows outside the window were rescaled again"


# --- dry run, persistence, failure --------------------------------------------------------


def _mixed_fixture(monkeypatch):
    _seed([100001, 100002, 100003, 100005], [
        (100001, D(2026, 9, 10), 100.0), (100002, D(2026, 9, 10), 10.02),
        (100003, D(2026, 9, 10), 100.0), (100005, D(2026, 9, 9), 100.0),
    ])
    _amfi_serves(monkeypatch, _report([
        (100001, D(2026, 9, 10), 100.5), (100002, D(2026, 9, 10), 100.2),
        (100003, D(2026, 9, 10), 107.53), (100005, D(2026, 9, 9), 100.0), (100005, D(2026, 9, 10), 100.2),
    ]))


def test_a_dry_run_computes_the_same_counts_and_writes_nothing(db_con, monkeypatch):
    _mixed_fixture(monkeypatch)
    before = {c: _navs(c) for c in (100001, 100002, 100003, 100005)}

    dry = amfi_sync.restate_recent_navs(dry_run=True, today=TODAY)

    assert {c: _navs(c) for c in before} == before
    assert _meta() is None, "a dry run must not pose as the last real run"
    real = amfi_sync.restate_recent_navs(today=TODAY)
    for key in ("checked", "restated", "gap_filled", "split_scaled", "suspect_count"):
        assert dry[key] == real[key], key
    assert dry["dry_run"] is True


def test_the_persisted_summary_has_exactly_the_shape_the_audit_reads(db_con, monkeypatch):
    _mixed_fixture(monkeypatch)
    amfi_sync.restate_recent_navs(today=TODAY)
    meta = _meta()

    assert set(meta) == {"ran_at", "window_days", "checked", "restated", "gap_filled",
                         "split_scaled", "suspect_count", "suspect"}
    assert datetime.datetime.fromisoformat(meta["ran_at"])
    assert (meta["window_days"], meta["checked"], meta["restated"], meta["gap_filled"],
            meta["split_scaled"], meta["suspect_count"]) == (30, 4, 1, 1, 1, 1)
    assert set(meta["suspect"][0]) == {"scheme_code", "nav_date", "stored", "amfi", "ratio"}
    assert isinstance(meta["suspect"][0]["scheme_code"], int)


def test_the_suspect_sample_is_capped_at_fifty(db_con, monkeypatch):
    codes = list(range(500000, 500060))
    _seed(codes, [(c, D(2026, 9, 10), 100.0) for c in codes])
    _amfi_serves(monkeypatch, _report([(c, D(2026, 9, 10), 130.0) for c in codes]))

    amfi_sync.restate_recent_navs(today=TODAY)
    meta = _meta()
    assert meta["suspect_count"] == 60 and len(meta["suspect"]) == 50


def test_a_failed_sub_range_is_skipped_and_the_rest_still_reconciled(db_con, monkeypatch):
    """The window is fetched a week at a time; one piece failing must not throw away the
    others -- and must not be mistaken for "AMFI has no NAVs those days" either."""
    _seed([100001], [(100001, D(2026, 9, 10), 100.0), (100001, D(2026, 9, 22), 100.0)])
    good = _report([(100001, D(2026, 9, 10), 100.5), (100001, D(2026, 9, 22), 100.5)])

    class FlakyAmfi:
        def download_bulk_historical_report(self, from_date, to_date):
            return None if from_date <= D(2026, 9, 22) <= to_date else good

        def parse_amfi_nav_lines(self, raw_text, default_amc=""):
            return AmfiClient().parse_amfi_nav_lines(raw_text, default_amc)

    monkeypatch.setattr(amfi_sync, "AmfiClient", FlakyAmfi)
    result = amfi_sync.restate_recent_navs(today=TODAY)

    assert result["ok"] is True and len(result["failed_ranges"]) == 1
    assert (result["checked"], result["restated"]) == (1, 1)
    assert _navs(100001) == {D(2026, 9, 10): pytest.approx(100.5), D(2026, 9, 22): pytest.approx(100.0)}


def test_an_unusable_download_writes_nothing_and_reports_failure(db_con, monkeypatch):
    """AMFI's history endpoint has been seen serving its own HTML form with HTTP 200."""
    _seed([100001], [(100001, D(2026, 9, 10), 100.0)])
    _amfi_serves(monkeypatch, "<html><body>" + "x" * 500 + "</body></html>")

    result = amfi_sync.restate_recent_navs(today=TODAY)

    assert result["ok"] is False and result["checked"] == 0
    assert _meta() is None
    assert _navs(100001)[D(2026, 9, 10)] == pytest.approx(100.0)
