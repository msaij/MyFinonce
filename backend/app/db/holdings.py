"""SQL for the holdings ledger (portfolios + holding_transactions).

Its own module rather than more of db/queries.py: that file is under a product
freeze until the maintainability refactor splits it by domain (see
docs/maintainability-refactor.md), and this is exactly the per-domain shape that
split is heading toward.

Writes here deliberately do NOT take connection.WRITE_LOCK. That lock orders the
AMFI ingest jobs, and a historical backfill holds it for the length of a chunk --
a user saving one transaction must never queue behind that. Ledger writes are
serialized by services/holdings_service's own lock instead.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from app.db.connection import fetchdf, get_connection

PORTFOLIO_COLUMNS = (
    "id", "name", "owner_label", "benchmark_scheme_code", "color", "notes",
    "archived", "created_at", "updated_at",
)
TXN_COLUMNS = (
    "id", "portfolio_id", "scheme_code", "txn_type", "trade_date", "amount", "units",
    "nav", "nav_source", "stamp_duty", "switch_group", "sip_mandate_id", "source",
    "notes", "created_at", "updated_at", "deleted_at",
)
TXN_WRITABLE = (
    "portfolio_id", "scheme_code", "txn_type", "trade_date", "amount", "units", "nav",
    "nav_source", "stamp_duty", "switch_group", "sip_mandate_id", "source", "notes",
)
PORTFOLIO_WRITABLE = ("name", "owner_label", "benchmark_scheme_code", "color", "notes", "archived")


def _rows(cur, columns: Sequence[str]) -> List[Dict[str, Any]]:
    return [dict(zip(columns, r)) for r in cur.fetchall()]


# --- Generic helpers ------------------------------------------------------------------
# Table and column names below always come from this module's constants, never
# from request input, so the f-string SQL is safe; values are always bound.

def _select(table: str, columns: Sequence[str], where: str = "", params: Sequence[Any] = (), order: str = "id",
            limit: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = f"SELECT {', '.join(columns)} FROM {table} {('WHERE ' + where) if where else ''} ORDER BY {order}"
    if limit is not None:
        sql, params = sql + " LIMIT %s", [*params, limit]
    with get_connection() as con:
        return _rows(con.execute(sql, list(params)), columns)


def _fetchone(sql: str, params: Sequence[Any], con=None):
    """One row from `sql`, on the caller's connection (inside its transaction) if given."""
    if con is not None:
        return con.execute(sql, list(params)).fetchone()
    with get_connection() as own:
        return own.execute(sql, list(params)).fetchone()


def _insert(table: str, writable: Sequence[str], columns: Sequence[str], fields: Dict[str, Any], con=None) -> Dict[str, Any]:
    """INSERT the writable subset of `fields`, RETURNING the full row."""
    cols = [c for c in writable if c in fields]
    row = _fetchone(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                    f"RETURNING {', '.join(columns)}", [fields[c] for c in cols], con)
    return dict(zip(columns, row))


def _update(table: str, writable: Sequence[str], columns: Sequence[str], row_id: int, fields: Dict[str, Any],
            extra_where: str = "", touch: bool = True, con=None) -> Optional[Dict[str, Any]]:
    """UPDATE the writable subset of `fields` on one row; None if no row matched."""
    cols = [c for c in writable if c in fields]
    if not cols:
        rows = _select(table, columns, f"id = %s {extra_where}", [row_id])
        return rows[0] if rows else None
    sets = ", ".join([f"{c} = %s" for c in cols] + (["updated_at = now()"] if touch else []))
    row = _fetchone(f"UPDATE {table} SET {sets} WHERE id = %s {extra_where} RETURNING {', '.join(columns)}",
                    [fields[c] for c in cols] + [row_id], con)
    return dict(zip(columns, row)) if row else None


# --- Portfolios ---------------------------------------------------------------

def list_portfolios(include_archived: bool = False) -> List[Dict[str, Any]]:
    return _select("portfolios", PORTFOLIO_COLUMNS, "" if include_archived else "NOT archived", order="archived, lower(name)")


def get_portfolio(portfolio_id: int) -> Optional[Dict[str, Any]]:
    rows = _select("portfolios", PORTFOLIO_COLUMNS, "id = %s", [portfolio_id])
    return rows[0] if rows else None


def create_portfolio(fields: Dict[str, Any]) -> Dict[str, Any]:
    return _insert("portfolios", PORTFOLIO_WRITABLE, PORTFOLIO_COLUMNS, fields)


def update_portfolio(portfolio_id: int, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _update("portfolios", PORTFOLIO_WRITABLE, PORTFOLIO_COLUMNS, portfolio_id, fields)


# --- Transactions -------------------------------------------------------------

def list_transactions(portfolio_ids: Sequence[int], include_deleted: bool = False) -> List[Dict[str, Any]]:
    if not portfolio_ids:
        return []
    where = "portfolio_id = ANY(%s)" + ("" if include_deleted else " AND deleted_at IS NULL")
    return _select("holding_transactions", TXN_COLUMNS, where, [list(portfolio_ids)], order="trade_date, id")


def get_transactions_by_ids(ids: Sequence[int]) -> List[Dict[str, Any]]:
    return _select("holding_transactions", TXN_COLUMNS, "id = ANY(%s)", [list(ids)]) if ids else []


def get_switch_group_ids(switch_group: Any) -> List[int]:
    return [r["id"] for r in _select("holding_transactions", ("id",), "switch_group = %s", [switch_group])]


def insert_transactions(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """All-or-nothing: a switch writes its OUT and IN legs, and SIP generation all
    its instalments, in one database transaction."""
    with get_connection() as con:
        con.begin()
        try:
            out = [_insert("holding_transactions", TXN_WRITABLE, TXN_COLUMNS, r, con=con) for r in rows]
            con.commit()
        except Exception:
            con.rollback()
            raise
    return out


def update_transaction(txn_id: int, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _update("holding_transactions", TXN_WRITABLE, TXN_COLUMNS, txn_id, fields, extra_where="AND deleted_at IS NULL")

def set_deleted(txn_ids: Sequence[int], deleted: bool) -> int:
    with get_connection() as con:
        cur = con.execute(
            "UPDATE holding_transactions SET deleted_at = CASE WHEN %s THEN now() ELSE NULL END, "
            "updated_at = now() WHERE id = ANY(%s)",
            (deleted, list(txn_ids)),
        )
        return cur.rowcount


# --- Market data the ledger needs ----------------------------------------------

def nav_on_or_before(scheme_code: int, on: datetime.date) -> Optional[Tuple[datetime.date, float]]:
    """The NAV a purchase on `on` would have been allotted at, or the last one
    before it (weekend / market holiday). Returns the date actually used so the
    UI can say so rather than silently shifting the trade."""
    with get_connection() as con:
        row = con.execute(
            "SELECT nav_date, nav FROM nav_history WHERE scheme_code = %s AND nav_date <= %s "
            "ORDER BY nav_date DESC LIMIT 1",
            (scheme_code, on),
        ).fetchone()
    return (row[0], float(row[1])) if row else None


def first_nav_date(scheme_code: int) -> Optional[datetime.date]:
    with get_connection() as con:
        row = con.execute(
            "SELECT min(nav_date) FROM nav_history WHERE scheme_code = %s", (scheme_code,)
        ).fetchone()
    return row[0] if row else None


def navs_on_dates(pairs: Iterable[Tuple[int, datetime.date]]) -> Dict[Tuple[int, datetime.date], float]:
    """NAV on-or-before each (scheme_code, date) in one round trip -- used to put
    user-typed statement NAVs on AMFI's split-adjusted scale."""
    pairs = sorted(set((int(c), d) for c, d in pairs))
    if not pairs:
        return {}
    codes = [c for c, _ in pairs]
    dates = [d for _, d in pairs]
    with get_connection() as con:
        rows = con.execute(
            """
            SELECT q.code, q.d, n.nav
            FROM unnest(%s::bigint[], %s::date[]) AS q(code, d)
            CROSS JOIN LATERAL (
                SELECT nav FROM nav_history
                WHERE scheme_code = q.code AND nav_date <= q.d
                ORDER BY nav_date DESC LIMIT 1
            ) n
            """,
            (codes, dates),
        ).fetchall()
    return {(int(r[0]), r[1]): float(r[2]) for r in rows}


SCHEME_META_COLUMNS = (
    "scheme_code", "scheme_name", "fund_house", "category", "broad_category", "plan_type",
    "option_type", "isin", "expense_ratio", "ter_status", "latest_date", "latest_nav",
    "is_active", "change_1d_pct", "return_1y_pct",
)


def scheme_meta(codes: Sequence[int]) -> Dict[int, Dict[str, Any]]:
    """Identity + latest NAV per scheme from summary_table, falling back to
    schemes + nav_history for any code the summary cache doesn't carry (it is a
    rebuildable cache and can briefly lag a brand-new scheme)."""
    codes = sorted(set(int(c) for c in codes))
    if not codes:
        return {}
    with get_connection() as con:
        try:
            df = fetchdf(con.execute(
                f"SELECT {', '.join(SCHEME_META_COLUMNS)} FROM summary_table WHERE scheme_code = ANY(%s)",
                (codes,),
            ))
        except Exception:
            df = pd.DataFrame(columns=SCHEME_META_COLUMNS)
        found = set(int(c) for c in df["scheme_code"]) if not df.empty else set()
        missing = [c for c in codes if c not in found]
        if missing:
            extra = fetchdf(con.execute(
                """
                SELECT s.scheme_code, s.scheme_name, s.fund_house, s.category,
                       NULL::text AS broad_category, s.plan_type, s.option_type, s.isin,
                       s.expense_ratio, s.ter_status, l.nav_date AS latest_date,
                       l.nav AS latest_nav, NULL::boolean AS is_active,
                       NULL::double precision AS change_1d_pct,
                       NULL::double precision AS return_1y_pct
                FROM schemes s
                LEFT JOIN LATERAL (
                    SELECT nav_date, nav FROM nav_history
                    WHERE scheme_code = s.scheme_code ORDER BY nav_date DESC LIMIT 1
                ) l ON TRUE
                WHERE s.scheme_code = ANY(%s)
                """,
                (missing,),
            ))
            df = pd.concat([df, extra], ignore_index=True) if not df.empty else extra
    out: Dict[int, Dict[str, Any]] = {}
    for rec in df.to_dict(orient="records"):
        out[int(rec["scheme_code"])] = rec
    return out


def scheme_exists(scheme_code: int) -> bool:
    with get_connection() as con:
        return con.execute("SELECT 1 FROM schemes WHERE scheme_code = %s", (scheme_code,)).fetchone() is not None


# --- Targets ---------------------------------------------------------------------

def get_targets(portfolio_id: int) -> Dict[str, float]:
    rows = _select("portfolio_targets", ("asset_class", "target_pct"), "portfolio_id = %s", [portfolio_id], order="asset_class")
    return {r["asset_class"]: float(r["target_pct"]) for r in rows}


def replace_targets(portfolio_id: int, targets: Dict[str, float]) -> Dict[str, float]:
    with get_connection() as con:
        con.begin()
        try:
            con.execute("DELETE FROM portfolio_targets WHERE portfolio_id = %s", (portfolio_id,))
            for asset_class, pct in targets.items():
                if pct > 0:
                    con.execute(
                        "INSERT INTO portfolio_targets (portfolio_id, asset_class, target_pct) VALUES (%s, %s, %s)",
                        (portfolio_id, asset_class, pct),
                    )
            con.commit()
        except Exception:
            con.rollback()
            raise
    return get_targets(portfolio_id)


# --- SIP mandates ------------------------------------------------------------------

SIP_COLUMNS = ("id", "portfolio_id", "scheme_code", "amount", "day_of_month", "start_date", "end_date",
               "step_up_pct", "active", "notes", "created_at", "updated_at")
SIP_WRITABLE = ("portfolio_id", "scheme_code", "amount", "day_of_month", "start_date", "end_date",
                "step_up_pct", "active", "notes")


def list_sip_mandates(portfolio_ids: Sequence[int]) -> List[Dict[str, Any]]:
    if not portfolio_ids:
        return []
    return _select("sip_mandates", SIP_COLUMNS, "portfolio_id = ANY(%s)", [list(portfolio_ids)], order="active DESC, id")


def get_sip_mandate(mandate_id: int) -> Optional[Dict[str, Any]]:
    rows = _select("sip_mandates", SIP_COLUMNS, "id = %s", [mandate_id])
    return rows[0] if rows else None


def create_sip_mandate(fields: Dict[str, Any]) -> Dict[str, Any]:
    return _insert("sip_mandates", SIP_WRITABLE, SIP_COLUMNS, fields)


def update_sip_mandate(mandate_id: int, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _update("sip_mandates", SIP_WRITABLE, SIP_COLUMNS, mandate_id, fields)


def mandate_installment_dates(mandate_id: int) -> List[datetime.date]:
    """Trade dates of this mandate's non-deleted generated instalments."""
    rows = _select("holding_transactions", ("trade_date",), "sip_mandate_id = %s AND deleted_at IS NULL", [mandate_id], order="trade_date")
    return [r["trade_date"] for r in rows]


def nav_on_or_after(scheme_code: int, on: datetime.date) -> Optional[Tuple[datetime.date, float]]:
    """A SIP debit that falls on a holiday is processed on the next business day."""
    with get_connection() as con:
        row = con.execute(
            "SELECT nav_date, nav FROM nav_history WHERE scheme_code = %s AND nav_date >= %s ORDER BY nav_date LIMIT 1",
            (scheme_code, on),
        ).fetchone()
    return (row[0], float(row[1])) if row else None


# --- Goals ---------------------------------------------------------------------------

GOAL_COLUMNS = ("id", "name", "target_amount", "target_date", "inflation_pct", "notes", "archived", "created_at", "updated_at")
GOAL_WRITABLE = ("name", "target_amount", "target_date", "inflation_pct", "notes", "archived")


def _goals(where: str = "", params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Goals with their linked portfolio ids attached as `portfolio_ids`."""
    rows = _select("goals", GOAL_COLUMNS, where, params, order="target_date, id")
    if rows:
        links = _select("goal_portfolios", ("goal_id", "portfolio_id"), "goal_id = ANY(%s)",
                        [[r["id"] for r in rows]], order="portfolio_id")
        for r in rows:
            r["portfolio_ids"] = [link["portfolio_id"] for link in links if link["goal_id"] == r["id"]]
    return rows


def list_goals(include_archived: bool = False) -> List[Dict[str, Any]]:
    return _goals("" if include_archived else "NOT archived")


def get_goal(goal_id: int) -> Optional[Dict[str, Any]]:
    rows = _goals("id = %s", [goal_id])
    return rows[0] if rows else None


def save_goal(goal_id: Optional[int], fields: Dict[str, Any], portfolio_ids: Optional[Sequence[int]]) -> Dict[str, Any]:
    """Create (goal_id None) or update a goal and replace its portfolio links, atomically."""
    with get_connection() as con:
        con.begin()
        try:
            if goal_id is None:
                goal_id = _insert("goals", GOAL_WRITABLE, GOAL_COLUMNS, fields, con=con)["id"]
            else:
                _update("goals", GOAL_WRITABLE, GOAL_COLUMNS, goal_id, fields, con=con)
            if portfolio_ids is not None:
                con.execute("DELETE FROM goal_portfolios WHERE goal_id = %s", (goal_id,))
                for pid in sorted(set(portfolio_ids)):
                    con.execute("INSERT INTO goal_portfolios (goal_id, portfolio_id) VALUES (%s, %s)", (goal_id, pid))
            con.commit()
        except Exception:
            con.rollback()
            raise
    return get_goal(goal_id)  # type: ignore[return-value]


# --- Alerts --------------------------------------------------------------------------

RULE_COLUMNS = ("id", "portfolio_id", "kind", "threshold", "active", "created_at")
ALERT_COLUMNS = ("id", "rule_id", "dedupe_key", "severity", "message", "fired_at", "ack_at")


def list_alert_rules() -> List[Dict[str, Any]]:
    return _select("holding_alert_rules", RULE_COLUMNS)


def create_alert_rule(fields: Dict[str, Any]) -> Dict[str, Any]:
    return _insert("holding_alert_rules", ("portfolio_id", "kind", "threshold", "active"), RULE_COLUMNS, fields)


def update_alert_rule(rule_id: int, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _update("holding_alert_rules", ("threshold", "active"), RULE_COLUMNS, rule_id, fields, touch=False)


def delete_alert_rule(rule_id: int) -> bool:
    """Rules (and their fired alerts) are configuration, not ledger data, so they
    genuinely delete -- unlike portfolios and transactions."""
    with get_connection() as con:
        return con.execute("DELETE FROM holding_alert_rules WHERE id = %s", (rule_id,)).rowcount > 0


def fire_alert(rule_id: int, dedupe_key: str, severity: str, message: str) -> bool:
    """True if newly fired; False if this rule already fired for this key."""
    with get_connection() as con:
        return con.execute(
            "INSERT INTO holding_alerts (rule_id, dedupe_key, severity, message) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (rule_id, dedupe_key) DO NOTHING",
            (rule_id, dedupe_key, severity, message),
        ).rowcount > 0


def list_alerts(unacked_only: bool = False, limit: int = 100) -> List[Dict[str, Any]]:
    return _select("holding_alerts", ALERT_COLUMNS, "ack_at IS NULL" if unacked_only else "", order="fired_at DESC, id DESC", limit=limit)


def unacked_alert_count() -> int:
    with get_connection() as con:
        return int(con.execute("SELECT count(*) FROM holding_alerts WHERE ack_at IS NULL").fetchone()[0])


def ack_alerts(ids: Optional[Sequence[int]]) -> int:
    with get_connection() as con:
        if ids is None:
            return con.execute("UPDATE holding_alerts SET ack_at = now() WHERE ack_at IS NULL").rowcount
        return con.execute("UPDATE holding_alerts SET ack_at = now() WHERE id = ANY(%s) AND ack_at IS NULL", (list(ids),)).rowcount


# --- Peer ranking (for insights) ----------------------------------------------------------

def category_percentiles(codes: Sequence[int], column: str) -> Dict[int, Dict[str, Any]]:
    """Each scheme's percentile rank (0 = worst, 100 = best) on `column` among
    active schemes in the same category AND plan type, from summary_table."""
    if column not in ("return_1y_pct", "return_3y_pct", "return_5y_pct"):
        raise ValueError(column)
    codes = sorted(set(int(c) for c in codes))
    if not codes:
        return {}
    with get_connection() as con:
        rows = con.execute(
            f"""
            WITH peers AS (
                SELECT scheme_code, category, plan_type, {column} AS r,
                       percent_rank() OVER (PARTITION BY category, plan_type ORDER BY {column}) AS pr,
                       count(*) OVER (PARTITION BY category, plan_type) AS n
                FROM summary_table
                WHERE is_active AND {column} IS NOT NULL
            )
            SELECT scheme_code, r, pr, n FROM peers WHERE scheme_code = ANY(%s)
            """,
            (codes,),
        ).fetchall()
    return {int(r[0]): {"value": float(r[1]), "percentile": float(r[2]) * 100.0, "peers": int(r[3])} for r in rows}


# --- Backup / restore ------------------------------------------------------------
#
# Every owner-authored table, in foreign-key order (restore inserts top-down).
# The second element is the serial id column whose sequence must be moved past
# restored ids, or None for link tables.

BACKUP_TABLES: List[Tuple[str, Optional[str]]] = [
    ("portfolios", "id"),
    ("holding_transactions", "id"),
    ("portfolio_targets", None),
    ("sip_mandates", "id"),
    ("goals", "id"),
    ("goal_portfolios", None),
    ("holding_alert_rules", "id"),
    ("holding_alerts", "id"),
]


def dump_all() -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    with get_connection() as con:
        for table, id_col in BACKUP_TABLES:
            cur = con.execute(f"SELECT * FROM {table} ORDER BY {id_col or '1, 2'}")
            cols = [d[0] for d in cur.description]
            out[table] = [dict(zip(cols, r)) for r in cur.fetchall()]
    return out


def ledger_is_empty() -> bool:
    with get_connection() as con:
        return all(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0 for t, _ in BACKUP_TABLES)


def restore_all(tables: Dict[str, Sequence[Dict[str, Any]]]) -> Dict[str, int]:
    """Loads a backup into an EMPTY ledger, preserving ids so switch pairs, SIP
    links and goal links survive, then moves each id sequence past them. Tables
    missing from an older backup are simply skipped."""
    counts: Dict[str, int] = {}
    with get_connection() as con:
        con.begin()
        try:
            for table, id_col in BACKUP_TABLES:
                rows = tables.get(table) or []
                for r in rows:
                    cols = list(r.keys())
                    con.execute(
                        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                        [r[c] for c in cols],
                    )
                counts[table] = len(rows)
                if id_col:
                    con.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', '{id_col}'), "
                        f"GREATEST((SELECT COALESCE(max({id_col}), 0) FROM {table}), 1), "
                        f"(SELECT count(*) > 0 FROM {table}))"
                    )
            con.commit()
        except Exception:
            con.rollback()
            raise
    return counts
