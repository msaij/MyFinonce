import datetime
import logging
import threading
import time
from typing import Tuple, Optional, List, Dict, Any

import pandas as pd

from app.amfi_client import AmfiClient
from app.amfi_ter_client import AmfiTerClient, TER_PORTAL_PAGE_URL
from app.core.config import settings
from app.db import queries as db
from app import costs_data

logger = logging.getLogger("amfi_sync")
logger.setLevel(logging.INFO)

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
    return success, msg

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

    df_stg_schemes = pd.DataFrame([
        {
            "scheme_code": s["scheme_code"],
            "scheme_name": s["scheme_name"],
            "fund_house": s["fund_house"],
            "category": s["category"],
            "plan_type": s["plan_type"],
            "option_type": s["option_type"],
            "isin": s["isin"],
            **costs_data.get_scheme_cost_specs(s["scheme_code"], s["scheme_name"], s["category"], s["plan_type"])
        }
        for s in schemes_dict.values()
    ])

    df_stg_nav = pd.DataFrame(nav_rows, columns=["scheme_code", "nav_date", "nav"])
    df_stg_nav.drop_duplicates(subset=["scheme_code", "nav_date"], inplace=True)

    con = db.get_connection()
    try:
        con.register("stg_schemes", df_stg_schemes)
        con.register("stg_nav", df_stg_nav)

        # 1. Update existing schemes with latest metadata
        con.execute("""
            UPDATE schemes
            SET scheme_name = s.scheme_name,
                fund_house = CASE WHEN s.fund_house IS NOT NULL AND s.fund_house != '' THEN s.fund_house ELSE schemes.fund_house END,
                category = CASE WHEN s.category IS NOT NULL AND s.category != '' THEN s.category ELSE schemes.category END,
                plan_type = s.plan_type,
                option_type = s.option_type,
                isin = COALESCE(s.isin, schemes.isin),
                expense_ratio = CASE WHEN schemes.ter_status = 'official' THEN schemes.expense_ratio ELSE s.expense_ratio END,
                ter_status = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_status ELSE s.ter_status END,
                ter_source = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_source ELSE s.ter_source END,
                ter_source_url = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_source_url ELSE s.ter_source_url END,
                ter_as_of_date = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_as_of_date ELSE s.ter_as_of_date END,
                exit_load_pct = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_load_pct ELSE s.exit_load_pct END,
                exit_load_days = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_load_days ELSE s.exit_load_days END,
                exit_load_description = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_load_description ELSE s.exit_load_description END,
                exit_rule_json = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_json ELSE s.exit_rule_json END,
                exit_rule_status = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_status ELSE s.exit_rule_status END,
                exit_rule_source = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_source ELSE s.exit_rule_source END,
                exit_rule_source_url = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_source_url ELSE s.exit_rule_source_url END,
                exit_rule_as_of_date = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_as_of_date ELSE s.exit_rule_as_of_date END,
                lock_in_years = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.lock_in_years ELSE s.lock_in_years END
            FROM stg_schemes s
            WHERE schemes.scheme_code = s.scheme_code;
        """)

        # 2. Insert brand new schemes
        con.execute("""
            INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, isin, expense_ratio, ter_status, ter_source, ter_source_url, ter_as_of_date, exit_load_pct, exit_load_days, exit_load_description, exit_rule_json, exit_rule_status, exit_rule_source, exit_rule_source_url, exit_rule_as_of_date, lock_in_years)
            SELECT s.scheme_code, s.scheme_name, s.fund_house, s.category, s.plan_type, s.option_type, s.isin, s.expense_ratio, s.ter_status, s.ter_source, s.ter_source_url, s.ter_as_of_date, s.exit_load_pct, s.exit_load_days, s.exit_load_description, s.exit_rule_json, s.exit_rule_status, s.exit_rule_source, s.exit_rule_source_url, s.exit_rule_as_of_date, s.lock_in_years
            FROM stg_schemes s
            WHERE s.scheme_code NOT IN (SELECT scheme_code FROM schemes);
        """)

        # 3. Replace matching nav_history records (to avoid duplicate keys)
        con.execute("""
            DELETE FROM nav_history
            USING stg_nav
            WHERE nav_history.scheme_code = stg_nav.scheme_code
              AND nav_history.nav_date = stg_nav.nav_date;
        """)

        # 4. Insert latest daily NAV records
        con.execute("""
            INSERT INTO nav_history (scheme_code, nav_date, nav)
            SELECT scheme_code, nav_date, nav
            FROM stg_nav;
        """)

        try:
            con.unregister("stg_schemes")
        except Exception:
            pass
        try:
            con.unregister("stg_nav")
        except Exception:
            pass
        con.close()

        # Refresh materialized summary table
        db.refresh_summary_table()
        msg = f"Successfully synced {len(df_stg_nav):,} NAV records across {len(df_stg_schemes):,} schemes!"
        logger.info(msg)
        return True, msg
    except Exception as e:
        logger.error(f"Error updating DuckDB: {e}")
        try:
            con.unregister("stg_schemes")
        except Exception:
            pass
        try:
            con.unregister("stg_nav")
        except Exception:
            pass
        con.close()
        return False, str(e)


def sync_official_ter(_trigger: str = "manual", months: Optional[List[str]] = None) -> Tuple[bool, str]:
    """Fetches AMFI's official, dated TER-portal disclosure (SEBI Regulation 66) and promotes
    matched schemes' current TER to 'official' status — the automated, continuously-refreshed
    counterpart to the manual CSV importer, run on the same daily cadence as sync_daily_nav().

    Unlike sync_daily_nav(), this does NOT hold db.WRITE_LOCK for its whole duration: the
    AMFI TER API caps pageSize at 100, so covering one calendar month can take hundreds of
    sequential HTTP requests (multiple minutes by month-end) — holding WRITE_LOCK across all
    of that would block every other writer (a manual "Sync Now" click, a cost import) for the
    whole fetch. Only the two actual DB-mutating calls inside (db.upsert_ter_history,
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
    months = months or [AmfiTerClient.current_month_str()]
    client = AmfiTerClient()

    # 1. Fetch and validate every row across the requested months.
    parsed_rows: List[Dict[str, Any]] = []
    for month in months:
        month_count = 0
        for raw_row in client.fetch_month(month):
            parsed = AmfiTerClient.parse_row(raw_row)
            if parsed:
                parsed_rows.append(parsed)
                month_count += 1
        logger.info(f"AMFI TER portal: fetched {month_count:,} valid rows for {month}.")

    if not parsed_rows:
        return False, "AMFI TER portal returned no usable data (fetch failed, or empty response)."

    # 2. Group by the underlying scheme (NSDL code) and compute its normalized name — the
    # same normalization costs_data.py already applies to the bundled legacy CSV.
    by_nsdl: Dict[str, List[Dict[str, Any]]] = {}
    for row in parsed_rows:
        by_nsdl.setdefault(row["nsdl_scheme_code"], []).append(row)

    name_to_nsdl: Dict[str, set] = {}
    for nsdl_code, rows in by_nsdl.items():
        name = costs_data.normalize_scheme_name(rows[0]["scheme_name"])
        if name:
            name_to_nsdl.setdefault(name, set()).add(nsdl_code)

    # Only accept a name AMFI's own portal reports as exactly one distinct scheme — the same
    # "unambiguous match or skip" discipline costs_data._unique_legacy_ter() already applies.
    unambiguous_names = {name for name, codes in name_to_nsdl.items() if len(codes) == 1}
    ambiguous_count = len(name_to_nsdl) - len(unambiguous_names)
    if ambiguous_count:
        logger.info(f"AMFI TER sync: skipping {ambiguous_count} scheme name(s) that were ambiguous on AMFI's own portal.")

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
        nsdl_code = next(iter(name_to_nsdl[name]))
        date_rows = sorted(by_nsdl[nsdl_code], key=lambda r: r["ter_date"])

        for _, our_row in our_matches.iterrows():
            plan = (our_row["plan_type"] or "").strip().lower()
            if plan == "direct":
                prefix = "d_"
            elif plan == "regular":
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
            if existing is None or latest["ter_date"] > existing["ter_date"]:
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
        f"Matched {len(matched_scheme_codes):,} schemes across {len(months)} month(s) "
        f"({', '.join(months)}); stored {n_history:,} dated TER records; "
        f"refreshed current TER for {result['updated']:,} schemes."
    )
    logger.info(f"AMFI TER sync: {msg}")
    return True, msg


# --- HISTORICAL TER BACKFILL (deeper reach than the daily current-month sync) ---

TER_BACKFILL_STATE = {
    "is_running": False,
    "should_stop": False,
    "total_months": 0,
    "current_month_idx": 0,
    "current_month_str": "",
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
    stop_historical_backfill() below. Deliberately unconditional (no is_running guard) — see
    fetcher/amfi_sync.py's identical function for the full rationale (a module-reload-adjacent
    class of staleness doesn't apply here the same way FastAPI doesn't hot-reload per-request,
    but the unconditional/harmless-when-idle behavior is kept identical on both sides so the two
    codebases don't silently diverge in behavior during the migration)."""
    with _TER_BACKFILL_LOCK:
        TER_BACKFILL_STATE["should_stop"] = True

def _ter_backfill_worker(n_months: int):
    global TER_BACKFILL_STATE
    months = AmfiTerClient.recent_months(n_months)
    with _TER_BACKFILL_LOCK:
        TER_BACKFILL_STATE.update({
            "is_running": True, "should_stop": False, "total_months": len(months), "current_month_idx": 0,
            "last_error": None, "started_at": time.time(), "finished_at": None,
        })
    stopped_early = False
    for i, month in enumerate(months):
        with _TER_BACKFILL_LOCK:
            if TER_BACKFILL_STATE["should_stop"]:
                stopped_early = True
                break
            TER_BACKFILL_STATE["current_month_idx"] = i + 1
            TER_BACKFILL_STATE["current_month_str"] = month
        try:
            sync_official_ter(_trigger="backfill", months=[month])
        except Exception as e:
            logger.error(f"Error backfilling TER for {month}: {e}")
            with _TER_BACKFILL_LOCK:
                TER_BACKFILL_STATE["last_error"] = str(e)
    with _TER_BACKFILL_LOCK:
        TER_BACKFILL_STATE["is_running"] = False
        TER_BACKFILL_STATE["finished_at"] = time.time()
    if stopped_early:
        logger.info(f"TER historical backfill stopped by user request after {TER_BACKFILL_STATE['current_month_idx']} of {len(months)} month(s).")
    else:
        logger.info(f"TER historical backfill completed for {len(months)} month(s).")

def start_ter_backfill(n_months: int = 12) -> bool:
    """Launches a deeper historical TER backfill (default: the trailing 12 calendar months,
    AMFI's portal reaches back to FY2018-19) in a background worker thread — the daily sync
    only ever covers the current month, so this is how older months get filled in on demand."""
    with _TER_BACKFILL_LOCK:
        if TER_BACKFILL_STATE["is_running"]:
            return False
    t = threading.Thread(target=_ter_backfill_worker, args=(n_months,), daemon=True)
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

    df_stg_schemes = pd.DataFrame([
        {
            "scheme_code": s["scheme_code"],
            "scheme_name": s["scheme_name"],
            "fund_house": s["fund_house"],
            "category": s["category"],
            "plan_type": s["plan_type"],
            "option_type": s["option_type"],
            "isin": s["isin"],
            **costs_data.get_scheme_cost_specs(s["scheme_code"], s["scheme_name"], s["category"], s["plan_type"])
        }
        for s in schemes_dict.values()
    ])

    df_stg_nav = pd.DataFrame(nav_rows, columns=["scheme_code", "nav_date", "nav"])
    df_stg_nav.drop_duplicates(subset=["scheme_code", "nav_date"], inplace=True)

    con = db.get_connection()
    try:
        con.register("stg_schemes", df_stg_schemes)
        con.register("stg_nav", df_stg_nav)

        con.execute("""
            UPDATE schemes
            SET scheme_name = s.scheme_name,
                fund_house = CASE WHEN s.fund_house IS NOT NULL AND s.fund_house != '' THEN s.fund_house ELSE schemes.fund_house END,
                category = CASE WHEN s.category IS NOT NULL AND s.category != '' THEN s.category ELSE schemes.category END,
                plan_type = s.plan_type,
                option_type = s.option_type,
                isin = COALESCE(s.isin, schemes.isin),
                expense_ratio = CASE WHEN schemes.ter_status = 'official' THEN schemes.expense_ratio ELSE s.expense_ratio END,
                ter_status = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_status ELSE s.ter_status END,
                ter_source = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_source ELSE s.ter_source END,
                ter_source_url = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_source_url ELSE s.ter_source_url END,
                ter_as_of_date = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_as_of_date ELSE s.ter_as_of_date END,
                exit_load_pct = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_load_pct ELSE s.exit_load_pct END,
                exit_load_days = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_load_days ELSE s.exit_load_days END,
                exit_load_description = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_load_description ELSE s.exit_load_description END,
                exit_rule_json = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_json ELSE s.exit_rule_json END,
                exit_rule_status = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_status ELSE s.exit_rule_status END,
                exit_rule_source = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_source ELSE s.exit_rule_source END,
                exit_rule_source_url = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_source_url ELSE s.exit_rule_source_url END,
                exit_rule_as_of_date = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_as_of_date ELSE s.exit_rule_as_of_date END,
                lock_in_years = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.lock_in_years ELSE s.lock_in_years END
            FROM stg_schemes s
            WHERE schemes.scheme_code = s.scheme_code;
        """)

        con.execute("""
            INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, isin, expense_ratio, ter_status, ter_source, ter_source_url, ter_as_of_date, exit_load_pct, exit_load_days, exit_load_description, exit_rule_json, exit_rule_status, exit_rule_source, exit_rule_source_url, exit_rule_as_of_date, lock_in_years)
            SELECT s.scheme_code, s.scheme_name, s.fund_house, s.category, s.plan_type, s.option_type, s.isin, s.expense_ratio, s.ter_status, s.ter_source, s.ter_source_url, s.ter_as_of_date, s.exit_load_pct, s.exit_load_days, s.exit_load_description, s.exit_rule_json, s.exit_rule_status, s.exit_rule_source, s.exit_rule_source_url, s.exit_rule_as_of_date, s.lock_in_years
            FROM stg_schemes s
            WHERE s.scheme_code NOT IN (SELECT scheme_code FROM schemes);
        """)

        con.execute("""
            DELETE FROM nav_history
            USING stg_nav
            WHERE nav_history.scheme_code = stg_nav.scheme_code
              AND nav_history.nav_date = stg_nav.nav_date;
        """)

        con.execute("""
            INSERT INTO nav_history (scheme_code, nav_date, nav)
            SELECT scheme_code, nav_date, nav
            FROM stg_nav;
        """)

        try:
            con.unregister("stg_schemes")
        except Exception:
            pass
        try:
            con.unregister("stg_nav")
        except Exception:
            pass
        con.close()
        return len(df_stg_schemes), len(df_stg_nav)
    except Exception as e:
        logger.error(f"Error updating DuckDB for AMC {amc_name}: {e}")
        try:
            con.unregister("stg_schemes")
        except Exception:
            pass
        try:
            con.unregister("stg_nav")
        except Exception:
            pass
        con.close()
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
    app/core/config.py. This backend owns its SQLite file exclusively (no
    other process ever touches it -- see app/db/connection.py's module
    docstring), so the flag is no longer a cross-process safety guard; it's
    just an explicit off-switch for e.g. a read-only exploration session.
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
    with _BACKFILL_LOCK:
        if BACKFILL_STATE["is_running"]:
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

    schemes_dict = {}
    nav_rows = []
    for scheme_meta, nav_record in client.parse_amfi_nav_lines(raw_text):
        code = scheme_meta["scheme_code"]
        if code not in schemes_dict:
            schemes_dict[code] = scheme_meta
        nav_rows.append((nav_record["scheme_code"], nav_record["nav_date"], nav_record["nav"]))

    if not nav_rows:
        return 0, 0

    df_stg_schemes = pd.DataFrame([
        {
            "scheme_code": s["scheme_code"],
            "scheme_name": s["scheme_name"],
            "fund_house": s["fund_house"],
            "category": s["category"],
            "plan_type": s["plan_type"],
            "option_type": s["option_type"],
            "isin": s["isin"],
            **costs_data.get_scheme_cost_specs(s["scheme_code"], s["scheme_name"], s["category"], s["plan_type"])
        }
        for s in schemes_dict.values()
    ])

    df_stg_nav = pd.DataFrame(nav_rows, columns=["scheme_code", "nav_date", "nav"])
    df_stg_nav.drop_duplicates(subset=["scheme_code", "nav_date"], inplace=True)

    con = db.get_connection()
    try:
        con.register("stg_schemes", df_stg_schemes)
        con.register("stg_nav", df_stg_nav)

        con.execute("""
            UPDATE schemes
            SET scheme_name = s.scheme_name,
                fund_house = CASE WHEN s.fund_house IS NOT NULL AND s.fund_house != '' THEN s.fund_house ELSE schemes.fund_house END,
                category = CASE WHEN s.category IS NOT NULL AND s.category != '' THEN s.category ELSE schemes.category END,
                plan_type = s.plan_type,
                option_type = s.option_type,
                isin = COALESCE(s.isin, schemes.isin),
                expense_ratio = CASE WHEN schemes.ter_status = 'official' THEN schemes.expense_ratio ELSE s.expense_ratio END,
                ter_status = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_status ELSE s.ter_status END,
                ter_source = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_source ELSE s.ter_source END,
                ter_source_url = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_source_url ELSE s.ter_source_url END,
                ter_as_of_date = CASE WHEN schemes.ter_status = 'official' THEN schemes.ter_as_of_date ELSE s.ter_as_of_date END,
                exit_load_pct = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_load_pct ELSE s.exit_load_pct END,
                exit_load_days = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_load_days ELSE s.exit_load_days END,
                exit_load_description = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_load_description ELSE s.exit_load_description END,
                exit_rule_json = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_json ELSE s.exit_rule_json END,
                exit_rule_status = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_status ELSE s.exit_rule_status END,
                exit_rule_source = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_source ELSE s.exit_rule_source END,
                exit_rule_source_url = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_source_url ELSE s.exit_rule_source_url END,
                exit_rule_as_of_date = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.exit_rule_as_of_date ELSE s.exit_rule_as_of_date END,
                lock_in_years = CASE WHEN schemes.exit_rule_status = 'official' THEN schemes.lock_in_years ELSE s.lock_in_years END
            FROM stg_schemes s
            WHERE schemes.scheme_code = s.scheme_code;
        """)

        con.execute("""
            INSERT INTO schemes (scheme_code, scheme_name, fund_house, category, plan_type, option_type, isin, expense_ratio, ter_status, ter_source, ter_source_url, ter_as_of_date, exit_load_pct, exit_load_days, exit_load_description, exit_rule_json, exit_rule_status, exit_rule_source, exit_rule_source_url, exit_rule_as_of_date, lock_in_years)
            SELECT s.scheme_code, s.scheme_name, s.fund_house, s.category, s.plan_type, s.option_type, s.isin, s.expense_ratio, s.ter_status, s.ter_source, s.ter_source_url, s.ter_as_of_date, s.exit_load_pct, s.exit_load_days, s.exit_load_description, s.exit_rule_json, s.exit_rule_status, s.exit_rule_source, s.exit_rule_source_url, s.exit_rule_as_of_date, s.lock_in_years
            FROM stg_schemes s
            WHERE s.scheme_code NOT IN (SELECT scheme_code FROM schemes);
        """)

        con.execute("""
            DELETE FROM nav_history
            USING stg_nav
            WHERE nav_history.scheme_code = stg_nav.scheme_code
              AND nav_history.nav_date = stg_nav.nav_date;
        """)

        con.execute("""
            INSERT INTO nav_history (scheme_code, nav_date, nav)
            SELECT scheme_code, nav_date, nav
            FROM stg_nav;
        """)

        try:
            con.unregister("stg_schemes")
        except Exception:
            pass
        try:
            con.unregister("stg_nav")
        except Exception:
            pass
        con.close()
        return len(df_stg_schemes), len(df_stg_nav)
    except Exception as e:
        logger.error(f"Error merging chunk {from_date} to {to_date}: {e}")
        try:
            con.unregister("stg_schemes")
        except Exception:
            pass
        try:
            con.unregister("stg_nav")
        except Exception:
            pass
        con.close()
        return 0, 0

def _historical_backfill_worker(start_year: int, max_chunks: Optional[int]):
    global BACKFILL_STATE
    chunks = generate_backfill_chunks(start_year)
    if max_chunks:
        chunks = chunks[:max_chunks]

    with _BACKFILL_LOCK:
        BACKFILL_STATE["is_running"] = True
        BACKFILL_STATE["should_stop"] = False
        BACKFILL_STATE["total_chunks"] = len(chunks)
        BACKFILL_STATE["current_chunk_idx"] = 0
        BACKFILL_STATE["records_added"] = 0
        BACKFILL_STATE["schemes_added"] = 0
        BACKFILL_STATE["last_error"] = None
        BACKFILL_STATE["started_at"] = time.time()
        BACKFILL_STATE["finished_at"] = None

    logger.info(f"Starting historical backfill: {len(chunks)} chunks to process.")

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
            logger.info(f"Chunk {i+1}/{len(chunks)} ({frm} to {to_dt}): ingested {nav_cnt:,} NAVs.")
            if (i + 1) % 3 == 0 or (i + 1) == len(chunks):
                db.refresh_summary_table()
        except Exception as e:
            logger.error(f"Error processing chunk {frm} to {to_dt}: {e}")
            with _BACKFILL_LOCK:
                BACKFILL_STATE["last_error"] = str(e)

        time.sleep(1)

    db.refresh_summary_table()

    with _BACKFILL_LOCK:
        BACKFILL_STATE["is_running"] = False
        BACKFILL_STATE["finished_at"] = time.time()
    logger.info("Historical backfill completed successfully!")

def start_historical_backfill(start_year: int = 2020, max_chunks: Optional[int] = None) -> bool:
    """Launches the historical backfill in a background worker thread."""
    with _BACKFILL_LOCK:
        if BACKFILL_STATE["is_running"]:
            return False
    t = threading.Thread(target=_historical_backfill_worker, args=(start_year, max_chunks), daemon=True)
    t.start()
    return True
