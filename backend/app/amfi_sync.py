import datetime
import logging
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
    "option_type", "isin", "expense_ratio", "ter_status", "ter_source",
    "ter_source_url", "ter_as_of_date",
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
SCHEME_UPDATE = {
    "scheme_name": "excluded.scheme_name",
    "fund_house": "CASE WHEN excluded.fund_house IS NOT NULL AND excluded.fund_house != ''"
                  " THEN excluded.fund_house ELSE schemes.fund_house END",
    "category": "CASE WHEN excluded.category IS NOT NULL AND excluded.category != ''"
                " THEN excluded.category ELSE schemes.category END",
    "plan_type": "excluded.plan_type",
    "option_type": "excluded.option_type",
    "isin": "COALESCE(excluded.isin, schemes.isin)",
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
            s["plan_type"], s["option_type"], s["isin"],
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
) -> Tuple[int, int]:
    """Merges one parsed AMFI payload into schemes + nav_history.

    Two primary-key upserts, no staging tables, no joins -- see db/bulk.py for
    why the staging-table merge this replaced was quadratic. Returns
    (schemes written, NAV rows written)."""
    scheme_rows = _scheme_rows(schemes_dict)
    nav_final = _dedupe_nav(nav_rows)

    con = db.get_connection()
    try:
        sch = bulk.upsert(
            con,
            table="schemes",
            columns=SCHEME_COLUMNS,
            conflict_columns=["scheme_code"],
            rows=scheme_rows,
            update=SCHEME_UPDATE,
            label=f"{label} schemes",
        )
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
        return sch.rows_written, nav.rows_written
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

def sync_daily_nav(_trigger: str = "manual") -> Tuple[bool, str]:
    """Serialized via db.WRITE_LOCK so this can never race the backfill worker, a cost
    import, or another sync call writing to the same DuckDB connection at the same time.
    Every call (scheduled, heartbeat, or manual) records its outcome via get_sync_history()."""
    try:
        with db.WRITE_LOCK:
            success, msg = _sync_daily_nav_impl()
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

def _sync_daily_nav_impl() -> Tuple[bool, str]:
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
    db.refresh_summary_table()
    msg = f"Successfully synced {n_nav:,} NAV records across {n_schemes:,} schemes!"
    logger.info(msg)
    return True, msg


def sync_official_ter(_trigger: str = "manual", months: Optional[List[str]] = None) -> Tuple[bool, str]:
    """Fetches AMFI's official, dated TER-portal disclosure (SEBI Regulation 66) and promotes
    matched schemes' current TER to 'official' status, run on the same daily cadence as
    sync_daily_nav().

    Unlike sync_daily_nav(), this does NOT hold db.WRITE_LOCK for its whole duration: the
    AMFI TER API caps pageSize at 100, so covering one calendar month can take hundreds of
    sequential HTTP requests (multiple minutes by month-end) — holding WRITE_LOCK across all
    of that would block every other writer (a manual "Sync Now" click) for the whole fetch.
    Only the two actual DB-mutating calls inside (db.upsert_ter_history,
    db.apply_latest_official_ter) take WRITE_LOCK, and only for their own brief duration.
    """
    try:
        success, msg = _sync_official_ter_impl(months)
    except Exception as e:
        _record_ter_sync_result(False, str(e), _trigger)
        raise
    _record_ter_sync_result(success, msg, _trigger)
    return success, msg

def _sync_official_ter_impl(months: Optional[List[str]] = None) -> Tuple[bool, str]:
    client = AmfiTerClient()
    recent = AmfiTerClient.recent_months(2)
    if months is None:
        con = db.get_connection()
        try:
            has_recent_ter = con.execute(
                "SELECT 1 FROM ter_history WHERE ter_date >= (CURRENT_DATE - INTERVAL '60 days') LIMIT 1;"
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

        for _, our_row in our_matches.iterrows():
            raw_plan = str(our_row["plan_type"] or "").strip().lower()
            raw_name = str(our_row["scheme_name"] or "").strip().lower()
            if "direct" in raw_plan or "direct" in raw_name:
                prefix = "d_"
            elif "regular" in raw_plan or "regular" in raw_name:
                prefix = "r_"
            else:
                continue  # Unrecognized plan type — never guess which column applies.

            scheme_code = int(our_row["scheme_code"])
            matched_scheme_codes.add(scheme_code)
            for dr in date_rows:
                history_rows.append({
                    "scheme_code": scheme_code,
                    "ter_date": dr["ter_date"],
                    "base_expense_ratio_pct": dr[f"{prefix}ber"],
                    "brokerage_cost_pct": dr[f"{prefix}brokerage"],
                    "transaction_cost_pct": dr[f"{prefix}transaction"],
                    "statutory_levies_pct": dr[f"{prefix}statutory"],
                    "total_ter_pct": dr[f"{prefix}ter"],
                    "source_url": TER_PORTAL_PAGE_URL,
                })

            latest = date_rows[-1]
            existing = latest_by_scheme.get(scheme_code)
            if existing is None or latest["ter_date"] >= existing["ter_date"]:
                latest_by_scheme[scheme_code] = {
                    "scheme_code": scheme_code,
                    "ter_date": latest["ter_date"],
                    "base_expense_ratio_pct": latest[f"{prefix}ber"],
                    "brokerage_cost_pct": latest[f"{prefix}brokerage"],
                    "transaction_cost_pct": latest[f"{prefix}transaction"],
                    "statutory_levies_pct": latest[f"{prefix}statutory"],
                    "total_ter_pct": latest[f"{prefix}ter"],
                    "source_url": TER_PORTAL_PAGE_URL,
                    "ter_source": "AMFI Total Expense Ratio Disclosure (Regulation 66, auto-synced)",
                }

    if not history_rows:
        return False, "AMFI TER portal data fetched, but no schemes matched unambiguously by name."

    df_history = pd.DataFrame(history_rows).drop_duplicates(subset=["scheme_code", "ter_date"])
    n_history = db.upsert_ter_history(df_history)

    df_latest = pd.DataFrame(latest_by_scheme.values())
    result = db.apply_latest_official_ter(df_latest)

    msg = (
        f"Matched {len(matched_scheme_codes):,} schemes across {len(months_fetched)} month(s) "
        f"({', '.join(months_fetched)}); stored {n_history:,} dated TER records; "
        f"refreshed current TER for {result['updated']:,} schemes."
    )
    logger.info(f"AMFI TER sync: {msg}")
    return True, msg


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
            success, msg = sync_official_ter(_trigger="backfill", months=[month])
            stopped = False
            with _TER_BACKFILL_LOCK:
                stopped = TER_BACKFILL_STATE["should_stop"]

            if success and not stopped:
                con = db.get_connection()
                try:
                    bulk.checkpoint_mark(con, TER_BACKFILL_JOB, month, "done")
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
        return _merge_amfi_payload(schemes_dict, nav_rows, label=f"amc {amc_name}")
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


def check_and_catchup_sync() -> bool:
    """
    Evaluates database staleness and automatically triggers catch-up.
    If multiple days were missed (e.g. computer was turned off for a long weekend or vacation),
    it automatically queries AMFI's official historical range API to seamlessly fill all missing
    intermediate days so there are zero data gaps in the charts.
    """
    stale, current_max, expected = is_database_stale()
    if not stale:
        return False

    logger.info(f"Database staleness detected! Local data is at {current_max}, expected {expected}. Initiating automatic catch-up...")

    # If gap is more than 1 day (e.g. missed 3 days, a week, etc.), backfill the intermediate days
    if current_max and (expected - current_max).days > 1:
        gap_days = (expected - current_max).days
        logger.info(f"Multi-day gap detected ({gap_days} days). Auto-filling missing range: {current_max} to {expected}...")
        try:
            cur_start = current_max
            while cur_start < expected:
                cur_end = min(expected, cur_start + datetime.timedelta(days=88))
                sch_cnt, nav_cnt = backfill_single_chunk(cur_start, cur_end)
                logger.info(f"Auto-filled {nav_cnt:,} intermediate NAV records across {sch_cnt:,} schemes for {cur_start} to {cur_end}.")
                cur_start = cur_end + datetime.timedelta(days=1)
        except Exception as e:
            logger.error(f"Error during multi-day gap auto-backfill: {e}")

    # Always fetch latest daily closing master feed and refresh summary table
    success, msg = sync_daily_nav(_trigger="catchup")
    logger.info(f"Daily sync completed: success={success}, msg={msg}")
    return success


def run_scheduled_sync_daemon():
    """
    Background daemon that runs daily sync at 00:05 IST, runs immediate catch-up on startup,
    and runs hourly heartbeats so no missed syncs ever leave the database behind.
    """
    import schedule

    logger.info("Starting AMFI daily sync daemon in background thread...")

    # 1. Startup catch-up: If the app was opened past 12 AM (e.g. computer was turned off overnight),
    # catch up immediately in background!
    try:
        time.sleep(3)  # Brief delay to allow initial app load before launching catch-up
        check_and_catchup_sync()
    except Exception as e:
        logger.error(f"Startup catch-up error: {e}")

    # 1b. Startup TER catch-up: current month's official TER, refreshed once per app start
    # regardless of the daily schedule below (e.g. after a long-running container restart).
    try:
        sync_official_ter(_trigger="startup")
    except Exception as e:
        logger.error(f"Startup TER sync error: {e}")

    # 2. Daily midnight triggers (when AMFI finalizes the day's NAVs)
    schedule.every().day.at("00:05").do(sync_daily_nav, _trigger="scheduled_00:05")
    schedule.every().day.at("23:30").do(sync_daily_nav, _trigger="scheduled_23:30")

    # 2b. Daily official TER sync (current month only — a deeper backfill is a manual action
    # from the Data Management page). Offset from the NAV jobs above purely so their log lines
    # don't interleave; db.WRITE_LOCK would serialize any real overlap regardless.
    schedule.every().day.at("00:20").do(sync_official_ter, _trigger="scheduled_00:20")

    # 3. Hourly heartbeat catch-up: Checks every hour if data has fallen behind
    schedule.every(1).hours.do(check_and_catchup_sync)

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
        logger.warning(f"Empty or failed download for chunk {from_date} to {to_date}")
        return 0, 0

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

    if not nav_rows:
        return 0, 0

    _t_merge_start = time.time()
    sch_cnt, nav_cnt = _merge_amfi_payload(
        schemes_dict, nav_rows,
        label=f"backfill {from_date}-{to_date}",
        # Polled between committed batches: a stop request lands within one batch
        # instead of after the whole chunk, and every batch already committed stays.
        should_stop=lambda: get_backfill_status().get("should_stop", False),
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
