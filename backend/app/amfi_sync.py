import datetime
import json
import logging
import math
import re
import threading
import time
from typing import Tuple, Optional, List, Dict, Any

import pandas as pd

from app.amfi_client import AmfiClient
from app.amfi_ter_client import AmfiTerClient, TER_PORTAL_PAGE_URL
from app.core.config import settings
from app.db import bulk
from app.db import queries as db
from app import costs_data

logger = logging.getLogger("amfi_sync")
logger.setLevel(logging.INFO)

# --- The one AMFI merge path ------------------------------------------------
# Every AMFI feed (daily NAVAll.txt, a historical backfill chunk, a single AMC's
# 90-day history) parses into the same shape -- scheme metadata plus NAV points --
# so all three share one merge, rather than the three byte-identical copies of a
# staging-table merge they each used to carry.

SCHEME_COLUMNS = [
    "scheme_code", "scheme_name", "fund_house", "category", "plan_type",
    "plan_source", "option_type", "option_source", "isin", "isin_reinvestment",
    "expense_ratio", "ter_status", "ter_source", "ter_source_url", "ter_as_of_date",
]

# How an existing scheme row is reconciled with a freshly-downloaded one. Two
# rules matter here and both are load-bearing:
#   * AMFI's per-file headers don't always carry fund_house/category (a chunk
#     download can omit the AMC banner lines), so a blank incoming value must
#     not erase a good stored one.
#   * An 'official' TER comes from AMFI's dated Regulation 66 disclosure portal.
#     Official must never be overwritten by the NAV-merge fallback.
# Expressed as ON CONFLICT DO UPDATE expressions, this handles insert-or-update
# in one statement -- replacing the old UPDATE-then-INSERT-what's-missing pair.
#: Share of a TER month's pages that may be dropped before the sync calls itself failed
#: rather than merely noting the gap. Above this, the month is materially incomplete and
#: the next scheduled run should fetch it again.
TER_INCOMPLETE_FRACTION = 0.10

SCHEME_UPDATE = {
    "scheme_name": "excluded.scheme_name",
    "fund_house": "CASE WHEN excluded.fund_house IS NOT NULL AND excluded.fund_house != ''"
                  " THEN excluded.fund_house ELSE schemes.fund_house END",
    "category": "CASE WHEN excluded.category IS NOT NULL AND excluded.category != ''"
                " THEN excluded.category ELSE schemes.category END",
    # COALESCE, not a plain overwrite: AMFI states the plan for only ~60% of rows, and
    # resolve_plan_options() works out most of the rest from the published Direct/Regular
    # NAVs. A plain overwrite would blank those hard-won values on the very next NAV sync
    # (the 23:30 run would leave 355 schemes unlabelled until the next morning's chain).
    # A value AMFI does state still wins -- it is the authority when it speaks.
    "plan_type": "COALESCE(excluded.plan_type, schemes.plan_type)",
    "option_type": "COALESCE(excluded.option_type, schemes.option_type)",
    # A source travels with its value and never on its own: it is taken from whichever
    # side the COALESCE above took the value from. Keyed on the *value* being NULL, not the
    # source, so a caller that states a value without a source records "unknown" rather
    # than leaving an older source beside a value it no longer explains.
    "plan_source": "CASE WHEN excluded.plan_type IS NULL"
                   " THEN schemes.plan_source ELSE excluded.plan_source END",
    "option_source": "CASE WHEN excluded.option_type IS NULL"
                     " THEN schemes.option_source ELSE excluded.option_source END",
    "isin": "COALESCE(excluded.isin, schemes.isin)",
    # COALESCE here is the rule for a feed that did NOT carry the reinvestment column: its
    # NULL means "unknown", so the stored evidence is kept. A feed that did carry it merges
    # through SCHEME_UPDATE_REINVESTMENT_REPORTED instead, where NULL means "AMFI says
    # none" and clears the value -- see _merge_amfi_payload.
    "isin_reinvestment": "COALESCE(excluded.isin_reinvestment, schemes.isin_reinvestment)",
    "expense_ratio": "CASE WHEN schemes.ter_status = 'official'"
                     " THEN schemes.expense_ratio ELSE excluded.expense_ratio END",
    "ter_status": "CASE WHEN schemes.ter_status = 'official'"
                  " THEN schemes.ter_status ELSE excluded.ter_status END",
    "ter_source": "CASE WHEN schemes.ter_status = 'official'"
                  " THEN schemes.ter_source ELSE excluded.ter_source END",
    "ter_source_url": "CASE WHEN schemes.ter_status = 'official'"
                      " THEN schemes.ter_source_url ELSE excluded.ter_source_url END",
    "ter_as_of_date": "CASE WHEN schemes.ter_status = 'official'"
                      " THEN schemes.ter_as_of_date ELSE excluded.ter_as_of_date END",
}

# The same rules for rows whose file carried the "ISIN Div Reinvestment" column: there the
# incoming value is AMFI's complete answer -- a real ISIN, or NULL for a placeholder -- so it
# replaces what is stored. Without this a scheme that loses its dividend option would keep
# the stale ISIN (and read as IDCW on the strength of it) forever.
#
# Why a second rule set rather than a sentinel value in one column: the "clear" vs "keep"
# distinction has to reach ON CONFLICT, but whatever the staging row carries is also what a
# brand-new scheme gets INSERTed with, so a sentinel string would land in fresh rows. Routing
# each row through the rule its file earned keeps the stored value to NULL-or-a-real-ISIN on
# both the insert and the update path, with no change to db/bulk.py.
SCHEME_UPDATE_REINVESTMENT_REPORTED = {
    **SCHEME_UPDATE,
    "isin_reinvestment": "excluded.isin_reinvestment",
}


def _scheme_rows(schemes_dict: Dict[int, dict]) -> List[tuple]:
    """Flattens parsed scheme metadata into SCHEME_COLUMNS order, merging in
    cost fields from get_scheme_cost_specs. Plain tuples for executemany."""
    rows = []
    for s in schemes_dict.values():
        specs = costs_data.get_scheme_cost_specs(
            s["scheme_code"], s["scheme_name"], s["category"], s["plan_type"]
        )
        rows.append((
            s["scheme_code"], s["scheme_name"], s["fund_house"], s["category"],
            s["plan_type"], s.get("plan_source"), s["option_type"], s.get("option_source"),
            s["isin"], s.get("isin_reinvestment"),
            specs.get("expense_ratio"), specs.get("ter_status"),
            specs.get("ter_source"), specs.get("ter_source_url"),
            specs.get("ter_as_of_date"),
        ))
    return rows


def _dedupe_nav(nav_rows: List[tuple]) -> List[tuple]:
    """Collapses duplicate (scheme_code, nav_date) points -- last one wins, matching
    the previous drop_duplicates() -- and returns them sorted by primary key.

    The sort is not cosmetic: rows arrive grouped by AMC, so NAV points for one
    scheme are scattered across the file by date. Feeding them to the upsert in
    primary-key order turns what would be random probes all over the nav_history
    B-tree into a near-sequential walk, so the pages a batch touches stay hot in
    the page cache instead of being faulted in and out one row at a time. Costs a
    fraction of a second in Python; saves far more than that in page churn on a
    table heading for millions of rows."""
    deduped = {(code, date): (code, date, nav) for code, date, nav in nav_rows}
    return sorted(deduped.values())


def _merge_amfi_payload(
    schemes_dict: Dict[int, dict],
    nav_rows: List[tuple],
    label: str,
    should_stop: Optional[Any] = None,
    reinvestment_clears: bool = True,
) -> Tuple[int, int]:
    """Merges one parsed AMFI payload into schemes + nav_history.

    Two primary-key upserts, no staging tables, no joins -- see db/bulk.py for
    why the staging-table merge this replaced was quadratic. Returns
    (schemes written, NAV rows written).

    Schemes are split by whether their row's file reported the reinvestment-ISIN column
    (see SCHEME_UPDATE_REINVESTMENT_REPORTED); a dict without the flag -- any caller that
    predates it -- falls in the keep-what-is-stored group, the safe side.
    `reinvestment_clears=False` puts every row there: the historical backfill passes it,
    because a blank in a 2015 report says the scheme had no reinvestment ISIN *then*,
    which is no grounds for deleting the one it carries today."""
    reported: Dict[int, dict] = {}
    unreported: Dict[int, dict] = {}
    for code, s in schemes_dict.items():
        (reported if reinvestment_clears and s.get("isin_reinvestment_reported") else unreported)[code] = s
    nav_final = _dedupe_nav(nav_rows)

    con = db.get_connection()
    try:
        sch_written = 0
        for group, rules, group_label in (
            (unreported, SCHEME_UPDATE, "schemes"),
            (reported, SCHEME_UPDATE_REINVESTMENT_REPORTED, "schemes (reinvestment column reported)"),
        ):
            if not group:
                continue
            sch_written += bulk.upsert(
                con,
                table="schemes",
                columns=SCHEME_COLUMNS,
                conflict_columns=["scheme_code"],
                rows=_scheme_rows(group),
                update=rules,
                label=f"{label} {group_label}",
            ).rows_written
        nav = bulk.upsert(
            con,
            table="nav_history",
            columns=["scheme_code", "nav_date", "nav"],
            conflict_columns=["scheme_code", "nav_date"],
            rows=nav_final,
            # A re-downloaded NAV point should reflect AMFI's current value (they
            # do restate), but re-writing an identical value would still dirty the
            # page and grow the WAL -- so skip the no-op case. IS DISTINCT FROM
            # correctly handles NULLs and is valid PostgreSQL syntax.
            update={"nav": "excluded.nav"},
            update_where="nav_history.nav IS DISTINCT FROM excluded.nav",
            label=f"{label} nav",
            should_stop=should_stop,
        )
        return sch_written, nav.rows_written
    finally:
        con.close()

# --- SYNC HEALTH HISTORY (surfaced on the Data Management page) ---
_SYNC_HISTORY_LOCK = threading.Lock()
_SYNC_HISTORY: Dict[str, Any] = {
    "last_attempt_at": None,
    "last_attempt_trigger": None,
    "last_success_at": None,
    "last_success_msg": None,
    "last_failure_at": None,
    "last_error": None,
    "total_syncs": 0,
    "total_failures": 0,
}

def _now_ist() -> datetime.datetime:
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)

def get_sync_history() -> Dict[str, Any]:
    """Last-attempt/last-success/last-failure timestamps across every sync trigger (scheduled,
    hourly heartbeat, or manual UI click) — they all funnel through sync_daily_nav()."""
    with _SYNC_HISTORY_LOCK:
        return dict(_SYNC_HISTORY)

def _record_sync_result(success: bool, msg: str, trigger: str = "manual") -> None:
    with _SYNC_HISTORY_LOCK:
        now = _now_ist()
        _SYNC_HISTORY["last_attempt_at"] = now
        _SYNC_HISTORY["last_attempt_trigger"] = trigger
        _SYNC_HISTORY["total_syncs"] += 1
        if success:
            _SYNC_HISTORY["last_success_at"] = now
            _SYNC_HISTORY["last_success_msg"] = msg
        else:
            _SYNC_HISTORY["last_failure_at"] = now
            _SYNC_HISTORY["last_error"] = msg
            _SYNC_HISTORY["total_failures"] += 1


# --- OFFICIAL TER SYNC HEALTH HISTORY (separate from NAV sync — different cadence/source) ---
_TER_SYNC_HISTORY_LOCK = threading.Lock()
_TER_SYNC_HISTORY: Dict[str, Any] = {
    "last_attempt_at": None,
    "last_attempt_trigger": None,
    "last_success_at": None,
    "last_success_msg": None,
    "last_failure_at": None,
    "last_error": None,
    "total_syncs": 0,
    "total_failures": 0,
}

def get_ter_sync_history() -> Dict[str, Any]:
    """Last-attempt/last-success/last-failure timestamps for the official AMFI TER-portal
    sync — tracked separately from get_sync_history() (daily NAV sync) since it runs on
    its own cadence against a different AMFI feed."""
    with _TER_SYNC_HISTORY_LOCK:
        return dict(_TER_SYNC_HISTORY)

def _ter_prefix_for(our_row: Any) -> Optional[str]:
    """Which of AMFI's TER column sets ("d_" Direct / "r_" Regular) a scheme of ours reads."""
    raw_plan = str(our_row["plan_type"] or "").strip().lower()
    raw_name = str(our_row["scheme_name"] or "").strip().lower()
    if "direct" in raw_plan or "direct" in raw_name:
        return "d_"
    if "regular" in raw_plan or "regular" in raw_name:
        return "r_"
    return None


def _ter_columns_for(row: Dict[str, Any], prefix: str, plans_we_hold: set) -> str:
    """The column set to read for a scheme whose plan says `prefix`.

    A single-plan scheme (every ETF) is disclosed by AMFI under ONE set of columns -- the
    Direct ones -- with the other set published as 0.0000 in every field. We label such a
    scheme "Regular", so reading "its" columns gave 108 live ETFs a TER of exactly 0.0000
    (Nippon Gold BeES: AMFI publishes 0.8100). All-zero columns mean "this plan does not
    exist", not "this plan is free": when they are all zero, the other plan's are not, and
    we hold no scheme of that other plan, the published set is the one that applies."""
    other = "r_" if prefix == "d_" else "d_"
    own_empty = all(float(row.get(f"{prefix}{k}") or 0.0) == 0.0
                    for k in ("ber", "brokerage", "transaction", "statutory", "ter"))
    if own_empty and float(row.get(f"{other}ter") or 0.0) > 0.0 and other not in plans_we_hold:
        return other
    return prefix


def _record_ter_sync_result(success: bool, msg: str, trigger: str = "manual") -> None:
    with _TER_SYNC_HISTORY_LOCK:
        now = _now_ist()
        _TER_SYNC_HISTORY["last_attempt_at"] = now
        _TER_SYNC_HISTORY["last_attempt_trigger"] = trigger
        _TER_SYNC_HISTORY["total_syncs"] += 1
        if success:
            _TER_SYNC_HISTORY["last_success_at"] = now
            _TER_SYNC_HISTORY["last_success_msg"] = msg
        else:
            _TER_SYNC_HISTORY["last_failure_at"] = now
            _TER_SYNC_HISTORY["last_error"] = msg
            _TER_SYNC_HISTORY["total_failures"] += 1

def sync_daily_nav(_trigger: str = "manual", refresh_summary: bool = True) -> Tuple[bool, str]:
    """Serialized via db.WRITE_LOCK so this can never race the backfill worker, a cost
    import, or another sync call writing to the same DuckDB connection at the same time.
    Every call (scheduled, heartbeat, or manual) records its outcome via get_sync_history().

    `refresh_summary=False` leaves the summary-table rebuild to the caller -- sync_all()
    runs three writers back to back and rebuilds once at the end instead of three times."""
    try:
        with db.WRITE_LOCK:
            success, msg = _sync_daily_nav_impl(refresh_summary=refresh_summary)
    except Exception as e:
        _record_sync_result(False, str(e), _trigger)
        raise
    _record_sync_result(success, msg, _trigger)
    if success:
        _evaluate_holdings_alerts()
    return success, msg


def _evaluate_holdings_alerts() -> None:
    """New NAVs can trip the owner's holdings alert rules (drift, drawdown, stale
    NAV). Runs after WRITE_LOCK is released, and can never fail or change the
    reported outcome of the market-data sync itself."""
    try:
        from app.services import holdings_insights

        holdings_insights.evaluate_all()
    except Exception:
        logger.exception("Holdings alert evaluation after NAV sync failed")

def _sync_daily_nav_impl(refresh_summary: bool = True) -> Tuple[bool, str]:
    """
    Fetches the latest official AMFI NAVAll.txt and upserts new daily NAV records into DuckDB.
    Uses high-speed DataFrame staging bulk operations instead of slow row-by-row loops.
    """
    client = AmfiClient()
    logger.info("Fetching daily NAV file from AMFI portal...")

    raw_text = client.download_daily_report()
    if not raw_text or len(raw_text) < 100:
        return False, "Failed to download daily NAV file from AMFI."

    schemes_dict = {}
    nav_rows = []

    for scheme_meta, nav_record in client.parse_amfi_nav_lines(raw_text):
        code = scheme_meta["scheme_code"]
        if code not in schemes_dict:
            schemes_dict[code] = scheme_meta
        nav_rows.append((nav_record["scheme_code"], nav_record["nav_date"], nav_record["nav"]))

    if not nav_rows:
        return False, "No valid NAV rows parsed from AMFI daily feed."

    try:
        n_schemes, n_nav = _merge_amfi_payload(schemes_dict, nav_rows, label="daily")
    except Exception as e:
        logger.error(f"Error updating database: {e}")
        return False, str(e)

    # Refresh materialized summary table
    if refresh_summary:
        db.refresh_summary_table()
    msg = f"Successfully synced {n_nav:,} NAV records across {n_schemes:,} schemes!"
    logger.info(msg)
    return True, msg


def sync_official_ter(_trigger: str = "manual", months: Optional[List[str]] = None,
                      stats: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """Fetches AMFI's official, dated TER-portal disclosure (SEBI Regulation 66) and promotes
    matched schemes' current TER to 'official' status, run on the same daily cadence as
    sync_daily_nav().

    Unlike sync_daily_nav(), this does NOT hold db.WRITE_LOCK for its whole duration: the
    AMFI TER API caps pageSize at 100, so covering one calendar month can take hundreds of
    sequential HTTP requests (multiple minutes by month-end) — holding WRITE_LOCK across all
    of that would block every other writer (a manual "Sync Now" click) for the whole fetch.
    Only the two actual DB-mutating calls inside (db.upsert_ter_history,
    db.apply_latest_official_ter) take WRITE_LOCK, and only for their own brief duration.

    `stats`, when given, is filled in with the row and scheme counts this run produced. The
    backfill needs them for its progress display and its checkpoint ledger, and the counts
    were otherwise reachable only by scraping them back out of the human-readable message.
    """
    try:
        success, msg = _sync_official_ter_impl(months, stats)
    except Exception as e:
        _record_ter_sync_result(False, str(e), _trigger)
        raise
    _record_ter_sync_result(success, msg, _trigger)
    return success, msg

def _sync_official_ter_impl(months: Optional[List[str]] = None,
                            stats: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    client = AmfiTerClient()
    recent = AmfiTerClient.recent_months(2)
    if months is None:
        con = db.get_connection()
        try:
            # Must test the end of the interval, not its start. ter_date is now the date a
            # TER *took effect*, so a fund whose fee has not moved in a year carries a row
            # dated a year ago however fresh the data is -- testing that would have reported
            # every scheme stale and pulled two months on every single daily sync.
            has_recent_ter = con.execute(
                "SELECT 1 FROM ter_history "
                "WHERE COALESCE(valid_to, ter_date) >= (CURRENT_DATE - INTERVAL '60 days') LIMIT 1;"
            ).fetchone() is not None
        except Exception:
            has_recent_ter = False
        finally:
            con.close()
        months_to_try = [recent[0]] if has_recent_ter else recent
    else:
        months_to_try = months

    # 1. Fetch and validate every row across the requested months.
    parsed_rows: List[Dict[str, Any]] = []
    months_fetched: List[str] = []
    for month in months_to_try:
        month_count = 0
        for raw_row in client.fetch_month(month):
            parsed = AmfiTerClient.parse_row(raw_row)
            if parsed:
                parsed_rows.append(parsed)
                month_count += 1
        logger.info(f"AMFI TER portal: fetched {month_count:,} valid rows for {month}.")
        if month_count > 0:
            months_fetched.append(month)

    # Fallback to previous month if single current-month fetch was empty (e.g. 1st/2nd of month)
    if not parsed_rows and months is None and len(months_to_try) == 1:
        fallback_month = recent[1]
        logger.info(f"Current month {months_to_try[0]} had no disclosures; falling back to {fallback_month}...")
        for raw_row in client.fetch_month(fallback_month):
            parsed = AmfiTerClient.parse_row(raw_row)
            if parsed:
                parsed_rows.append(parsed)
        if parsed_rows:
            months_fetched.append(fallback_month)

    if not parsed_rows:
        return False, "AMFI TER portal returned no usable data (fetch failed, or empty response)."

    # 2. Group by underlying scheme: use NSDL code when present, or normalized scheme name
    # when missing (June 2018 - Oct 2024 historical disclosures lacked NSDL codes).
    by_scheme_key: Dict[str, List[Dict[str, Any]]] = {}
    for row in parsed_rows:
        nsdl = (row.get("nsdl_scheme_code") or "").strip()
        if nsdl:
            key = f"NSDL:{nsdl}"
        else:
            norm_name = costs_data.normalize_scheme_name(row["scheme_name"])
            key = f"NAME:{norm_name}"
        by_scheme_key.setdefault(key, []).append(row)

    name_to_keys: Dict[str, set] = {}
    for key, rows in by_scheme_key.items():
        name = costs_data.normalize_scheme_name(rows[0]["scheme_name"])
        if name:
            name_to_keys.setdefault(name, set()).add(key)

    # Accept unambiguous names, as well as names where multiple option codes (e.g. Growth & IDCW)
    # report identical/consistent TER disclosures, or historical transitions from name to NSDL code.
    unambiguous_names = set()
    for name, keys in name_to_keys.items():
        if len(keys) == 1:
            unambiguous_names.add(name)
        else:
            # Check for conflict across keys on any shared/overlapping dates
            date_to_ters: Dict[datetime.date, Tuple[float, float]] = {}
            is_consistent = True
            for k in keys:
                for r in by_scheme_key[k]:
                    dt = r["ter_date"]
                    cur_ters = (r.get("d_ter", 0.0), r.get("r_ter", 0.0))
                    if dt in date_to_ters:
                        prev_ters = date_to_ters[dt]
                        if abs(prev_ters[0] - cur_ters[0]) > 0.005 or abs(prev_ters[1] - cur_ters[1]) > 0.005:
                            is_consistent = False
                            break
                    else:
                        date_to_ters[dt] = cur_ters
                if not is_consistent:
                    break
            if is_consistent:
                unambiguous_names.add(name)

    ambiguous_count = len(name_to_keys) - len(unambiguous_names)
    if ambiguous_count:
        logger.info(f"AMFI TER sync: skipping {ambiguous_count} scheme name(s) with conflicting disclosures on AMFI's portal.")

    # 3. Match against our own scheme universe by the same normalized name.
    identity = db.get_scheme_identity_map()
    if identity.empty:
        return False, "No local schemes to match against."
    identity = identity.copy()
    identity["_norm"] = identity["scheme_name"].apply(costs_data.normalize_scheme_name)
    our_groups = {name: sub for name, sub in identity.groupby("_norm")}

    history_rows: List[Dict[str, Any]] = []
    latest_by_scheme: Dict[int, Dict[str, Any]] = {}
    matched_scheme_codes = set()

    for name in unambiguous_names:
        our_matches = our_groups.get(name)
        if our_matches is None or our_matches.empty:
            continue
        merged_by_date = {}
        for key in name_to_keys[name]:
            for dr in by_scheme_key[key]:
                merged_by_date[dr["ter_date"]] = dr
        date_rows = sorted(merged_by_date.values(), key=lambda r: r["ter_date"])
        plans_we_hold = {_ter_prefix_for(r) for _, r in our_matches.iterrows()}

        for _, our_row in our_matches.iterrows():
            prefix = _ter_prefix_for(our_row)
            if prefix is None:
                continue  # Unrecognized plan type — never guess which column applies.

            scheme_code = int(our_row["scheme_code"])
            matched_scheme_codes.add(scheme_code)
            for dr in date_rows:
                p = _ter_columns_for(dr, prefix, plans_we_hold)
                history_rows.append({
                    "scheme_code": scheme_code,
                    "ter_date": dr["ter_date"],
                    "base_expense_ratio_pct": dr[f"{p}ber"],
                    "brokerage_cost_pct": dr[f"{p}brokerage"],
                    "transaction_cost_pct": dr[f"{p}transaction"],
                    "statutory_levies_pct": dr[f"{p}statutory"],
                    "total_ter_pct": dr[f"{p}ter"],
                    "source_url": TER_PORTAL_PAGE_URL,
                })

            latest = date_rows[-1]
            lp = _ter_columns_for(latest, prefix, plans_we_hold)
            existing = latest_by_scheme.get(scheme_code)
            if existing is None or latest["ter_date"] >= existing["ter_date"]:
                latest_by_scheme[scheme_code] = {
                    "scheme_code": scheme_code,
                    "ter_date": latest["ter_date"],
                    "base_expense_ratio_pct": latest[f"{lp}ber"],
                    "brokerage_cost_pct": latest[f"{lp}brokerage"],
                    "transaction_cost_pct": latest[f"{lp}transaction"],
                    "statutory_levies_pct": latest[f"{lp}statutory"],
                    "total_ter_pct": latest[f"{lp}ter"],
                    "source_url": TER_PORTAL_PAGE_URL,
                    "ter_source": "AMFI Total Expense Ratio Disclosure (Regulation 66, auto-synced)",
                }

    if not history_rows:
        return False, "AMFI TER portal data fetched, but no schemes matched unambiguously by name."

    df_history = pd.DataFrame(history_rows).drop_duplicates(subset=["scheme_code", "ter_date"])
    # A month fetched without losing a single page is the portal's complete word on those
    # days, so it replaces what is stored rather than merging into it. That distinction is
    # what lets a deliberate re-fetch repair bad data: merging can add days and correct
    # figures, but it can never delete a day that should never have been there. When pages
    # did drop, the merge is what keeps the days they failed to re-deliver.
    complete_fetch = not client.failed_pages
    n_history = db.upsert_ter_history(df_history, authoritative=complete_fetch)

    df_latest = pd.DataFrame(latest_by_scheme.values())
    result = db.apply_latest_official_ter(df_latest)

    if stats is not None:
        stats["rows_written"] = n_history
        stats["schemes_matched"] = len(matched_scheme_codes)
        stats["schemes_current_ter_updated"] = result["updated"]
        stats["months_fetched"] = list(months_fetched)

    msg = (
        f"Matched {len(matched_scheme_codes):,} schemes across {len(months_fetched)} month(s) "
        f"({', '.join(months_fetched)}); stored {n_history:,} dated TER records; "
        f"refreshed current TER for {result['updated']:,} schemes."
    )
    # A page the portal rate-limited away is missing data, not a clean fetch. Reporting
    # success regardless is how a third of 09-2026 went missing on 2026-09-23 while the
    # health card stayed green. A few dropped pages are noted; a materially incomplete
    # month is reported as a failure so the next scheduled run fetches it again.
    dropped, total = len(client.failed_pages), max(client.total_pages, 1)
    if dropped:
        msg += f" {dropped} of {total} pages could not be fetched (AMFI rate limit), so this month is incomplete."
        if dropped / total > TER_INCOMPLETE_FRACTION:
            logger.warning(f"AMFI TER sync incomplete: {msg}")
            return False, msg
    logger.info(f"AMFI TER sync: {msg}")
    return True, msg


# --- NAV RESTATEMENT ------------------------------------------------------------------
#
# The daily file only ever carries today, so a NAV AMFI later corrects would otherwise
# stay wrong forever once stored. This re-reads a trailing window and reconciles it.
#
# The hazard it is built around: nav_history is split-normalised IN PLACE. After a 10:1
# split every earlier stored NAV sits on the new basis, so it legitimately differs from
# AMFI's raw figure by a clean factor. Writing AMFI's raw value back over such a row
# re-creates the price jump, and normalize_nav_splits() -- which runs next -- would then
# rescale everything before it again, silently corrupting rows far outside the window.
# Hence: only small differences are ever written; large ones are only reported.

NAV_RESTATEMENT_WINDOW_DAYS = 30
#: The window is fetched in pieces this long. A whole 30-day open-ended report is ~24 MB,
#: and AMFI's server was seen cutting one off mid-body; a week is a few MB.
NAV_RESTATEMENT_REQUEST_DAYS = 7
#: A genuine restatement is a small correction. Anything larger is either a split-scaled
#: row (which must not be touched) or something a person should look at.
NAV_RESTATEMENT_TOLERANCE = 0.02
#: How close stored/AMFI must be to a clean factor to be explained as split scaling. Far
#: tighter than the normaliser's 5%: that one measures across a trading day (a real price
#: move rides on top of the ratio), this one compares the same day's NAV with itself.
NAV_RESTATEMENT_SPLIT_TOLERANCE = 0.005
_RESTATEMENT_SPLIT_FACTORS = sorted({float(f) for f in (2, 3, 4, 5, 10, 20, 100)}
                                    | {1.0 / f for f in (2, 3, 4, 5, 10, 20, 100)})
#: Read by the data audit -- keep the payload shape stable.
NAV_RESTATEMENT_META_KEY = "nav_restatement_last"
_RESTATEMENT_SUSPECT_SAMPLE = 50


def _classify_restatement(stored: float, amfi: float) -> str:
    """'same', 'restate', 'split_scaled' or 'suspect' for one stored NAV against AMFI's
    figure for the same scheme and day. Relative difference is measured against AMFI's
    value, since that is the reference being reconciled to."""
    diff = abs(stored - amfi) / amfi
    if diff <= 1e-9:
        return "same"
    if diff <= NAV_RESTATEMENT_TOLERANCE:
        return "restate"
    ratio = stored / amfi
    if any(abs(ratio - f) / f <= NAV_RESTATEMENT_SPLIT_TOLERANCE for f in _RESTATEMENT_SPLIT_FACTORS):
        return "split_scaled"
    return "suspect"


def _gap_fill_is_safe(value: float, prev_nav: Optional[float], next_nav: Optional[float]) -> bool:
    """Whether inserting AMFI's raw `value` between its stored neighbours would leave a
    ratio the split normaliser acts on.

    A missing day inside an already-normalised stretch (the pre-split side of a split the
    window straddles) is on the raw basis while its neighbours are not; inserting it would
    create two artificial "splits" whose product is not exactly 1, and the normaliser would
    rescale all earlier history by it. Tested with the normaliser's own predicate, so the
    answer is exactly "would it react". A refused day is not lost: once history is
    consistent around it, a later run fills it."""
    if prev_nav and prev_nav > 0 and db._is_clean_split_ratio(value / prev_nav):
        return False
    if next_nav and next_nav > 0 and db._is_clean_split_ratio(next_nav / value):
        return False
    return True


def restate_recent_navs(dry_run: bool = False, window_days: int = NAV_RESTATEMENT_WINDOW_DAYS,
                        today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Re-downloads the trailing `window_days` of AMFI's NAV history and reconciles it
    with what is stored: small differences are corrected, missing days are filled, and
    large differences are counted and sampled but never written (see the section note).

    `dry_run=True` computes the same counts and writes nothing -- not even the summary in
    sync_meta, which the data audit reads as the record of the last real run."""
    to_date = today or _now_ist().date()
    from_date = to_date - datetime.timedelta(days=window_days)
    empty = {"checked": 0, "restated": 0, "gap_filled": 0, "gap_skipped": 0,
             "split_scaled": 0, "suspect_count": 0}

    client = AmfiClient()
    amfi: Dict[Tuple[int, datetime.date], float] = {}
    failed_ranges: List[str] = []
    start = from_date
    while start <= to_date:
        end = min(to_date, start + datetime.timedelta(days=NAV_RESTATEMENT_REQUEST_DAYS - 1))
        raw_text = None
        # One immediate retry: the failure seen live is the server cutting a large body
        # off mid-transfer, which is transient.
        for _attempt in range(2):
            raw_text = client.download_bulk_historical_report(start, end)
            if raw_text and _looks_like_nav_report(raw_text):
                break
            raw_text = None
        if raw_text is None:
            failed_ranges.append(f"{start.isoformat()}..{end.isoformat()}")
        else:
            for _meta, rec in client.parse_amfi_nav_lines(raw_text):
                if start <= rec["nav_date"] <= end and rec["nav"] > 0:
                    amfi[(rec["scheme_code"], rec["nav_date"])] = rec["nav"]
        start = end + datetime.timedelta(days=1)

    # A sub-range that failed is simply not compared: every verdict below is about a row
    # AMFI did send, so a partial download under-reports but can never mis-correct.
    if failed_ranges and not amfi:
        return {"ok": False, "dry_run": dry_run, "window_days": window_days,
                "reason": "AMFI did not return a usable NAV history report for the window.",
                "failed_ranges": failed_ranges, **empty}

    con = db.get_connection()
    try:
        # A NULL NAV is left out, so the day reads as missing and goes through the gap
        # path (with its split guard) rather than being compared as a number.
        stored = {(int(c), d): float(n) for c, d, n in con.execute(
            "SELECT scheme_code, nav_date, nav FROM nav_history"
            " WHERE nav_date BETWEEN %s AND %s AND nav IS NOT NULL",
            (from_date, to_date),
        ).fetchall()}
        known_schemes = {int(r[0]) for r in con.execute("SELECT scheme_code FROM schemes").fetchall()}
    finally:
        con.close()

    counts = dict(empty)
    writes: List[tuple] = []
    suspects: List[dict] = []
    gaps: List[Tuple[int, datetime.date, float]] = []
    for (code, nav_date), amfi_nav in amfi.items():
        have = stored.get((code, nav_date))
        if have is None:
            # A scheme the schemes table has never seen is the daily sync's to introduce,
            # with its metadata; filling NAVs for it here would leave orphan history.
            if code in known_schemes:
                gaps.append((code, nav_date, amfi_nav))
            continue
        counts["checked"] += 1
        verdict = "suspect" if have <= 0 else _classify_restatement(have, amfi_nav)
        if verdict == "restate":
            counts["restated"] += 1
            writes.append((code, nav_date, amfi_nav))
        elif verdict == "split_scaled":
            counts["split_scaled"] += 1
        elif verdict == "suspect":
            counts["suspect_count"] += 1
            suspects.append({"scheme_code": code, "nav_date": nav_date.isoformat(),
                             "stored": have, "amfi": amfi_nav, "ratio": round(have / amfi_nav, 6)})

    if gaps:
        con = db.get_connection()
        try:
            neighbours = con.execute(
                """SELECT g.code, g.d,
                          (SELECT n.nav FROM nav_history n WHERE n.scheme_code = g.code
                             AND n.nav_date < g.d AND n.nav IS NOT NULL
                           ORDER BY n.nav_date DESC LIMIT 1),
                          (SELECT n.nav FROM nav_history n WHERE n.scheme_code = g.code
                             AND n.nav_date > g.d AND n.nav IS NOT NULL
                           ORDER BY n.nav_date ASC LIMIT 1)
                   FROM unnest(%s::bigint[], %s::date[]) AS g(code, d)""",
                ([g[0] for g in gaps], [g[1] for g in gaps]),
            ).fetchall()
        finally:
            con.close()
        around = {(int(c), d): (p, n) for c, d, p, n in neighbours}
        for code, nav_date, amfi_nav in gaps:
            prev_nav, next_nav = around.get((code, nav_date), (None, None))
            if _gap_fill_is_safe(amfi_nav, prev_nav, next_nav):
                counts["gap_filled"] += 1
                writes.append((code, nav_date, amfi_nav))
            else:
                counts["gap_skipped"] += 1

    if writes and not dry_run:
        with db.WRITE_LOCK:
            con = db.get_connection()
            try:
                bulk.upsert(
                    con,
                    table="nav_history",
                    columns=["scheme_code", "nav_date", "nav"],
                    conflict_columns=["scheme_code", "nav_date"],
                    rows=sorted(writes),
                    update={"nav": "excluded.nav"},
                    update_where="nav_history.nav IS DISTINCT FROM excluded.nav",
                    label="nav restatement",
                )
            finally:
                con.close()
        db.invalidate_database_stats_cache()

    # Worst first: the sample is for a person to act on, and the largest unexplained
    # differences are the ones most likely to be real corruption.
    suspects.sort(key=lambda s: -abs(math.log(s["ratio"])) if s["ratio"] > 0 else float("-inf"))
    ran_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    if not dry_run:
        db.set_sync_meta_value(NAV_RESTATEMENT_META_KEY, json.dumps({
            "ran_at": ran_at,
            "window_days": window_days,
            "checked": counts["checked"],
            "restated": counts["restated"],
            "gap_filled": counts["gap_filled"],
            "split_scaled": counts["split_scaled"],
            "suspect_count": counts["suspect_count"],
            "suspect": suspects[:_RESTATEMENT_SUSPECT_SAMPLE],
        }))

    logger.info(
        f"NAV restatement{' (dry run)' if dry_run else ''} {from_date}..{to_date}: "
        f"checked {counts['checked']:,}, restated {counts['restated']:,}, gap-filled "
        f"{counts['gap_filled']:,} (skipped {counts['gap_skipped']:,}), split-scaled "
        f"{counts['split_scaled']:,}, suspect {counts['suspect_count']:,}"
        + (f"; could not fetch {', '.join(failed_ranges)}." if failed_ranges else ".")
    )
    return {"ok": True, "dry_run": dry_run, "ran_at": ran_at, "window_days": window_days,
            "from_date": from_date.isoformat(), "to_date": to_date.isoformat(), **counts,
            "failed_ranges": failed_ranges, "suspect": suspects[:_RESTATEMENT_SUSPECT_SAMPLE]}


# --- SYNC JOBS ----------------------------------------------------------------------------
#
# Every sync -- the 00:05 full refresh, the 23:30 NAV pass, the hourly and startup catch-up,
# and each button on the Data Management page -- runs as a "sync job" through this one
# tracker. That gives three things the scheduled runs used to lack:
#
# * Visibility: what is running, why (its trigger) and which step it is on, for the Data
#   Management page and the top bar of every fund page. The 00:05 refresh used to call
#   sync_all() directly, bypassing the state the page watched, so it ran unseen.
# * One at a time: a job that finds another running does not start. The button used to be
#   able to start a second full refresh on top of the scheduled one -- safe (writes queue
#   on WRITE_LOCK) but every step done twice.
# * Memory across restarts: each kind's last run is kept in sync_meta, where the in-process
#   sync histories reset with every container start.
#
# The two historical backfills keep their own trackers: they are resumable multi-hour
# jobs with chunk progress, and a catch-up must still be able to run beside one.

SYNC_JOB_KINDS: Dict[str, str] = {
    "full": "Full refresh",
    "nav": "NAV sync",
    "ter": "TER sync",
    "catchup": "Catch-up",
}
_SYNC_JOB_LOCK = threading.Lock()
SYNC_JOB_STATE: Dict[str, Any] = {"is_running": False, "kind": None, "trigger": None, "step": "", "started_at": None}
SYNC_JOB_META_PREFIX = "sync_job_last:"
# A full refresh that failed is retried by the hourly catch-up, but not more often than this:
# the chain includes a multi-minute TER fetch that should not hammer AMFI's portal.
FULL_REFRESH_RETRY_SECONDS = 3 * 3600
# The nightly slot the full refresh belongs to (IST); a refresh older than the latest slot is due.
FULL_REFRESH_SLOT = datetime.time(0, 5)


def _load_last_jobs() -> Dict[str, Optional[Dict[str, Any]]]:
    keys = [SYNC_JOB_META_PREFIX + k for k in SYNC_JOB_KINDS]
    raw = db.get_sync_meta_values(keys)
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for kind in SYNC_JOB_KINDS:
        value = raw.get(SYNC_JOB_META_PREFIX + kind)
        try:
            out[kind] = json.loads(value) if value else None
        except (TypeError, ValueError):
            out[kind] = None
    return out


def get_sync_job_status() -> Dict[str, Any]:
    """The job running now (if any) and the last completed run of each kind."""
    with _SYNC_JOB_LOCK:
        current = dict(SYNC_JOB_STATE)
    current["label"] = SYNC_JOB_KINDS.get(current["kind"]) if current["kind"] else None
    current["last"] = _load_last_jobs()
    return current


def current_sync_activity() -> Optional[Dict[str, Any]]:
    """What the top bar says while data is updating: the running job, else None."""
    with _SYNC_JOB_LOCK:
        if not SYNC_JOB_STATE["is_running"]:
            return None
        s = dict(SYNC_JOB_STATE)
    return {"kind": s["kind"], "label": SYNC_JOB_KINDS.get(s["kind"]), "trigger": s["trigger"],
            "step": s["step"], "started_at": s["started_at"]}


def _claim_sync_job(kind: str, trigger: str) -> Optional[str]:
    """Marks `kind` as running. Returns None on success, else the label of what is running."""
    with _SYNC_JOB_LOCK:
        if SYNC_JOB_STATE["is_running"]:
            return f"{SYNC_JOB_KINDS.get(SYNC_JOB_STATE['kind'], 'A sync')} ({SYNC_JOB_STATE['trigger']})"
        SYNC_JOB_STATE.update({"is_running": True, "kind": kind, "trigger": trigger, "step": "Starting",
                               "started_at": time.time()})
    return None


def _job_message(kind: str, result: Dict[str, Any]) -> str:
    if result.get("message"):
        return str(result["message"])
    if kind == "full":
        nav = (result.get("nav") or {}).get("message") or ""
        ter = (result.get("ter") or {}).get("message") or ""
        return " | ".join(p for p in (f"NAV: {nav}" if nav else "", f"TER: {ter}" if ter else "") if p)
    return ""


def _execute_sync_job(kind: str, trigger: str, body: Any) -> Dict[str, Any]:
    """Runs a claimed job, records its outcome durably, and always releases the claim."""
    started = time.time()

    def step(name: str) -> None:
        with _SYNC_JOB_LOCK:
            SYNC_JOB_STATE["step"] = name

    try:
        result = body(trigger, step) or {}
    except Exception as e:  # noqa: BLE001 -- a worker must never die silently or hold the claim
        logger.exception(f"{SYNC_JOB_KINDS.get(kind, kind)} ({trigger}) failed")
        result = {"ok": False, "message": str(e)}
    finished = time.time()
    record = {
        "kind": kind, "trigger": trigger, "ok": bool(result.get("ok")), "message": _job_message(kind, result),
        "started_at": started, "finished_at": finished, "elapsed_seconds": round(finished - started, 1),
        "result": result,
    }
    try:
        db.set_sync_meta_value(SYNC_JOB_META_PREFIX + kind, json.dumps(record, default=str))
    except Exception:  # noqa: BLE001 -- losing the record must not lose the run
        logger.exception("Could not store the sync job record")
    with _SYNC_JOB_LOCK:
        SYNC_JOB_STATE.update({"is_running": False, "kind": None, "trigger": None, "step": "", "started_at": None})
    return result


def run_sync_job(kind: str, trigger: str, body: Any) -> Optional[Dict[str, Any]]:
    """Runs a job in the calling thread (the scheduler's). None if another job is running --
    it is skipped, not queued: the running job is doing the same work or more."""
    busy = _claim_sync_job(kind, trigger)
    if busy:
        logger.info(f"{SYNC_JOB_KINDS[kind]} ({trigger}) skipped: {busy} is already running.")
        return None
    return _execute_sync_job(kind, trigger, body)


def start_sync_job(kind: str, trigger: str = "manual") -> Tuple[bool, Optional[str]]:
    """Starts a job in a background thread for the page: the full chain takes minutes, which
    no proxy or browser holds a POST open for. (started, label of the job already running)."""
    body = _SYNC_JOB_BODIES[kind]
    busy = _claim_sync_job(kind, trigger)
    if busy:
        return False, busy
    threading.Thread(target=_execute_sync_job, args=(kind, trigger, body),
                     name=f"AMFI_{kind}_sync", daemon=True).start()
    return True, None


def _full_body(trigger: str, step: Any) -> Dict[str, Any]:
    return sync_all(_trigger=trigger, progress=step)


def _nav_body(trigger: str, step: Any) -> Dict[str, Any]:
    step("Syncing today's NAVs")
    ok, msg = sync_daily_nav(_trigger=trigger)
    return {"ok": ok, "message": msg}


def _ter_body(trigger: str, step: Any) -> Dict[str, Any]:
    step("Syncing official TER")
    ok, msg = sync_official_ter(_trigger=trigger)
    return {"ok": ok, "message": msg}


def _fill_missing_days(step: Any, current_max: Optional[datetime.date], expected: Optional[datetime.date]) -> int:
    """Fills the days between the newest stored NAV and the expected one from AMFI's history
    API (the daily file carries one day only). Returns the NAV rows written."""
    if not current_max or not expected or (expected - current_max).days <= 1:
        return 0
    written = 0
    cur_start = current_max
    while cur_start < expected:
        cur_end = min(expected, cur_start + datetime.timedelta(days=88))
        step(f"Filling missing days {cur_start.isoformat()} to {cur_end.isoformat()}")
        try:
            sch_cnt, nav_cnt = backfill_single_chunk(cur_start, cur_end)
            written += nav_cnt
            logger.info(f"Auto-filled {nav_cnt:,} NAV records across {sch_cnt:,} schemes for {cur_start} to {cur_end}.")
        except Exception as e:  # noqa: BLE001 -- the daily sync below still runs
            logger.error(f"Error during multi-day gap auto-backfill: {e}")
        cur_start = cur_end + datetime.timedelta(days=1)
    return written


def _catchup_body(trigger: str, step: Any) -> Dict[str, Any]:
    stale, current_max, expected = is_database_stale()
    filled = _fill_missing_days(step, current_max, expected) if stale else 0
    step("Syncing today's NAVs")
    ok, msg = sync_daily_nav(_trigger=trigger)
    return {"ok": ok, "message": msg + (f" (filled {filled:,} NAVs for missed days)" if filled else ""), "filled": filled}


def _catchup_full_body(trigger: str, step: Any) -> Dict[str, Any]:
    """A missed nightly refresh: fill missed days first, then the whole chain, so plan
    identification, TER, the NAV re-check and the audit are not skipped until tomorrow."""
    stale, current_max, expected = is_database_stale()
    filled = _fill_missing_days(step, current_max, expected) if stale else 0
    result = sync_all(_trigger=trigger, progress=step)
    if filled:
        result["filled"] = filled
    return result


_SYNC_JOB_BODIES: Dict[str, Any] = {"full": _full_body, "nav": _nav_body, "ter": _ter_body, "catchup": _catchup_body}


def _latest_full_refresh_slot(now_epoch: float, now_ist: datetime.datetime) -> float:
    """Epoch seconds of the most recent 00:05 IST at or before now."""
    slot = now_ist.replace(hour=FULL_REFRESH_SLOT.hour, minute=FULL_REFRESH_SLOT.minute, second=0, microsecond=0)
    if now_ist < slot:
        slot -= datetime.timedelta(days=1)
    return now_epoch - (now_ist - slot).total_seconds()


def full_refresh_due(last: Optional[Dict[str, Any]], now_epoch: Optional[float] = None,
                     now_ist: Optional[datetime.datetime] = None) -> bool:
    """True when no successful full refresh has finished since the latest nightly slot --
    the machine was off at 00:05, or that run failed. A failed attempt is retried only
    after FULL_REFRESH_RETRY_SECONDS."""
    now_epoch = time.time() if now_epoch is None else now_epoch
    now_ist = _now_ist() if now_ist is None else now_ist
    slot = _latest_full_refresh_slot(now_epoch, now_ist)
    if not last or not last.get("finished_at"):
        return True
    finished = float(last["finished_at"])
    if last.get("ok") and finished >= slot:
        return False
    if not last.get("ok") and finished >= slot and now_epoch - finished < FULL_REFRESH_RETRY_SECONDS:
        return False
    return finished < slot or not last.get("ok")


STARTUP_TER_FRESH_SECONDS = 6 * 3600


def startup_ter_due(last_jobs: Dict[str, Optional[Dict[str, Any]]], now_epoch: Optional[float] = None) -> bool:
    """Whether a start should fetch this month's TER: not when a TER sync, or a full refresh
    (which ends with one), succeeded within STARTUP_TER_FRESH_SECONDS. Fetching it on every
    start cost ~40s of AMFI downloads per backend restart, and kept the sync job busy -- so
    the next restart had to wait for it -- with figures fetched hours earlier."""
    now_epoch = time.time() if now_epoch is None else now_epoch
    return not any(
        job and job.get("ok") and job.get("finished_at") and now_epoch - float(job["finished_at"]) < STARTUP_TER_FRESH_SECONDS
        for job in (last_jobs.get("ter"), last_jobs.get("full"))
    )


def sync_all(_trigger: str = "manual", progress: Optional[Any] = None) -> Dict[str, Any]:
    """NAVs, then a re-check of recent NAVs, then split normalisation, then plan/option
    resolution, then TER, then one summary rebuild.

    The order is a dependency chain, not a preference: the resolution needs today's NAVs to
    compare against AMFI's published Direct/Regular pair, and TER matching reads the plan to
    decide which of the two disclosed expense ratios belongs to a scheme. Running TER first
    (as the old separate 00:20 job effectively did) hands a Direct plan the Regular figure.

    Each source keeps its own health history -- they fail independently, and a TER outage
    must not read as "the data is stale", which is what a single merged verdict would imply.
    A TER or resolution failure therefore never fails the NAV sync that already succeeded."""
    started = time.time()
    result: Dict[str, Any] = {"trigger": _trigger, "nav": None, "restatement": None, "splits": None,
                              "resolve": None, "ter": None}
    say = progress if callable(progress) else (lambda _name: None)

    say("Syncing today's NAVs")
    nav_ok, nav_msg = sync_daily_nav(_trigger=_trigger, refresh_summary=False)
    result["nav"] = {"ok": nav_ok, "message": nav_msg}

    if nav_ok:
        # Before the split normaliser, so any day this fills in is normalised along with
        # everything else in the same run rather than sitting unadjusted until tomorrow.
        # It never writes a split-sized difference itself (see restate_recent_navs), which
        # is what makes running the normaliser straight after it safe.
        say("Re-checking recent NAVs")
        try:
            restated = restate_recent_navs()
            result["restatement"] = {k: restated.get(k) for k in (
                "ok", "reason", "window_days", "checked", "restated", "gap_filled", "gap_skipped",
                "split_scaled", "suspect_count", "failed_ranges") if k in restated}
        except Exception as e:  # noqa: BLE001 -- a verification pass must not fail the sync
            logger.exception("NAV restatement failed")
            result["restatement"] = {"ok": False, "reason": str(e)}

        # Before anything reads those NAVs. A fund that redenominates its units (a Rs 1000
        # liquid fund rebasing to Rs 100, an ETF splitting 10:1) leaves a raw ratio that
        # every return calculation spanning the date reads as a ~-90% collapse -- DSP Gold
        # ETF's 10:1 split on 2026-08-28 was being published across the app as the market's
        # worst performer at -90.6%. The normalizer existed for this and was never wired to
        # a caller, so 280 such events sat unadjusted.
        say("Adjusting unit splits")
        try:
            result["splits"] = {"ok": True, "rows_adjusted": db.normalize_nav_splits()}
        except Exception as e:  # noqa: BLE001 -- a repair pass must not fail the sync
            logger.exception("NAV split normalization failed")
            result["splits"] = {"ok": False, "reason": str(e), "rows_adjusted": 0}

        say("Identifying Direct/Regular plans")
        try:
            result["resolve"] = resolve_plan_options()
        except Exception as e:  # noqa: BLE001 -- an enrichment must never fail the sync
            logger.exception("Plan/option resolution failed")
            result["resolve"] = {"ok": False, "reason": str(e), "resolved": 0, "plan_changed": 0, "option_changed": 0}
    else:
        result["restatement"] = {"ok": False, "reason": "Skipped: NAV sync failed."}
        result["resolve"] = {"ok": False, "reason": "Skipped: NAV sync failed, so there is nothing new to match against.",
                             "resolved": 0, "plan_changed": 0, "option_changed": 0}

    say("Syncing official TER")
    try:
        ter_ok, ter_msg = sync_official_ter(_trigger=_trigger)
    except Exception as e:  # noqa: BLE001 -- see above; sync_official_ter re-raises after recording
        ter_ok, ter_msg = False, str(e)
    result["ter"] = {"ok": ter_ok, "message": ter_msg}

    say("Rebuilding the performance table")
    # One rebuild, after every writer above, so the pages (which all read summary_table)
    # show the new NAVs, the corrected plans and the new TERs at the same moment. The TER
    # sync used to leave this out entirely, so a fresh expense ratio stayed invisible until
    # some later NAV sync happened to rebuild the table.
    db.refresh_summary_table()

    # Last, so it judges the data every page is about to show rather than a half-written
    # state. The upserts above only ever say "stored what AMFI sent"; this is the step that
    # asks whether what is stored now contradicts itself. A failure here is recorded, never
    # raised -- a broken check must not make a successful sync read as failed.
    say("Auditing the stored data")
    try:
        from app import data_audit
        audit = data_audit.run_data_audit(trigger=_trigger)
        result["audit"] = {"ok": True, "run_id": audit.get("run_id"), "summary": audit.get("summary")}
    except Exception as e:  # noqa: BLE001
        logger.exception("Post-sync data audit failed")
        result["audit"] = {"ok": False, "reason": str(e)}

    result["elapsed_seconds"] = round(time.time() - started, 1)
    result["ok"] = nav_ok
    logger.info(f"Full refresh ({_trigger}) finished in {result['elapsed_seconds']}s: "
                f"NAV {'ok' if nav_ok else 'FAILED'}, TER {'ok' if ter_ok else 'FAILED'}.")
    return result


def verify_sync() -> Dict[str, Any]:
    """Post-sync reconciliation: what the database actually holds, and which of those
    numbers deserve attention. Checks are stated as findings rather than a single score --
    "13,905 schemes have no plan" and "NAVs are 1 day behind" are different problems."""
    con = db.get_connection()
    try:
        nav = con.execute(
            "SELECT count(*), count(DISTINCT scheme_code), min(nav_date), max(nav_date) FROM nav_history"
        ).fetchone()
        schemes_total = con.execute("SELECT count(*) FROM schemes").fetchone()[0]
        unknown_plan, unknown_option = con.execute(
            """SELECT count(*) FILTER (WHERE plan_type IS NULL OR plan_type = ''),
                      count(*) FILTER (WHERE option_type IS NULL OR option_type IN ('', 'Other'))
               FROM schemes WHERE scheme_code IN (SELECT scheme_code FROM summary_table WHERE is_active)"""
        ).fetchone()
        active = con.execute("SELECT count(*) FROM summary_table WHERE is_active").fetchone()[0]
        ter_official = con.execute("SELECT count(*) FROM schemes WHERE ter_status = 'official'").fetchone()[0]
        # A scheme AMFI still publishes daily but whose own last NAV trails the file's
        # newest date is the shape a half-finished ingest leaves behind.
        stale = con.execute(
            """SELECT count(*) FROM (
                   SELECT scheme_code, max(nav_date) d FROM nav_history GROUP BY scheme_code
               ) x WHERE x.d < (SELECT max(nav_date) - 7 FROM nav_history)"""
        ).fetchone()[0]
    finally:
        con.close()

    nav_rows, nav_schemes, nav_min, nav_max = nav
    _, _, expected = is_database_stale()
    lag_days = (expected - nav_max).days if (expected and nav_max) else None
    progress = get_backfill_progress()

    findings: List[Dict[str, str]] = []
    if lag_days and lag_days > 1:
        findings.append({"level": "warning", "text": f"Newest NAV is {nav_max}, {lag_days} days behind the expected {expected}."})
    if unknown_plan:
        findings.append({"level": "info", "text": f"{unknown_plan:,} of {active:,} active schemes have no plan AMFI could confirm."})
    if unknown_option:
        findings.append({"level": "info", "text": f"{unknown_option:,} of {active:,} active schemes have no confirmed option."})
    if active and ter_official / max(active, 1) < 0.5:
        findings.append({"level": "info", "text": f"Official TER covers {ter_official:,} schemes, under half the active universe."})
    if stale:
        findings.append({"level": "info", "text": f"{stale:,} schemes have published nothing for over a week (merged, wound up, or renamed)."})

    return {
        "checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "nav": {"rows": int(nav_rows), "schemes": int(nav_schemes),
                "first_date": nav_min.isoformat() if nav_min else None,
                "last_date": nav_max.isoformat() if nav_max else None,
                "expected_date": expected.isoformat() if expected else None, "lag_days": lag_days},
        "schemes": {"total": int(schemes_total), "active": int(active),
                    "unknown_plan": int(unknown_plan), "unknown_option": int(unknown_option),
                    "ter_official": int(ter_official)},
        "backfill": {"completed_chunks": progress["completed_chunks"]},
        "findings": findings,
    }


# --- PLAN / OPTION RESOLUTION -------------------------------------------------------
#
# AMFI's NAV file leaves Plan and Option blank for ~40% of its rows. The parser no longer
# guesses (see amfi_client.parse_amfi_nav_lines); this settles those rows from AMFI's own
# fund-performance feed, which publishes each fund's Direct and Regular NAV side by side.
# If our scheme's NAV on that date equals the fund's Direct NAV, the scheme IS the Direct
# plan -- arithmetic, not inference.


def _plan_from_navs(nav: float, direct: Optional[float], regular: Optional[float]) -> Optional[str]:
    """Which plan a NAV identifies, or None when it identifies neither or both.

    Both: a fund whose two plans still report the same NAV (seen right after launch,
    before the expense difference has had time to separate them) proves nothing."""
    if direct is not None and regular is not None and round(direct, 4) == round(regular, 4):
        return None
    if direct is not None and round(nav, 4) == round(direct, 4):
        return "Direct"
    if regular is not None and round(nav, 4) == round(regular, 4):
        return "Regular"
    return None


_SNAPSHOT_RETURNS = {
    f"return_{span}_{leg}": f"return{feed}{leg_feed}"
    for span, feed in (("1y", "1Year"), ("3y", "3Year"), ("5y", "5Year"))
    for leg, leg_feed in (("direct", "Direct"), ("regular", "Regular"), ("benchmark", "Benchmark"))
}
_SNAPSHOT_COLUMNS = ["fund_name", "sub_category", "category_id", "fund_house", "scheme_codes", "aum_cr",
                     "benchmark", "riskometer", *_SNAPSHOT_RETURNS, "as_of"]


def _feed_number(raw: Any) -> Optional[float]:
    if raw in (None, "", "-"):
        return None
    try:
        v = float(str(raw).replace(",", ""))
    except ValueError:
        return None
    return v if v == v else None


def _brand_prefixes(fund_houses: List[str]) -> List[Tuple[str, str]]:
    """(lower-case brand, fund house), longest brand first: "ICICI Prudential Mutual Fund"
    brands its schemes "ICICI Prudential ...". Longest first so "Aditya Birla Sun Life"
    wins over a shorter brand that happens to be its prefix."""
    out = []
    first_words: Dict[str, set] = {}
    for house in set(fund_houses):
        brand = re.sub(r"\s+mutual fund\s*$", "", house.strip(), flags=re.I).strip()
        if brand:
            out.append((brand.lower(), house))
            first_words.setdefault(brand.split()[0].lower(), set()).add(house)
    # "Kotak Mahindra Mutual Fund" names its schemes just "Kotak ...": the brand's first word
    # also places a fund, but only where no other AMC shares that word.
    known = {b for b, _ in out}
    out += [(w, next(iter(h))) for w, h in first_words.items() if len(h) == 1 and w not in known]
    return sorted(out, key=lambda b: -len(b[0]))


def _house_by_brand(fund_name: str, brands: List[Tuple[str, str]]) -> Optional[str]:
    low = fund_name.lower()
    for brand, house in brands:
        if low == brand or low.startswith(brand + " "):
            return house
    return None


#: New fund offers price at these round numbers, so many new funds share them on one date:
#: an exact NAV match there identifies nothing.
_NFO_NAVS = (10.0, 100.0, 1000.0)


def _fund_name_by_nav(names_by_nav: Dict[Tuple[datetime.date, str, float], set], nav_date: datetime.date,
                      house: Optional[str], direct: Optional[float], regular: Optional[float]) -> Optional[str]:
    """Our fund-name key for a feed fund the NAME did not match, from its published NAVs: the
    one fund of the same house priced at exactly that NAV (4 dp) that day. None unless every
    published NAV points to the same single fund, and never on an NFO-price NAV."""
    if not house:
        return None
    found: set = set()
    for nav in (direct, regular):
        if nav is None:
            continue
        nav4 = round(float(nav), 4)
        if any(abs(nav4 - p) < 0.01 for p in _NFO_NAVS):
            return None
        keys = names_by_nav.get((nav_date, house, nav4), set())
        if len(keys) != 1:
            if keys:
                return None  # two of our funds share this NAV today: ambiguous
            continue
        found |= keys
    return next(iter(found)) if len(found) == 1 else None


def _fund_snapshot_row(fund: Dict[str, Any], nav_date: Optional[datetime.date], candidates: List[dict],
                       houses: Optional[Dict[str, int]], brands: Optional[List[Tuple[str, str]]] = None
                       ) -> Optional[Dict[str, Any]]:
    """One feed fund as an amfi_fund_snapshot row, or None if it cannot be keyed."""
    from app.amfi_perf_client import parse_nav_date

    name = (fund.get("schemeName") or "").strip()
    as_of = nav_date or parse_nav_date(fund.get("reportDate"))
    if not name or as_of is None:
        return None
    aum = _feed_number(fund.get("dailyAUM"))
    # The house most of the same-named schemes carry; a name shared across AMCs would be a
    # data error, and the majority is the least surprising answer to it. A fund whose feed
    # name no longer matches ours (renamed since: "ICICI Prudential Bluechip" is now "Large
    # Cap") is placed by its brand prefix instead, which every Indian scheme name carries.
    house = max(houses.items(), key=lambda kv: kv[1])[0] if houses else None
    if house is None and brands:
        house = _house_by_brand(name, brands)
    return {
        "fund_name": name,
        "sub_category": (fund.get("_sub_category") or "").strip(),
        "category_id": fund.get("_category_id"),
        "fund_house": house,
        "scheme_codes": sorted({c["scheme_code"] for c in candidates}),
        # AMFI reports AUM in Rs crore; a zero is a fund reporting nothing, not an empty fund.
        "aum_cr": aum if aum and aum > 0 else None,
        "benchmark": (fund.get("benchmark") or "").strip() or None,
        "riskometer": (fund.get("riskometerScheme") or "").strip() or None,
        **{col: _feed_number(fund.get(key)) for col, key in _SNAPSHOT_RETURNS.items()},
        "as_of": as_of,
    }


def _write_fund_snapshot(rows: List[Dict[str, Any]]) -> int:
    """Replaces the snapshot with this fetch's picture in one transaction, so a reader never
    sees half of yesterday and half of today."""
    placeholders = ", ".join(["%s"] * len(_SNAPSHOT_COLUMNS))
    with db.WRITE_LOCK:
        con = db.get_connection()
        try:
            con.begin()
            try:
                con.execute("DELETE FROM amfi_fund_snapshot")
                con.executemany(
                    f"INSERT INTO amfi_fund_snapshot ({', '.join(_SNAPSHOT_COLUMNS)}) VALUES ({placeholders})",
                    [tuple(r[c] for c in _SNAPSHOT_COLUMNS) for r in rows],
                )
                con.commit()
            except Exception:
                con.rollback()
                raise
        finally:
            con.close()
    return len(rows)


def resolve_plan_options(dry_run: bool = False) -> Dict[str, Any]:
    """Fills in plan (and, where unambiguous, option) for schemes AMFI's NAV file does not
    label. Returns a summary; writes only the schemes whose label actually changes.

    `dry_run=True` computes everything and writes nothing, so a correction set can be
    inspected before it lands -- this rewrites identity fields that the TER matcher and
    every fund label depend on."""
    from app.amfi_perf_client import AmfiPerfClient, parse_nav_date, to_nav

    client = AmfiPerfClient()
    # Not the feed's own latest date as-is: over a weekend or holiday that date lists a
    # hundred funds, and the snapshot below is replaced whole.
    _report_date, funds = client.fetch_latest_full()
    if not funds:
        return {"ok": False, "reason": "AMFI fund-performance feed has no full trading day in the last week.",
                "resolved": 0, "plan_changed": 0, "option_changed": 0}

    dates = {parse_nav_date(f.get("navDate")) for f in funds}
    dates.discard(None)
    if not dates:
        return {"ok": False, "reason": "Feed carried no usable NAV date.", "resolved": 0,
                "plan_changed": 0, "option_changed": 0}

    con = db.get_connection()
    try:
        rows = con.execute(
            """SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, n.nav_date, n.nav, s.fund_house
               FROM schemes s JOIN nav_history n ON n.scheme_code = s.scheme_code
               WHERE n.nav_date = ANY(%s)""",
            (sorted(dates),),
        ).fetchall()
        # For the fund snapshot: which AMC a feed fund belongs to (the feed does not say),
        # read off our schemes of the same name whatever date they last priced on.
        house_rows = con.execute(
            "SELECT scheme_name, fund_house FROM schemes WHERE fund_house IS NOT NULL AND fund_house <> ''"
        ).fetchall()
    finally:
        con.close()
    house_by_name: Dict[str, Dict[str, int]] = {}
    for name, house in house_rows:
        key = costs_data.normalize_scheme_name(name or "")
        if key:
            counts = house_by_name.setdefault(key, {})
            counts[house] = counts.get(house, 0) + 1
    brands = _brand_prefixes([h for _, h in house_rows])

    ours: Dict[str, List[dict]] = {}
    # (date, fund house, NAV to 4 dp) -> our fund-name keys priced exactly there that day.
    names_by_nav: Dict[Tuple[datetime.date, str, float], set] = {}
    for code, name, plan, option, nav_date, nav, house in rows:
        key = costs_data.normalize_scheme_name(name or "")
        if key:
            ours.setdefault(key, []).append({
                "scheme_code": int(code), "plan": plan, "option": option,
                "nav_date": nav_date, "nav": round(float(nav), 4),
            })
            if house:
                names_by_nav.setdefault((nav_date, house, round(float(nav), 4)), set()).add(key)

    plan_updates: Dict[int, str] = {}
    option_updates: Dict[int, str] = {}
    riskometer_updates: Dict[int, Tuple[str, datetime.date]] = {}
    snapshot: Dict[Tuple[str, str], Dict[str, Any]] = {}
    resolved = 0
    unmatched_funds = 0

    for fund in funds:
        nav_date = parse_nav_date(fund.get("navDate"))
        direct, regular = to_nav(fund.get("navDirect")), to_nav(fund.get("navRegular"))
        name_key = costs_data.normalize_scheme_name(fund.get("schemeName") or "")
        candidates = [c for c in ours.get(name_key, []) if c["nav_date"] == nav_date]
        if not candidates and nav_date is not None:
            # The name did not match (renamed since, or the feed spells it differently:
            # "ICICI Prudential Large Cap Fund" vs our "... Bluechip Fund"). The feed's own
            # Direct/Regular NAVs identify the fund instead, within the brand's fund house.
            matched_key = _fund_name_by_nav(names_by_nav, nav_date, _house_by_brand(fund.get("schemeName") or "", brands),
                                            direct, regular)
            if matched_key:
                name_key = matched_key
                candidates = [c for c in ours.get(name_key, []) if c["nav_date"] == nav_date]
        row = _fund_snapshot_row(fund, nav_date, candidates, house_by_name.get(name_key), brands)
        if row is not None:
            snapshot[(row["fund_name"], row["sub_category"])] = row
        if not candidates or nav_date is None:
            unmatched_funds += 1
            continue
        # How many of this fund's schemes share each NAV. A value carried by more than one
        # of them (a fund whose Growth and IDCW have not diverged yet) still identifies the
        # PLAN for all of them, but cannot single out which one is the Growth option.
        nav_counts: Dict[float, int] = {}
        for c in candidates:
            nav_counts[c["nav"]] = nav_counts.get(c["nav"], 0) + 1

        # The riskometer belongs to the FUND, not to a plan or option -- SEBI classifies the
        # portfolio, which every Direct/Regular and Growth/IDCW variant shares. So it goes on
        # every scheme matched by name and date, including ones whose plan the NAVs cannot
        # settle (the "both NAVs equal" case below still identifies the fund).
        level = (fund.get("riskometerScheme") or "").strip()
        if level:
            for c in candidates:
                riskometer_updates[c["scheme_code"]] = (level, nav_date)

        for c in candidates:
            plan = _plan_from_navs(c["nav"], direct, regular)
            if plan is None:
                continue
            resolved += 1
            if c["plan"] != plan:
                plan_updates[c["scheme_code"]] = plan
            # Only ever fills an option in, never overwrites one AMFI stated: an IDCW
            # scheme priced identically to its Growth twin is exactly the case above.
            if not c["option"] or c["option"] == "Other":
                if nav_counts[c["nav"]] == 1:
                    option_updates[c["scheme_code"]] = "Growth"

    if (plan_updates or option_updates) and not dry_run:
        with db.WRITE_LOCK:
            con = db.get_connection()
            try:
                # The source is written in the same statement as the value, so the two can
                # never disagree about who set it.
                if plan_updates:
                    con.executemany("UPDATE schemes SET plan_type = %s, plan_source = 'nav_match'"
                                    " WHERE scheme_code = %s",
                                    [(p, c) for c, p in plan_updates.items()])
                if option_updates:
                    con.executemany("UPDATE schemes SET option_type = %s, option_source = 'nav_match'"
                                    " WHERE scheme_code = %s",
                                    [(o, c) for c, o in option_updates.items()])
            finally:
                con.close()

    riskometer_changed = 0
    if riskometer_updates and not dry_run:
        with db.WRITE_LOCK:
            con = db.get_connection()
            try:
                # Rewrites only a label that actually moved, so a nightly run over ~4,000
                # unchanged funds leaves their rows (and the WAL) alone.
                for code, (level, as_of) in riskometer_updates.items():
                    cur = con.execute(
                        "UPDATE schemes SET riskometer = %s, riskometer_as_of = %s WHERE scheme_code = %s"
                        " AND (riskometer IS DISTINCT FROM %s OR riskometer_as_of IS DISTINCT FROM %s)",
                        (level, as_of, code, level, as_of))
                    riskometer_changed += cur.rowcount or 0
            finally:
                con.close()

    snapshot_written = 0
    if snapshot and not dry_run:
        snapshot_written = _write_fund_snapshot(list(snapshot.values()))

    summary = {
        "ok": True,
        "dry_run": dry_run,
        "funds_in_feed": len(funds),
        "funds_not_matched": unmatched_funds,
        "snapshot_funds": len(snapshot),
        "snapshot_written": snapshot_written,
        "snapshot_with_house": sum(1 for r in snapshot.values() if r["fund_house"]),
        "resolved": resolved,
        "plan_changed": len(plan_updates),
        "option_changed": len(option_updates),
        "riskometer_found": len(riskometer_updates),
        "riskometer_changed": riskometer_changed,
        "report_dates": sorted(d.isoformat() for d in dates),
        # A sample rather than thousands of rows: enough to eyeball a correction set
        # without turning an API response into a data dump.
        "sample_plan_changes": [{"scheme_code": c, "plan_type": p} for c, p in list(plan_updates.items())[:10]],
    }
    logger.info(
        f"Plan/option resolution{' (dry run)' if dry_run else ''}: {resolved:,} schemes identified "
        f"from {len(funds):,} funds; {len(plan_updates):,} plan(s) and {len(option_updates):,} option(s) "
        f"{'would change' if dry_run else 'corrected'}."
    )
    return summary


# --- HISTORICAL TER BACKFILL (deeper reach than the daily current-month sync) ---

TER_BACKFILL_JOB = "historical_ter_backfill"

TER_BACKFILL_STATE = {
    "is_running": False,
    "should_stop": False,
    "total_months": 0,
    "current_month_idx": 0,
    "current_month_str": "",
    "skipped_months": 0,
    "records_added": 0,
    "last_error": None,
    "started_at": None,
    "finished_at": None,
}
_TER_BACKFILL_LOCK = threading.Lock()

def get_ter_backfill_status() -> dict:
    with _TER_BACKFILL_LOCK:
        return dict(TER_BACKFILL_STATE)

def stop_ter_backfill():
    """Signals a running TER backfill to halt after its in-flight month finishes — mirrors
    stop_historical_backfill() below. Deliberately unconditional (no is_running guard)."""
    with _TER_BACKFILL_LOCK:
        TER_BACKFILL_STATE["should_stop"] = True

def clear_ter_backfill_checkpoints() -> int:
    """Clears all historical TER backfill checkpoints so the next run reprocesses all months."""
    con = db.get_connection()
    try:
        return bulk.clear_checkpoints(con, TER_BACKFILL_JOB)
    finally:
        con.close()

def ter_months_since(start_year: int) -> List[str]:
    """The months a backfill "since <year>" would actually fetch, newest first.

    Asks the engine rather than arithmetic in the caller: recent_months() counts back from
    the current month and stops at 2018, because AMFI's TER portal holds nothing earlier.
    A caller computing (years * 12) instead would promise months that do not exist -- and
    the UI's own estimate was separately wrong by a constant, offering 18 months for a
    "since 2026" that is nine months long."""
    today = datetime.date.today()
    span = (today.year - start_year) * 12 + today.month
    return AmfiTerClient.recent_months(max(1, span))


def _ter_backfill_worker(n_months: int, resume: bool = True):
    global TER_BACKFILL_STATE
    all_months = AmfiTerClient.recent_months(n_months)

    con = db.get_connection()
    try:
        completed = bulk.completed_units(con, TER_BACKFILL_JOB) if resume else set()
    finally:
        con.close()

    pending = [m for m in all_months if m not in completed]
    skipped = len(all_months) - len(pending)

    with _TER_BACKFILL_LOCK:
        TER_BACKFILL_STATE.update({
            "is_running": True,
            "should_stop": False,
            "total_months": len(all_months),
            "current_month_idx": 0,
            "current_month_str": "",
            "skipped_months": skipped,
            "records_added": 0,
            "last_error": None,
            "started_at": time.time(),
            "finished_at": None,
        })

    if skipped:
        logger.info(f"Starting historical TER backfill: {len(pending)} month(s) to process "
                    f"({skipped} already completed in previous run -- resuming, not redoing).")
    else:
        logger.info(f"Starting historical TER backfill: {len(pending)} month(s) to process.")

    stopped_early = False
    for i, month in enumerate(pending):
        with _TER_BACKFILL_LOCK:
            if TER_BACKFILL_STATE["should_stop"]:
                stopped_early = True
                break
            TER_BACKFILL_STATE["current_month_idx"] = i + 1
            TER_BACKFILL_STATE["current_month_str"] = month

        try:
            month_stats: Dict[str, Any] = {}
            success, msg = sync_official_ter(_trigger="backfill", months=[month], stats=month_stats)
            rows_written = int(month_stats.get("rows_written") or 0)
            stopped = False
            with _TER_BACKFILL_LOCK:
                stopped = TER_BACKFILL_STATE["should_stop"]
                # Was pinned at 0 for the whole run, so the progress card reported "0 records"
                # after writing 1.4M of them, and every checkpoint logged rows_written = 0.
                TER_BACKFILL_STATE["records_added"] += rows_written

            if success and not stopped:
                con = db.get_connection()
                try:
                    bulk.checkpoint_mark(con, TER_BACKFILL_JOB, month, "done", rows_written=rows_written)
                finally:
                    con.close()
            elif not success:
                con = db.get_connection()
                try:
                    bulk.checkpoint_mark(con, TER_BACKFILL_JOB, month, "failed", detail=msg[:500])
                finally:
                    con.close()
        except Exception as e:
            logger.error(f"Error backfilling TER for {month}: {e}")
            with _TER_BACKFILL_LOCK:
                TER_BACKFILL_STATE["last_error"] = str(e)
            try:
                con = db.get_connection()
                try:
                    bulk.checkpoint_mark(con, TER_BACKFILL_JOB, month, "failed", detail=str(e)[:500])
                finally:
                    con.close()
            except Exception:
                pass

        time.sleep(0.2)

    with _TER_BACKFILL_LOCK:
        TER_BACKFILL_STATE["is_running"] = False
        TER_BACKFILL_STATE["finished_at"] = time.time()

    if stopped_early:
        logger.info(f"TER historical backfill stopped by user request after {TER_BACKFILL_STATE['current_month_idx']} of {len(pending)} month(s).")
    else:
        logger.info(f"TER historical backfill completed for {len(pending)} month(s).")

def start_ter_backfill(n_months: int = 12, resume: bool = True) -> bool:
    """Launches a deeper historical TER backfill in a background worker thread with durable checkpointing."""
    with _TER_BACKFILL_LOCK:
        if TER_BACKFILL_STATE["is_running"]:
            return False
    if not resume:
        clear_ter_backfill_checkpoints()
    t = threading.Thread(target=_ter_backfill_worker, args=(n_months, resume), daemon=True)
    t.start()
    return True


def sync_amc_90d_history(mf_id: int, amc_name: str, days: int = 90) -> Tuple[int, int]:
    """Serialized via db.WRITE_LOCK — see sync_daily_nav()."""
    with db.WRITE_LOCK:
        return _sync_amc_90d_history_impl(mf_id, amc_name, days)

def _sync_amc_90d_history_impl(mf_id: int, amc_name: str, days: int = 90) -> Tuple[int, int]:
    """
    Downloads 90-day NAV history for a single AMC and stores in DuckDB.
    Uses high-speed DataFrame staging bulk operations.
    """
    client = AmfiClient()
    today = datetime.date.today()
    from_date = today - datetime.timedelta(days=days)

    raw_text = client.download_amc_90d_report(mf_id, from_date, today)
    if not raw_text or len(raw_text) < 100:
        return 0, 0

    schemes_dict = {}
    nav_rows = []

    for scheme_meta, nav_record in client.parse_amfi_nav_lines(raw_text, default_amc=amc_name):
        code = scheme_meta["scheme_code"]
        if code not in schemes_dict:
            schemes_dict[code] = scheme_meta
        nav_rows.append((nav_record["scheme_code"], nav_record["nav_date"], nav_record["nav"]))

    if not nav_rows:
        return 0, 0

    try:
        # No clearing: each scheme's metadata here is taken from its first -- oldest -- row,
        # up to 90 days stale. See _merge_amfi_payload's reinvestment_clears.
        return _merge_amfi_payload(schemes_dict, nav_rows, label=f"amc {amc_name}",
                                   reinvestment_clears=False)
    except Exception as e:
        logger.error(f"Error updating database for AMC {amc_name}: {e}")
        return 0, 0


def get_latest_expected_trading_date() -> datetime.date:
    """
    Computes the most recent business date for which official AMFI closing NAVs should already be published.
    Considers the 23:00 IST publication window and weekend market closures.
    """
    now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    today = now_ist.date()
    weekday = today.weekday()  # 0=Monday, ..., 4=Friday, 5=Saturday, 6=Sunday

    # Before 23:00 IST: today's market NAVs are not yet published; latest available is previous trading day
    if now_ist.hour < 23:
        if weekday == 0:  # Monday morning/afternoon -> Friday
            return today - datetime.timedelta(days=3)
        elif weekday == 6:  # Sunday -> Friday
            return today - datetime.timedelta(days=2)
        elif weekday == 5:  # Saturday -> Friday
            return today - datetime.timedelta(days=1)
        else:  # Tuesday to Friday morning/afternoon -> Yesterday
            return today - datetime.timedelta(days=1)
    else:
        # After 23:00 IST:
        if weekday in (5, 6):  # Saturday or Sunday night -> Friday
            days_back = 1 if weekday == 5 else 2
            return today - datetime.timedelta(days=days_back)
        else:
            # Monday to Friday night -> Today's NAV is available
            return today


#: The evening NAV pass. AMFI's file carries the day's NAVs from about 23:00 (when
#: get_latest_expected_trading_date starts expecting them); the sync waits half an hour so
#: the late AMCs are in it.
EVENING_SYNC = datetime.time(23, 30)
#: How long that pass may take after its slot before "behind" means overdue.
EVENING_SYNC_GRACE = datetime.timedelta(minutes=20)
PUBLISH_FROM = datetime.time(23, 0)


def evening_sync_pending(now_ist: Optional[datetime.datetime] = None) -> bool:
    """True from 23:00 on a weekday until the evening sync's slot (plus grace) has passed,
    while the background daemon is on: the database is a day behind AMFI then, but only
    because the scheduled sync has not had its turn -- nothing for the owner to do. The
    top bar used to tell them to "catch up in Data Management" every weekday night from
    23:00 to 23:30."""
    if not settings.enable_sync_daemon:
        return False
    now = now_ist or _now_ist()
    if now.weekday() >= 5:
        return False
    deadline = datetime.datetime.combine(now.date(), EVENING_SYNC) + EVENING_SYNC_GRACE
    return now.time() >= PUBLISH_FROM and now < deadline


def is_database_stale() -> Tuple[bool, Optional[datetime.date], Optional[datetime.date]]:
    """
    Checks whether the local DuckDB database is behind the latest expected AMFI publication date.
    Returns (is_stale, current_max_date, expected_date).
    """
    try:
        stats = db.get_database_stats()
        max_dt = stats.get("max_date")
        if not max_dt:
            return True, None, get_latest_expected_trading_date()
        if isinstance(max_dt, str):
            max_d = pd.to_datetime(max_dt).date()
        elif isinstance(max_dt, datetime.datetime):
            max_d = max_dt.date()
        else:
            max_d = max_dt
        expected = get_latest_expected_trading_date()
        is_stale = max_d < expected
        return is_stale, max_d, expected
    except Exception as e:
        logger.warning(f"Could not check database staleness: {e}")
        return False, None, None


def catch_up(trigger: str = "heartbeat") -> Optional[str]:
    """Brings the database up to date if it has fallen behind; returns the job kind it ran.

    * The nightly full refresh was missed (machine off at 00:05) or failed: the whole chain,
      after filling any missed days -- plans, TER, the NAV re-check and the audit included,
      not just NAVs, which used to leave those a day behind until the next midnight.
    * Only NAVs are behind: missed days filled, then today's NAVs.

    A fresh database with no NAVs yet gets the NAV catch-up only; the full chain has
    nothing to identify plans or match TER against until NAVs exist."""
    stale, current_max, expected = is_database_stale()
    if current_max is not None and full_refresh_due(_load_last_jobs().get("full")):
        logger.info(f"Full refresh due ({trigger}): none has succeeded since the last nightly slot.")
        return "full" if run_sync_job("full", trigger, _catchup_full_body) is not None else None
    if stale:
        logger.info(f"Database behind ({trigger}): local data at {current_max}, expected {expected}. Catching up...")
        return "catchup" if run_sync_job("catchup", trigger, _catchup_body) is not None else None
    return None


def check_and_catchup_sync() -> bool:
    """Kept for callers of the old name; see catch_up()."""
    return catch_up("catchup") is not None


def run_scheduled_sync_daemon():
    """
    Background daemon that runs daily sync at 00:05 IST, runs immediate catch-up on startup,
    and runs hourly heartbeats so no missed syncs ever leave the database behind.
    """
    import schedule

    logger.info("Starting AMFI daily sync daemon in background thread...")

    # 1. Startup catch-up: if the machine was off overnight, the missed full refresh (or just
    # the missed NAVs) runs now rather than waiting for the next midnight.
    ran = None
    try:
        time.sleep(3)  # Brief delay to allow initial app load before launching catch-up
        ran = catch_up("startup")
    except Exception as e:
        logger.error(f"Startup catch-up error: {e}")

    # 1b. This month's official TER on start, unless the catch-up's full refresh has just
    # fetched it or one was fetched in the last few hours (see startup_ter_due).
    if ran != "full" and startup_ter_due(_load_last_jobs()):
        try:
            run_sync_job("ter", "startup", _ter_body)
        except Exception as e:
            logger.error(f"Startup TER sync error: {e}")

    # 2. Daily midnight triggers (when AMFI finalizes the day's NAVs). The 00:05 run is the
    # full refresh -- NAVs, then the plan/option resolution that depends on those NAVs, then
    # TER (whose matching depends on the plan), then ONE summary rebuild. The late run is
    # NAVs only: TER is a monthly disclosure and plans do not change intraday. Both go
    # through the job tracker, so they show on the page and never overlap another sync.
    schedule.every().day.at("00:05").do(run_sync_job, "full", "scheduled_00:05", _full_body)
    schedule.every().day.at(EVENING_SYNC.strftime("%H:%M")).do(run_sync_job, "nav", "scheduled_23:30", _nav_body)

    # 3. Hourly heartbeat: a missed or failed full refresh, or NAVs that have fallen behind.
    schedule.every(1).hours.do(catch_up, "heartbeat")

    while True:
        try:
            schedule.run_pending()
        except Exception as loop_err:
            logger.error(f"Error during schedule.run_pending(): {loop_err}")
        time.sleep(30)


_SYNC_DAEMON_THREAD = None
_SYNC_DAEMON_LOCK = threading.Lock()

def ensure_sync_daemon_running():
    """
    Idempotent thread starter. Ensures the background sync daemon is active,
    regardless of which page/request accesses the app first.

    GATED behind settings.enable_sync_daemon (default True) -- see
    app/core/config.py. This backend is the sole writer of its PostgreSQL
    database (see app/db/connection.py), so the flag is an explicit off-switch
    for e.g. a read-only exploration session, not a cross-process safety guard.
    """
    global _SYNC_DAEMON_THREAD
    if not settings.enable_sync_daemon:
        logger.info("Sync daemon NOT started: ENABLE_SYNC_DAEMON is false.")
        return
    with _SYNC_DAEMON_LOCK:
        if _SYNC_DAEMON_THREAD is None or not _SYNC_DAEMON_THREAD.is_alive():
            _SYNC_DAEMON_THREAD = threading.Thread(
                target=run_scheduled_sync_daemon,
                name="AMFI_Sync_Daemon",
                daemon=True
            )
            _SYNC_DAEMON_THREAD.start()
            logger.info("AMFI scheduled sync daemon thread started successfully.")



# --- HISTORICAL MULTI-YEAR BACKFILL ENGINE (2020 TO PRESENT) ---

BACKFILL_STATE = {
    "is_running": False,
    "should_stop": False,
    "total_chunks": 0,
    "current_chunk_idx": 0,
    "current_chunk_str": "",
    "records_added": 0,
    "schemes_added": 0,
    "last_error": None,
    "started_at": None,
    "finished_at": None
}
_BACKFILL_LOCK = threading.Lock()

def get_backfill_status() -> dict:
    with _BACKFILL_LOCK:
        return dict(BACKFILL_STATE)

def stop_historical_backfill():
    """Unconditional, matching stop_ter_backfill() above -- a previous version of this
    function only set should_stop under an ``if BACKFILL_STATE["is_running"]:`` guard,
    which could silently no-op a stop request that landed in the narrow window around a
    status read/write race, with zero feedback that anything had gone wrong. Harmless to
    call when idle either way: _historical_backfill_worker() resets should_stop to False
    itself the moment a new run actually starts."""
    with _BACKFILL_LOCK:
        BACKFILL_STATE["should_stop"] = True

def generate_backfill_chunks(start_year: int = 2020) -> list:
    """Generates 88-day non-overlapping intervals in reverse chronological order from today back to start_year."""
    start = datetime.date(start_year, 1, 1)
    end = datetime.date.today()
    cur = end
    chunks = []
    while cur > start:
        prev = max(start, cur - datetime.timedelta(days=88))
        chunks.append((prev, cur))
        cur = prev - datetime.timedelta(days=1)
    return chunks

class ChunkDownloadError(RuntimeError):
    """AMFI did not return a usable NAV report for this chunk.

    Raised rather than returned as (0, 0) because the worker checkpoints whatever
    it gets back without an exception: an unusable download reported as "zero rows"
    marks that date range *done*, so every later resume skips it and the gap is
    never filled. Observed for real on 2026-09-23, when the historical-report
    endpoint began serving its own HTML form page (which parses to zero rows)
    instead of the report; a whole backfill would have checkpointed itself
    complete having ingested nothing."""


def _looks_like_nav_report(raw_text: str) -> bool:
    """True for AMFI's text report (daily or historical), which always carries the
    "Scheme Code" header. An error/form page served with HTTP 200 does not -- which
    is the only way to tell the two apart, since both arrive as a 200 with a body."""
    return "scheme code" in raw_text[:4000].lower()


def backfill_single_chunk(from_date: datetime.date, to_date: datetime.date) -> Tuple[int, int]:
    """Serialized via db.WRITE_LOCK — see sync_daily_nav()."""
    with db.WRITE_LOCK:
        return _backfill_single_chunk_impl(from_date, to_date)

def _backfill_single_chunk_impl(from_date: datetime.date, to_date: datetime.date) -> Tuple[int, int]:
    """Downloads a single historical chunk for ALL mutual funds across India and bulk merges into DuckDB."""
    client = AmfiClient()
    logger.info(f"Downloading historical chunk: {from_date} to {to_date}...")
    raw_text = client.download_bulk_historical_report(from_date, to_date)
    if not raw_text or len(raw_text) < 100:
        raise ChunkDownloadError(f"Empty or failed download for chunk {from_date} to {to_date}")
    if not _looks_like_nav_report(raw_text):
        raise ChunkDownloadError(
            f"AMFI returned {len(raw_text):,} bytes that are not a NAV report (no 'Scheme Code' header) "
            f"for chunk {from_date} to {to_date} -- the portal is likely serving an error or form page."
        )

    # Step-by-step timing: kept from the investigation that found the quadratic staging
    # merge (see db/bulk.py). This chunk's date range hung twice with zero log output past
    # the download, and per-step timing is what localized it -- worth keeping permanently
    # so the next regression here is one log read away instead of another investigation.
    _t_parse_start = time.time()
    schemes_dict = {}
    nav_rows = []
    for scheme_meta, nav_record in client.parse_amfi_nav_lines(raw_text):
        code = scheme_meta["scheme_code"]
        if code not in schemes_dict:
            schemes_dict[code] = scheme_meta
        nav_rows.append((nav_record["scheme_code"], nav_record["nav_date"], nav_record["nav"]))
    logger.info(f"backfill chunk {from_date}-{to_date}: line parsing done in {time.time() - _t_parse_start:.1f}s -- {len(schemes_dict):,} unique schemes, {len(nav_rows):,} NAV rows.")

    # A *valid* report that happens to carry no rows (a range older than AMFI's own
    # history) is genuinely complete, so this returns rather than raising: the chunk gets
    # checkpointed and is never re-downloaded. Only an unusable response raises, above.
    if not nav_rows:
        logger.info(f"Chunk {from_date} to {to_date}: valid report, no NAV rows in range -- nothing to ingest.")
        return 0, 0

    _t_merge_start = time.time()
    sch_cnt, nav_cnt = _merge_amfi_payload(
        schemes_dict, nav_rows,
        label=f"backfill {from_date}-{to_date}",
        # Polled between committed batches: a stop request lands within one batch
        # instead of after the whole chunk, and every batch already committed stays.
        should_stop=lambda: get_backfill_status().get("should_stop", False),
        reinvestment_clears=False,
    )
    logger.info(f"backfill chunk {from_date}-{to_date}: merged {nav_cnt:,} NAVs / "
                f"{sch_cnt:,} schemes in {time.time() - _t_merge_start:.1f}s.")
    return sch_cnt, nav_cnt

BACKFILL_JOB = "historical_nav_backfill"


def _chunk_unit(frm: datetime.date, to_dt: datetime.date) -> str:
    """Stable identity for one chunk of work, used as its checkpoint key. Derived
    from the date range, not the chunk's position in the list, so it stays valid
    when the list is regenerated with a different start year or a later end date."""
    return f"{frm.isoformat()}..{to_dt.isoformat()}"


def get_backfill_progress() -> dict:
    """Durable progress, as opposed to get_backfill_status()'s in-memory view of the
    *current* run -- this survives a stop, a crash and a container restart."""
    con = db.get_connection()
    try:
        done = bulk.completed_units(con, BACKFILL_JOB)
        return {"completed_chunks": len(done), "completed_units": sorted(done)}
    finally:
        con.close()


def _historical_backfill_worker(start_year: int, max_chunks: Optional[int]):
    global BACKFILL_STATE
    chunks = generate_backfill_chunks(start_year)
    if max_chunks:
        chunks = chunks[:max_chunks]

    # Resume rather than restart. Every chunk that finished in a previous run is
    # recorded durably, so a stopped/crashed/restarted backfill skips straight to
    # where it left off instead of re-downloading and re-merging an hour of work
    # it already has. (Redoing it would be *correct* -- the merge is an idempotent
    # upsert -- just wasteful, which is exactly the failure mode this avoids.)
    con = db.get_connection()
    try:
        already_done = bulk.completed_units(con, BACKFILL_JOB)
    finally:
        con.close()
    pending = [(f, t) for (f, t) in chunks if _chunk_unit(f, t) not in already_done]
    skipped = len(chunks) - len(pending)

    with _BACKFILL_LOCK:
        BACKFILL_STATE["is_running"] = True
        BACKFILL_STATE["should_stop"] = False
        BACKFILL_STATE["total_chunks"] = len(pending)
        BACKFILL_STATE["current_chunk_idx"] = 0
        BACKFILL_STATE["records_added"] = 0
        BACKFILL_STATE["schemes_added"] = 0
        BACKFILL_STATE["skipped_chunks"] = skipped
        BACKFILL_STATE["last_error"] = None
        BACKFILL_STATE["started_at"] = time.time()
        BACKFILL_STATE["finished_at"] = None

    if skipped:
        logger.info(f"Starting historical backfill: {len(pending)} chunks to process "
                    f"({skipped} already completed in a previous run -- resuming, not redoing).")
    else:
        logger.info(f"Starting historical backfill: {len(pending)} chunks to process.")

    chunks = pending
    for i, (frm, to_dt) in enumerate(chunks):
        with _BACKFILL_LOCK:
            if BACKFILL_STATE["should_stop"]:
                logger.info("Historical backfill stopped by user request.")
                break
            BACKFILL_STATE["current_chunk_idx"] = i + 1
            BACKFILL_STATE["current_chunk_str"] = f"{frm.strftime('%d-%b-%Y')} to {to_dt.strftime('%d-%b-%Y')}"

        try:
            sch_cnt, nav_cnt = backfill_single_chunk(frm, to_dt)
            with _BACKFILL_LOCK:
                BACKFILL_STATE["records_added"] += nav_cnt
                BACKFILL_STATE["schemes_added"] = max(BACKFILL_STATE["schemes_added"], sch_cnt)
                stopped = BACKFILL_STATE["should_stop"]
            # Only a chunk that ran to completion is checkpointed. A chunk cut short
            # by a stop request committed a correct *prefix* of its NAV rows, so
            # leaving it unmarked means the next run redoes it and fills in the rest.
            if not stopped:
                con = db.get_connection()
                try:
                    bulk.checkpoint_mark(con, BACKFILL_JOB, _chunk_unit(frm, to_dt),
                                         "done", rows_written=nav_cnt)
                finally:
                    con.close()
            logger.info(f"Chunk {i+1}/{len(chunks)} ({frm} to {to_dt}): ingested {nav_cnt:,} NAVs.")
            # Every 10th chunk, not every 3rd: refresh_summary_table() recomputes returns/52W
            # stats across every scheme in one full-table pass, so it's real, non-incremental
            # cost -- rebuilding it 16 times over a 48-chunk backfill (the old "every 3"
            # cadence) redid that same full-table work 3x more often than "every 10" for a
            # marginal freshness gain the UI polls right past anyway (5s refetch interval).
            # should_stop is still honored between every single chunk regardless (below/above)
            # -- only the summary-table rebuild cadence changed, not stop responsiveness.
            if (i + 1) % 10 == 0 or (i + 1) == len(chunks):
                db.refresh_summary_table()
        except Exception as e:
            logger.error(f"Error processing chunk {frm} to {to_dt}: {e}")
            with _BACKFILL_LOCK:
                BACKFILL_STATE["last_error"] = str(e)
            # Recorded as 'failed', not 'done', so it is retried on the next run
            # rather than silently skipped -- and so a persistently bad date range
            # is visible in the table instead of only in a scrolled-past log line.
            try:
                con = db.get_connection()
                try:
                    bulk.checkpoint_mark(con, BACKFILL_JOB, _chunk_unit(frm, to_dt),
                                         "failed", detail=str(e)[:500])
                finally:
                    con.close()
            except Exception:
                pass

        # A short courtesy pause, not a rate-limit workaround -- AMFI documents no per-request
        # limit for this endpoint, and each chunk's own download (tens of seconds) already
        # spaces requests out far more than this ever could. Cut from 1s: over a 48-chunk
        # backfill (2015-present) that alone was 48s of pure dead time contributing nothing.
        time.sleep(0.2)

    # A backfill imports years of history at once, which is where old redenominations live.
    # Adjusting before the rebuild means the summary table's returns are computed from a
    # continuous series rather than from a raw ratio that reads as a -90% collapse.
    try:
        adjusted = db.normalize_nav_splits()
        if adjusted:
            logger.info(f"Historical backfill: adjusted {adjusted:,} NAV rows for unit splits.")
    except Exception:
        logger.exception("NAV split normalization after backfill failed")

    db.refresh_summary_table()

    with _BACKFILL_LOCK:
        BACKFILL_STATE["is_running"] = False
        BACKFILL_STATE["finished_at"] = time.time()
    logger.info("Historical backfill completed successfully!")

def start_historical_backfill(start_year: int = 2020, max_chunks: Optional[int] = None,
                              resume: bool = True) -> bool:
    """Launches the historical backfill in a background worker thread.

    Resumes by default, skipping chunks a previous run already completed. Pass
    resume=False to forget that progress and re-download everything -- worth doing
    only if AMFI is believed to have restated history, since the merge itself
    already overwrites changed NAVs on any overlapping run."""
    with _BACKFILL_LOCK:
        if BACKFILL_STATE["is_running"]:
            return False
    if not resume:
        con = db.get_connection()
        try:
            n = bulk.clear_checkpoints(con, BACKFILL_JOB)
            logger.info(f"Historical backfill: cleared {n} checkpoint(s) for a full re-run.")
        finally:
            con.close()
    t = threading.Thread(target=_historical_backfill_worker, args=(start_year, max_chunks), daemon=True)
    t.start()
    return True
