"""Tests for AmfiClient.parse_amfi_nav_lines' header/column handling.

Focused on the two-ISIN-column layout AMFI actually publishes, which the parser
mishandled until 2026-09-12. The feed carries both "ISIN Div Payout/ISIN Growth"
and "ISIN Div Reinvestment", and a given row populates exactly one of them
depending on its option type. The parser recorded only the first ISIN column it
saw, so every IDCW-Reinvestment scheme in every file silently lost its ISIN --
silently because the row still parsed, still produced a NAV, and still upserted
fine, just with isin = NULL.
"""

import datetime

import pytest

from app.amfi_client import AmfiClient


# The real NAVAll.txt column order: the two ISIN columns sit between the scheme
# code and the scheme name. Row 1 is a Growth plan (first ISIN populated, second
# "-"); row 2 is an IDCW Reinvestment plan (first "-", second populated); row 3
# has neither.
TWO_ISIN_COLUMNS = """Open Ended Schemes ( Equity Scheme - Large Cap Fund )

Test Mutual Fund

Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Repurchase Price;Sale Price;Date
111111;INF000001;-;Test Fund - Direct Plan - Growth;125.4321;0;0;01-Feb-2025
222222;-;INF000002;Test Fund - Direct Plan - IDCW Reinvestment;120.1234;0;0;01-Feb-2025
333333;-;-;Test Fund - Regular Plan - Growth;118.0000;0;0;01-Feb-2025
"""


def _parse(text, **kwargs):
    return list(AmfiClient().parse_amfi_nav_lines(text, **kwargs))


def _isin_by_code(text):
    return {meta["scheme_code"]: meta["isin"] for meta, _ in _parse(text)}


def test_isin_is_read_from_the_first_column_when_populated():
    assert _isin_by_code(TWO_ISIN_COLUMNS)[111111] == "INF000001"


def test_isin_is_read_from_the_second_column_for_reinvestment_rows():
    """The actual bug: this row's ISIN lives in the *second* ISIN column, and the
    parser only ever looked at the first."""
    assert _isin_by_code(TWO_ISIN_COLUMNS)[222222] == "INF000002"


def test_isin_is_none_when_no_column_has_one():
    assert _isin_by_code(TWO_ISIN_COLUMNS)[333333] is None


@pytest.mark.parametrize("placeholder", ["-", "", "None", "null"])
def test_isin_placeholders_are_normalized_to_none(placeholder):
    text = TWO_ISIN_COLUMNS.replace("111111;INF000001;-;", f"111111;{placeholder};{placeholder};")
    assert _isin_by_code(text)[111111] is None


def test_placeholder_in_the_first_column_does_not_mask_a_real_second_isin():
    """Guards the specific ordering mistake of taking the first column's value
    unconditionally: a "-" there must not win over a real ISIN beside it."""
    text = TWO_ISIN_COLUMNS.replace("222222;-;INF000002;", "222222;None;INF000002;")
    assert _isin_by_code(text)[222222] == "INF000002"


def test_the_rest_of_the_row_still_parses_correctly():
    """The ISIN change moves column indices around, so pin the neighbouring fields
    against an off-by-one."""
    records = _parse(TWO_ISIN_COLUMNS)
    assert len(records) == 3
    meta, nav = records[0]
    assert meta["scheme_code"] == 111111
    assert meta["scheme_name"] == "Test Fund - Direct Plan - Growth"
    assert meta["fund_house"] == "Test Mutual Fund"
    assert meta["category"] == "Equity Scheme - Large Cap Fund"
    assert meta["plan_type"] == "Direct"
    assert nav["nav"] == pytest.approx(125.4321)
    assert nav["nav_date"] == datetime.date(2025, 2, 1)


#: A real shape from NAVAll.txt: AMFI supplies Plan and Option columns but leaves them
#: empty, and the scheme name carries no hint either. About 40% of the file looks like this.
NO_PLAN_EVIDENCE = """Open Ended Schemes ( Income/Debt Oriented Schemes - Liquid Fund )

Test Mutual Fund

Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date
145834;INF247L01734;-;Test Liquid Fund;;;14.9763;22-Sep-2026
"""


def test_a_blank_plan_column_is_recorded_as_unknown_not_guessed_as_regular():
    """This used to answer "Regular" for every unlabelled row. The TER matcher picks the
    Direct or Regular expense ratio *from this field*, so a guess here silently gave every
    Direct plan in that 40% the Regular plan's costs."""
    meta, _ = _parse(NO_PLAN_EVIDENCE)[0]
    assert meta["plan_type"] is None
    assert meta["option_type"] is None


def test_an_explicit_plan_column_is_still_honoured():
    text = NO_PLAN_EVIDENCE.replace("Test Liquid Fund;;;", "Test Liquid Fund;Direct;Growth;")
    meta, _ = _parse(text)[0]
    assert (meta["plan_type"], meta["option_type"]) == ("Direct", "Growth")


def test_a_plan_named_only_in_the_scheme_name_is_still_read():
    text = NO_PLAN_EVIDENCE.replace("Test Liquid Fund;;;", "Test Liquid Fund - Regular Plan - IDCW;;;")
    meta, _ = _parse(text)[0]
    assert (meta["plan_type"], meta["option_type"]) == ("Regular", "IDCW")


#: The two Motilal Oswal Digital India Fund rows exactly as AMFI publishes them. Both are
#: stated "Direct Plan / Growth"; only the reinvestment ISIN distinguishes them, which is
#: why the app showed two funds with one identical label.
DIGITAL_INDIA_PAIR = """Open Ended Schemes ( Equity Scheme - Sectoral/ Thematic )

Motilal Oswal Mutual Fund

Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date
152964;INF247L01DN2;-;Motilal Oswal Digital India Fund;Direct Plan;Growth;11.4086;24-Sep-2026
152965;INF247L01DO0;INF247L01DP7;Motilal Oswal Digital India Fund;Direct Plan;Growth;11.4082;24-Sep-2026
"""


def test_a_reinvestment_isin_identifies_an_idcw_scheme_amfi_labelled_growth():
    """A registrar issues a reinvestment ISIN only to a scheme that has a dividend option,
    so that column outranks the hand-typed Option field. Without this the two rows below --
    genuinely different funds with different ISINs and different NAVs -- both rendered as
    "Motilal Oswal Digital India Fund (Direct - Growth)" and could only be told apart by
    their AMFI code."""
    rows = {meta["scheme_code"]: meta for meta, _ in _parse(DIGITAL_INDIA_PAIR)}

    assert rows[152964]["option_type"] == "Growth", "no reinvestment ISIN, so the Option column stands"
    assert rows[152965]["option_type"] == "IDCW", "carries INF247L01DP7 despite being published as Growth"
    assert rows[152964]["plan_type"] == rows[152965]["plan_type"] == "Direct"
    # The labels must now differ, which is the whole point.
    assert rows[152964]["option_type"] != rows[152965]["option_type"]


def test_the_reinvestment_isin_is_kept_as_the_evidence_for_the_option():
    """Storing it is what makes the classification auditable: otherwise a scheme reads as
    IDCW with nothing in the database explaining why, while AMFI's own file says Growth."""
    rows = {meta["scheme_code"]: meta for meta, _ in _parse(DIGITAL_INDIA_PAIR)}

    assert rows[152965]["isin_reinvestment"] == "INF247L01DP7"
    assert rows[152965]["isin"] == "INF247L01DO0", "the payout/growth ISIN is unchanged"
    assert rows[152964]["isin_reinvestment"] is None, "a placeholder is not an ISIN"


def test_a_reinvestment_isin_settles_a_blank_option_column():
    """410 rows carry a reinvestment ISIN with no Option stated at all -- previously these
    fell through to None and landed in the "Unspecified" bucket."""
    text = NO_PLAN_EVIDENCE.replace("INF247L01734;-;", "INF247L01734;INF247L01742;")
    meta, _ = _parse(text)[0]
    assert meta["option_type"] == "IDCW"


def test_a_growth_row_without_a_reinvestment_isin_is_untouched():
    """The rule must not reclassify the ordinary case: placeholders are not ISINs."""
    for placeholder in ("-", "", "None", "null"):
        text = NO_PLAN_EVIDENCE.replace("Test Liquid Fund;;;", "Test Liquid Fund;Direct;Growth;")
        text = text.replace("INF247L01734;-;", f"INF247L01734;{placeholder};")
        meta, _ = _parse(text)[0]
        assert meta["option_type"] == "Growth", f"placeholder {placeholder!r} must not mean IDCW"


def test_an_amc_line_without_the_word_fund_leaves_fund_house_blank():
    """Documents where the blank fund_house the merge has to defend against
    actually comes from: AMC lines are recognized by containing "fund", so an AMC
    styled "... Asset Management Company Ltd" is not recognized and every scheme
    under it parses with fund_house "". amfi_sync.SCHEME_UPDATE must therefore
    never let that blank overwrite a stored value -- see
    test_amfi_sync_merge.test_merge_does_not_erase_fund_house_or_category_with_a_blank."""
    text = TWO_ISIN_COLUMNS.replace("Test Mutual Fund", "Test Asset Management Company Ltd")
    metas = {meta["scheme_code"]: meta for meta, _ in _parse(text)}
    assert metas[111111]["fund_house"] == ""


def test_default_amc_fills_in_when_the_feed_names_no_amc():
    """The single-AMC 90-day report has no AMC banner at all, so the caller passes
    the name it already knows."""
    text = TWO_ISIN_COLUMNS.replace("Test Mutual Fund", "Test Asset Management Company Ltd")
    metas = {meta["scheme_code"]: meta
             for meta, _ in _parse(text, default_amc="Known AMC")}
    assert metas[111111]["fund_house"] == "Known AMC"


def _by_code(text):
    return {meta["scheme_code"]: meta for meta, _ in _parse(text)}


# --- reinvestment column: "reported blank" vs "not in this file" -----------------------


def test_a_present_reinvestment_column_is_reported_whether_or_not_it_holds_an_isin():
    """Both rows below carry the column, so both are AMFI's statement about it -- the
    placeholder one says "no reinvestment ISIN", which is what lets the merge clear a stale
    stored value."""
    rows = _by_code(DIGITAL_INDIA_PAIR)
    assert rows[152965]["isin_reinvestment_reported"] is True
    assert rows[152964]["isin_reinvestment_reported"] is True
    assert rows[152964]["isin_reinvestment"] is None


def test_a_file_without_the_reinvestment_column_does_not_report_it():
    """Absent from the header means unknown, never "reported blank" -- otherwise a download
    that simply lacks the column would wipe every stored reinvestment ISIN."""
    text = """Open Ended Schemes ( Equity Scheme - Large Cap Fund )

Test Mutual Fund

Scheme Code;Scheme Name;ISIN;Plan;Option;Net Asset Value;Date
111111;Test Fund;INF000009;Direct;Growth;125.4321;01-Feb-2025
"""
    meta = _by_code(text)[111111]
    assert meta["isin_reinvestment_reported"] is False
    assert meta["isin_reinvestment"] is None


def test_a_header_without_the_column_does_not_inherit_its_fallback_index():
    """The fallback map puts the reinvestment ISIN at index 5. In this layout index 5 is the
    NAV, and reading it as an ISIN would both flip the option to IDCW and store "125.4321"
    as a reinvestment ISIN. A header decides which columns exist."""
    text = """Test Mutual Fund

Scheme Code;Scheme Name;ISIN;Plan;Option;Net Asset Value;Date
111111;Test Fund;INF000009;Direct;Growth;125.4321;01-Feb-2025
"""
    meta = _by_code(text)[111111]
    assert meta["isin_reinvestment"] is None
    assert meta["option_type"] == "Growth" and meta["option_source"] == "amfi"


def test_rows_before_any_header_never_count_as_reported():
    """Headerless rows are read through guessed indices, which say where a column usually
    is, not that this file has it."""
    text = "Test Mutual Fund\n111111;INF000001;-;Test Fund - Direct Plan - Growth;125.4321;0;0;01-Feb-2025\n"
    meta = _by_code(text)[111111]
    assert meta["isin_reinvestment_reported"] is False


# --- provenance ----------------------------------------------------------------------


def test_values_stated_in_amfis_columns_are_sourced_amfi():
    meta = _by_code(NO_PLAN_EVIDENCE.replace("Test Liquid Fund;;;", "Test Liquid Fund;Direct Plan;Growth;"))[145834]
    assert (meta["plan_type"], meta["plan_source"]) == ("Direct", "amfi")
    assert (meta["option_type"], meta["option_source"]) == ("Growth", "amfi")


def test_values_read_off_the_scheme_name_are_sourced_name():
    meta = _by_code(NO_PLAN_EVIDENCE.replace("Test Liquid Fund;;;", "Test Liquid Fund - Regular Plan - IDCW;;;"))[145834]
    assert (meta["plan_type"], meta["plan_source"]) == ("Regular", "name")
    assert (meta["option_type"], meta["option_source"]) == ("IDCW", "name")


def test_an_option_derived_from_the_reinvestment_isin_is_sourced_isin():
    rows = _by_code(DIGITAL_INDIA_PAIR)
    assert (rows[152965]["option_type"], rows[152965]["option_source"]) == ("IDCW", "isin")
    assert (rows[152964]["option_type"], rows[152964]["option_source"]) == ("Growth", "amfi")


def test_no_value_means_no_source():
    meta = _by_code(NO_PLAN_EVIDENCE)[145834]
    assert (meta["plan_type"], meta["plan_source"]) == (None, None)
    assert (meta["option_type"], meta["option_source"]) == (None, None)


def test_provenance_does_not_change_which_signal_wins():
    """Recording a source must not re-decide the value. The original precedence tests the
    name for "direct" before the column for "regular", so a row whose column says Regular
    but whose name says Direct stays Direct -- now honestly labelled as coming from the name.
    Same for the option: the reinvestment ISIN still outranks a stated "Growth"."""
    text = NO_PLAN_EVIDENCE.replace("Test Liquid Fund;;;", "Test Liquid Fund - Direct Plan;Regular;Growth;")
    meta = _by_code(text)[145834]
    assert (meta["plan_type"], meta["plan_source"]) == ("Direct", "name")

    text = NO_PLAN_EVIDENCE.replace("INF247L01734;-;Test Liquid Fund;;;", "INF247L01734;INF247L01742;Test Liquid Fund;Direct;Growth;")
    meta = _by_code(text)[145834]
    assert (meta["option_type"], meta["option_source"]) == ("IDCW", "isin")


# --- bulk history download ------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def read(self):
        return self.body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, bodies_by_tp):
    import re
    import urllib.request

    requested = []

    def urlopen(req, context=None, timeout=None):
        tp = re.search(r"[?&]tp=(\d+)", req.full_url)
        requested.append(int(tp.group(1)) if tp else None)
        return _FakeResponse(bodies_by_tp.get(requested[-1], "<html>form page</html>"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return requested


_REPORT_HEAD = "Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Net Asset Value;Date\n"


def test_the_bulk_history_report_is_fetched_per_scheme_type_and_joined(monkeypatch):
    """The all-AMC report without `tp` now returns the portal's HTML form (seen live
    2026-09-23/25). Each type's part has its own header, which the parser re-reads."""
    requested = _serve(monkeypatch, {
        1: _REPORT_HEAD + "Open Ended Schemes ( Equity Scheme - Large Cap Fund )\nA Mutual Fund\n"
                          "111111;Fund A;Direct;Growth;INF1;-;10.5;24-Sep-2026\n",
        2: _REPORT_HEAD + "Close Ended Schemes ( Income )\nB Mutual Fund\n"
                          "222222;Fund B;Regular;IDCW;INF2;-;11.5;24-Sep-2026\n",
        3: _REPORT_HEAD + "Interval Fund Schemes ( Income )\nC Mutual Fund\n"
                          "333333;Fund C;Direct;Growth;INF3;-;12.5;24-Sep-2026\n",
    })
    text = AmfiClient().download_bulk_historical_report(datetime.date(2026, 9, 20), datetime.date(2026, 9, 24))

    assert requested == [1, 2, 3]
    assert set(_by_code(text)) == {111111, 222222, 333333}


def test_one_failed_scheme_type_fails_the_whole_bulk_download(monkeypatch):
    """Returning the other types' rows would let the backfill checkpoint a chunk as done
    with every close-ended scheme missing from it."""
    _serve(monkeypatch, {1: _REPORT_HEAD + "111111;Fund A;Direct;Growth;INF1;-;10.5;24-Sep-2026\n",
                         3: _REPORT_HEAD})
    assert AmfiClient().download_bulk_historical_report(
        datetime.date(2026, 9, 20), datetime.date(2026, 9, 24)) is None


def test_a_single_isin_column_layout_still_works():
    """Some historical reports publish only one ISIN column; the list-of-indices
    handling must not require two."""
    text = """Open Ended Schemes ( Equity Scheme - Large Cap Fund )

Test Mutual Fund

Scheme Code;Scheme Name;ISIN;Net Asset Value;Date
111111;Test Fund - Direct Plan - Growth;INF000009;125.4321;01-Feb-2025
"""
    assert _isin_by_code(text)[111111] == "INF000009"
