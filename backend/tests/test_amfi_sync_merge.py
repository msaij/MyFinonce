"""Tests for the single AMFI merge path (_merge_amfi_payload) and the backfill's
durable resume, both introduced when the three duplicated staging-table merges
were replaced by primary-key upserts. See db/bulk.py.

test_amfi_sync_write_path.py already covers sync_daily_nav end-to-end through
the real parser; this file goes at the merge's *semantics* directly -- the
precedence rules that used to live inside a wall of correlated subqueries and
now live in SCHEME_UPDATE, where a mistake is much quieter.
"""

import datetime

import pytest

from app import amfi_sync
from app.db import bulk
from app.db import connection
from app.db import queries as db


@pytest.fixture()
def db_con(pg_db):
    db.init_db()
    yield


def _scheme(code=111111, name="Test Fund - Direct Plan - Growth", house="Test AMC",
            category="Equity Scheme - Large Cap Fund", plan="Direct", option="Growth",
            isin="INF000001"):
    return {
        "scheme_code": code, "scheme_name": name, "fund_house": house,
        "category": category, "plan_type": plan, "option_type": option, "isin": isin,
    }


def _fetch(sql, params=()):
    con = connection.get_connection()
    try:
        con.execute(sql, params)
        return con.fetchall()
    finally:
        con.close()


def test_merge_inserts_schemes_and_navs(db_con):
    sch, nav = amfi_sync._merge_amfi_payload(
        {111111: _scheme()},
        [(111111, datetime.date(2025, 2, 1), 100.5)],
        label="test",
    )
    assert (sch, nav) == (1, 1)
    assert _fetch("SELECT scheme_name, isin FROM schemes")[0] == (
        "Test Fund - Direct Plan - Growth", "INF000001")
    assert _fetch("SELECT nav FROM nav_history")[0][0] == pytest.approx(100.5)


def test_merge_is_idempotent(db_con):
    payload = ({111111: _scheme()}, [(111111, datetime.date(2025, 2, 1), 100.5)])
    for _ in range(3):
        amfi_sync._merge_amfi_payload(*payload, label="test")
    assert _fetch("SELECT COUNT(*) FROM schemes")[0][0] == 1
    assert _fetch("SELECT COUNT(*) FROM nav_history")[0][0] == 1


def test_merge_updates_a_changed_nav_in_place(db_con):
    """AMFI restates NAVs, so a re-downloaded overlapping range must correct the
    stored value -- without duplicating the (scheme_code, nav_date) row."""
    amfi_sync._merge_amfi_payload({111111: _scheme()},
                                  [(111111, datetime.date(2025, 2, 1), 100.5)], label="t")
    amfi_sync._merge_amfi_payload({111111: _scheme()},
                                  [(111111, datetime.date(2025, 2, 1), 102.25)], label="t")
    rows = _fetch("SELECT nav FROM nav_history WHERE scheme_code = 111111")
    assert len(rows) == 1
    assert rows[0][0] == pytest.approx(102.25)


def test_merge_does_not_let_a_derived_ter_overwrite_an_official_one(db_con):
    """The precedence rule with real consequences: 'official' TER comes from AMFI's
    dated Regulation 66 disclosure, and the NAV-merge fallback must not replace it.
    Every daily sync re-submits fallback TER fields, so if this rule broke, one
    sync would quietly downgrade every officially-sourced expense ratio."""
    amfi_sync._merge_amfi_payload({111111: _scheme()},
                                  [(111111, datetime.date(2025, 2, 1), 100.0)], label="t")
    con = connection.get_connection()
    try:
        con.execute("""UPDATE schemes SET expense_ratio = 0.42, ter_status = 'official',
                       ter_source = 'AMFI TER portal' WHERE scheme_code = 111111""")
    finally:
        con.close()

    amfi_sync._merge_amfi_payload({111111: _scheme()},
                                  [(111111, datetime.date(2025, 2, 2), 101.0)], label="t")

    ratio, status, source = _fetch(
        "SELECT expense_ratio, ter_status, ter_source FROM schemes WHERE scheme_code = 111111")[0]
    assert ratio == pytest.approx(0.42)
    assert status == "official"
    assert source == "AMFI TER portal"


def test_merge_applies_fallback_ter_when_none_is_official(db_con, monkeypatch):
    """The other half of the same rule -- a scheme with no official TER must still
    receive the fallback from get_scheme_cost_specs, or the precedence guard
    would just be a write block.

    get_scheme_cost_specs is stubbed because the real function always returns
    expense_ratio=None / status 'unknown', which would make this assertion pass
    for the wrong reason."""
    monkeypatch.setattr(
        amfi_sync.costs_data, "get_scheme_cost_specs",
        lambda code, name, category, plan: {
            "expense_ratio": 1.25, "ter_status": "unverified", "ter_source": "test fallback",
            "ter_source_url": None, "ter_as_of_date": None,
        })
    amfi_sync._merge_amfi_payload({111111: _scheme()},
                                  [(111111, datetime.date(2025, 2, 1), 100.0)], label="t")
    ratio, status = _fetch(
        "SELECT expense_ratio, ter_status FROM schemes WHERE scheme_code = 111111")[0]
    assert ratio == pytest.approx(1.25)
    assert status == "unverified"


def test_merge_does_not_erase_fund_house_or_category_with_a_blank(db_con):
    """A chunk download can arrive without AMC banner lines, so fund_house and
    category come through empty. Overwriting good stored values with those blanks
    would strip the fields every grouping and filter in the app depends on."""
    amfi_sync._merge_amfi_payload({111111: _scheme()},
                                  [(111111, datetime.date(2025, 2, 1), 100.0)], label="t")
    amfi_sync._merge_amfi_payload(
        {111111: _scheme(house="", category="")},
        [(111111, datetime.date(2025, 2, 2), 101.0)], label="t")
    house, category = _fetch(
        "SELECT fund_house, category FROM schemes WHERE scheme_code = 111111")[0]
    assert house == "Test AMC"
    assert category == "Equity Scheme - Large Cap Fund"


def test_merge_accepts_an_updated_fund_house(db_con):
    amfi_sync._merge_amfi_payload({111111: _scheme()},
                                  [(111111, datetime.date(2025, 2, 1), 100.0)], label="t")
    amfi_sync._merge_amfi_payload({111111: _scheme(house="Renamed AMC")},
                                  [(111111, datetime.date(2025, 2, 2), 101.0)], label="t")
    assert _fetch("SELECT fund_house FROM schemes WHERE scheme_code = 111111")[0][0] == "Renamed AMC"


def test_merge_keeps_a_known_isin_when_a_later_feed_omits_it(db_con):
    amfi_sync._merge_amfi_payload({111111: _scheme(isin="INF000001")},
                                  [(111111, datetime.date(2025, 2, 1), 100.0)], label="t")
    amfi_sync._merge_amfi_payload({111111: _scheme(isin=None)},
                                  [(111111, datetime.date(2025, 2, 2), 101.0)], label="t")
    assert _fetch("SELECT isin FROM schemes WHERE scheme_code = 111111")[0][0] == "INF000001"


# --- reinvestment ISIN: clear when AMFI says none, keep when AMFI says nothing ----------

_HEADER_WITH_REINVEST = ("Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
                         "Scheme Name;Plan;Option;Net Asset Value;Date")
_HEADER_WITHOUT_REINVEST = "Scheme Code;ISIN Div Payout/ ISIN Growth;Scheme Name;Plan;Option;Net Asset Value;Date"


def _feed(header, *rows):
    return "Test Mutual Fund\n\n" + header + "\n" + "\n".join(rows) + "\n"


def _merge_feed(text, **kwargs):
    from app.amfi_client import AmfiClient

    schemes, navs = {}, []
    for meta, rec in AmfiClient().parse_amfi_nav_lines(text):
        schemes.setdefault(meta["scheme_code"], meta)
        navs.append((rec["scheme_code"], rec["nav_date"], rec["nav"]))
    return amfi_sync._merge_amfi_payload(schemes, navs, label="t", **kwargs)


def _reinvest(code=152965):
    return _fetch("SELECT isin_reinvestment FROM schemes WHERE scheme_code = %s", (code,))[0][0]


_IDCW_ROW = "152965;INF247L01DO0;INF247L01DP7;Digital India Fund;Direct Plan;IDCW;11.4082;24-Sep-2026"


def test_a_placeholder_in_a_present_reinvestment_column_clears_the_stored_isin(db_con):
    """The scheme genuinely lost its dividend option: AMFI's file still has the column and
    now says "-" for it. Under the old COALESCE the stale ISIN -- and the IDCW label it
    justifies -- survived every sync forever."""
    _merge_feed(_feed(_HEADER_WITH_REINVEST, _IDCW_ROW))
    assert _reinvest() == "INF247L01DP7"

    for placeholder in ("-", "", "None", "null"):
        _merge_feed(_feed(_HEADER_WITH_REINVEST, _IDCW_ROW))
        _merge_feed(_feed(_HEADER_WITH_REINVEST, _IDCW_ROW.replace("INF247L01DP7", placeholder)
                          .replace(";IDCW;", ";Growth;")))
        assert _reinvest() is None, f"placeholder {placeholder!r} must clear the stored ISIN"


def test_a_feed_without_the_reinvestment_column_keeps_the_stored_isin(db_con):
    _merge_feed(_feed(_HEADER_WITH_REINVEST, _IDCW_ROW))
    _merge_feed(_feed(_HEADER_WITHOUT_REINVEST,
                      "152965;INF247L01DO0;Digital India Fund;Direct Plan;IDCW;11.5;25-Sep-2026"))
    assert _reinvest() == "INF247L01DP7"


def test_a_new_scheme_never_stores_a_placeholder(db_con):
    """The hard requirement on the insert path: a fresh row holds NULL or a real ISIN, never
    whatever marker meant "clear" on its way through the merge."""
    rows = [f"{900000 + i};INF00000{i};{p};Fund {i};Direct Plan;Growth;10.0;24-Sep-2026"
            for i, p in enumerate(("-", "", "None", "null"))]
    _merge_feed(_feed(_HEADER_WITH_REINVEST, *rows))
    _merge_feed(_feed(_HEADER_WITHOUT_REINVEST, "910000;INF000099;Fund X;Direct Plan;Growth;10.0;24-Sep-2026"))

    stored = _fetch("SELECT scheme_code, isin_reinvestment FROM schemes ORDER BY scheme_code")
    assert len(stored) == 5
    assert all(value is None for _, value in stored), stored


def test_a_historical_merge_never_clears(db_con):
    """A blank in an old report describes the scheme then, not now -- the backfill and the
    per-AMC history merge with reinvestment_clears=False."""
    _merge_feed(_feed(_HEADER_WITH_REINVEST, _IDCW_ROW))
    _merge_feed(_feed(_HEADER_WITH_REINVEST, _IDCW_ROW.replace("INF247L01DP7", "-")),
                reinvestment_clears=False)
    assert _reinvest() == "INF247L01DP7"


def test_a_legacy_scheme_dict_without_the_flag_keeps_the_stored_isin(db_con):
    amfi_sync._merge_amfi_payload({111111: {**_scheme(), "isin_reinvestment": "INF000777"}},
                                  [(111111, datetime.date(2025, 2, 1), 100.0)], label="t")
    amfi_sync._merge_amfi_payload({111111: _scheme()},
                                  [(111111, datetime.date(2025, 2, 2), 101.0)], label="t")
    assert _reinvest(111111) == "INF000777"


# --- plan/option provenance ------------------------------------------------------------


def _provenance(code=111111):
    return _fetch("SELECT plan_type, plan_source, option_type, option_source FROM schemes"
                  " WHERE scheme_code = %s", (code,))[0]


def test_a_source_is_stored_with_its_value(db_con):
    amfi_sync._merge_amfi_payload(
        {111111: {**_scheme(), "plan_source": "amfi", "option_source": "name"}},
        [(111111, datetime.date(2025, 2, 1), 100.0)], label="t")
    assert _provenance() == ("Direct", "amfi", "Growth", "name")


def test_a_kept_value_keeps_its_source(db_con):
    """AMFI going silent keeps the stored value (COALESCE) -- and so must keep the source,
    or a value would end up credited to a signal that never produced it."""
    amfi_sync._merge_amfi_payload(
        {111111: {**_scheme(), "plan_source": "amfi", "option_source": "isin"}},
        [(111111, datetime.date(2025, 2, 1), 100.0)], label="t")
    amfi_sync._merge_amfi_payload(
        {111111: {**_scheme(plan=None, option=None), "plan_source": None, "option_source": None}},
        [(111111, datetime.date(2025, 2, 2), 101.0)], label="t")
    assert _provenance() == ("Direct", "amfi", "Growth", "isin")


def test_a_new_value_brings_its_own_source(db_con):
    amfi_sync._merge_amfi_payload(
        {111111: {**_scheme(), "plan_source": "name", "option_source": "name"}},
        [(111111, datetime.date(2025, 2, 1), 100.0)], label="t")
    amfi_sync._merge_amfi_payload(
        {111111: {**_scheme(plan="Regular", option="IDCW"), "plan_source": "amfi", "option_source": "isin"}},
        [(111111, datetime.date(2025, 2, 2), 101.0)], label="t")
    assert _provenance() == ("Regular", "amfi", "IDCW", "isin")


def test_a_value_stated_without_a_source_records_unknown_not_the_old_source(db_con):
    """The old source described the old writer. Leaving it beside a value some other caller
    just wrote would claim a provenance nobody established."""
    amfi_sync._merge_amfi_payload(
        {111111: {**_scheme(), "plan_source": "amfi", "option_source": "amfi"}},
        [(111111, datetime.date(2025, 2, 1), 100.0)], label="t")
    amfi_sync._merge_amfi_payload({111111: _scheme()},
                                  [(111111, datetime.date(2025, 2, 2), 101.0)], label="t")
    assert _provenance() == ("Direct", None, "Growth", None)


def test_the_parser_sources_reach_the_database(db_con):
    _merge_feed(_feed(_HEADER_WITH_REINVEST, _IDCW_ROW.replace(";IDCW;", ";Growth;")))
    assert _provenance(152965) == ("Direct", "amfi", "IDCW", "isin")


def test_merge_dedupes_repeated_nav_points_last_one_wins(db_con):
    """The feed can repeat a (scheme_code, nav_date) pair within one file. The
    upsert would survive it, but deduping first keeps the batch honest and matches
    the previous drop_duplicates() behaviour."""
    sch, nav = amfi_sync._merge_amfi_payload(
        {111111: _scheme()},
        [(111111, datetime.date(2025, 2, 1), 100.0),
         (111111, datetime.date(2025, 2, 1), 105.0)],
        label="t")
    assert nav == 1
    assert _fetch("SELECT nav FROM nav_history")[0][0] == pytest.approx(105.0)


def test_dedupe_nav_returns_primary_key_order(db_con):
    """Sorted output is what keeps the upsert's B-tree writes near-sequential --
    an ordering regression here is a pure-performance one, invisible in results."""
    rows = amfi_sync._dedupe_nav([
        (222, datetime.date(2025, 1, 2), 2.0),
        (111, datetime.date(2025, 3, 1), 3.0),
        (111, datetime.date(2025, 1, 1), 1.0),
    ])
    assert [(r[0], r[1]) for r in rows] == [
        (111, datetime.date(2025, 1, 1)),
        (111, datetime.date(2025, 3, 1)),
        (222, datetime.date(2025, 1, 2)),
    ]


def test_merge_stops_between_batches_and_keeps_committed_rows(db_con, monkeypatch):
    monkeypatch.setattr(bulk, "DEFAULT_BATCH_SIZE", 10)
    nav_rows = [(111111, datetime.date(2025, 1, 1) + datetime.timedelta(days=i), 100.0 + i)
                for i in range(100)]
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2

    _, nav = amfi_sync._merge_amfi_payload({111111: _scheme()}, nav_rows,
                                           label="t", should_stop=should_stop)
    assert nav == 20
    assert _fetch("SELECT COUNT(*) FROM nav_history")[0][0] == 20


# --- durable resume --------------------------------------------------------


def test_chunk_unit_is_derived_from_the_date_range_not_the_list_position(db_con):
    """Checkpoint keys must stay valid when the chunk list is regenerated with a
    different start year or a later end date -- an index-based key would silently
    mark the wrong ranges complete."""
    unit = amfi_sync._chunk_unit(datetime.date(2015, 1, 1), datetime.date(2015, 3, 30))
    assert unit == "2015-01-01..2015-03-30"


def test_backfill_skips_chunks_completed_in_a_previous_run(db_con, monkeypatch):
    """The whole point of the checkpoint table: a restarted backfill must not
    re-download an hour of work it already has."""
    chunks = [
        (datetime.date(2015, 1, 1), datetime.date(2015, 3, 30)),
        (datetime.date(2015, 3, 31), datetime.date(2015, 6, 27)),
        (datetime.date(2015, 6, 28), datetime.date(2015, 9, 24)),
    ]
    monkeypatch.setattr(amfi_sync, "generate_backfill_chunks", lambda start_year: chunks)
    monkeypatch.setattr(db, "refresh_summary_table", lambda: None)

    con = connection.get_connection()
    try:
        bulk.checkpoint_mark(con, amfi_sync.BACKFILL_JOB,
                             amfi_sync._chunk_unit(*chunks[0]), "done", rows_written=10)
    finally:
        con.close()

    processed = []
    monkeypatch.setattr(amfi_sync, "backfill_single_chunk",
                        lambda f, t: (processed.append((f, t)), (1, 5))[1])

    amfi_sync._historical_backfill_worker(2015, None)

    assert processed == chunks[1:], "the already-completed chunk should have been skipped"
    assert amfi_sync.get_backfill_status()["skipped_chunks"] == 1


def test_backfill_checkpoints_each_chunk_it_completes(db_con, monkeypatch):
    chunks = [(datetime.date(2015, 1, 1), datetime.date(2015, 3, 30))]
    monkeypatch.setattr(amfi_sync, "generate_backfill_chunks", lambda start_year: chunks)
    monkeypatch.setattr(db, "refresh_summary_table", lambda: None)
    monkeypatch.setattr(amfi_sync, "backfill_single_chunk", lambda f, t: (1, 5))

    amfi_sync._historical_backfill_worker(2015, None)
    assert amfi_sync.get_backfill_progress()["completed_chunks"] == 1


def test_backfill_does_not_checkpoint_a_failed_chunk(db_con, monkeypatch):
    """A failed chunk must be retried next run, not skipped as though done."""
    chunks = [(datetime.date(2015, 1, 1), datetime.date(2015, 3, 30))]
    monkeypatch.setattr(amfi_sync, "generate_backfill_chunks", lambda start_year: chunks)
    monkeypatch.setattr(db, "refresh_summary_table", lambda: None)

    def boom(f, t):
        raise RuntimeError("download failed")

    monkeypatch.setattr(amfi_sync, "backfill_single_chunk", boom)
    amfi_sync._historical_backfill_worker(2015, None)

    assert amfi_sync.get_backfill_progress()["completed_chunks"] == 0
    assert "download failed" in amfi_sync.get_backfill_status()["last_error"]


def test_backfill_does_not_checkpoint_a_chunk_cut_short_by_a_stop(db_con, monkeypatch):
    """A stopped chunk committed a correct *prefix* of its rows. Marking it done
    would leave a permanent hole that no later run ever fills."""
    chunks = [(datetime.date(2015, 1, 1), datetime.date(2015, 3, 30))]
    monkeypatch.setattr(amfi_sync, "generate_backfill_chunks", lambda start_year: chunks)
    monkeypatch.setattr(db, "refresh_summary_table", lambda: None)

    def stop_midway(f, t):
        amfi_sync.stop_historical_backfill()
        return (1, 5)

    monkeypatch.setattr(amfi_sync, "backfill_single_chunk", stop_midway)
    amfi_sync._historical_backfill_worker(2015, None)

    assert amfi_sync.get_backfill_progress()["completed_chunks"] == 0


class _FakeAmfi:
    """Stands in for AmfiClient so the chunk path can be driven without the network."""

    payload = ""

    def download_bulk_historical_report(self, from_date, to_date):
        return self.payload

    def parse_amfi_nav_lines(self, raw_text, default_amc=""):
        from app.amfi_client import AmfiClient

        return AmfiClient().parse_amfi_nav_lines(raw_text, default_amc)


AMFI_HTML_ERROR_PAGE = (
    "<!DOCTYPE html><html><head><title>View/Download NAV History</title></head>"
    "<body><form action='./DownloadNAVHistoryReport_Po.aspx'>" + "x" * 500 + "</form></body></html>"
)
AMFI_EMPTY_REPORT = "Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date\n" + " " * 120


def _with_payload(monkeypatch, payload):
    _FakeAmfi.payload = payload
    monkeypatch.setattr(amfi_sync, "AmfiClient", _FakeAmfi)


def test_a_chunk_that_downloads_no_report_is_not_checkpointed_as_done(db_con, monkeypatch):
    """AMFI serving its HTML form page (HTTP 200, parses to zero rows) must fail the
    chunk. Checkpointing it 'done' would make every later resume skip that range, so a
    whole backfill could report itself complete having ingested nothing."""
    chunks = [(datetime.date(2015, 1, 1), datetime.date(2015, 3, 30))]
    monkeypatch.setattr(amfi_sync, "generate_backfill_chunks", lambda start_year: chunks)
    monkeypatch.setattr(db, "refresh_summary_table", lambda: None)
    _with_payload(monkeypatch, AMFI_HTML_ERROR_PAGE)

    with pytest.raises(amfi_sync.ChunkDownloadError):
        amfi_sync.backfill_single_chunk(*chunks[0])

    amfi_sync._historical_backfill_worker(2015, None)
    assert amfi_sync.get_backfill_progress()["completed_chunks"] == 0
    assert "not a NAV report" in amfi_sync.get_backfill_status()["last_error"]


def test_an_empty_download_fails_the_chunk_rather_than_completing_it(db_con, monkeypatch):
    chunks = [(datetime.date(2015, 1, 1), datetime.date(2015, 3, 30))]
    monkeypatch.setattr(amfi_sync, "generate_backfill_chunks", lambda start_year: chunks)
    monkeypatch.setattr(db, "refresh_summary_table", lambda: None)
    _with_payload(monkeypatch, "")

    amfi_sync._historical_backfill_worker(2015, None)
    assert amfi_sync.get_backfill_progress()["completed_chunks"] == 0


def test_a_valid_report_with_no_rows_still_completes_the_chunk(db_con, monkeypatch):
    """A date range older than AMFI's own history is genuinely done: it must be
    checkpointed, or every future run would re-download it forever."""
    chunks = [(datetime.date(2001, 1, 1), datetime.date(2001, 3, 30))]
    monkeypatch.setattr(amfi_sync, "generate_backfill_chunks", lambda start_year: chunks)
    monkeypatch.setattr(db, "refresh_summary_table", lambda: None)
    _with_payload(monkeypatch, AMFI_EMPTY_REPORT)

    assert amfi_sync.backfill_single_chunk(*chunks[0]) == (0, 0)
    amfi_sync._historical_backfill_worker(2001, None)
    assert amfi_sync.get_backfill_progress()["completed_chunks"] == 1


def test_start_backfill_with_resume_false_clears_prior_progress(db_con, monkeypatch):
    con = connection.get_connection()
    try:
        bulk.checkpoint_mark(con, amfi_sync.BACKFILL_JOB, "2015-01-01..2015-03-30", "done")
    finally:
        con.close()
    monkeypatch.setattr(amfi_sync, "generate_backfill_chunks", lambda start_year: [])
    monkeypatch.setattr(db, "refresh_summary_table", lambda: None)

    assert amfi_sync.start_historical_backfill(2015, resume=False) is True
    # The clear happens synchronously, before the worker thread is started.
    assert amfi_sync.get_backfill_progress()["completed_chunks"] == 0
