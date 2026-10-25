"""Row-level data audit: the database reporting on itself after a sync.

`amfi_sync.verify_sync()` answers "how much is there" ("13,905 schemes have no plan").
This answers "which rows contradict themselves": a scheme labelled Growth that holds a
dividend-reinvestment ISIN, a Direct plan charging more than its own Regular twin, a NAV
of zero, a history row for a scheme that does not exist. Each check names the rows, so a
finding can be looked up rather than taken on trust.

Every run is stored (one row per check per run in `data_audit_runs`), so "was this
already wrong last week?" has an answer, and the history is pruned so it cannot grow
without bound. The nightly chain calls `run_data_audit()` after its summary rebuild;
the Data Management page calls it on demand.

The checks only read. The one table this module writes is its own.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from app.db.connection import get_connection
from app.db.queries import format_scheme_display_name

logger = logging.getLogger(__name__)

SAMPLE_LIMIT = 20
MAX_RUNS_KEPT = 90
STALE_TER_DAYS = 45
RECENT_NAV_DAYS = 30

# SEBI (Mutual Funds) Regulations 1996, Reg 52(6)(c), as re-slabbed by SEBI circular
# SEBI/HO/IMD/DF2/CIR/P/2018/137: the highest slab any open-ended scheme may charge is
# 2.25% (equity-oriented, first Rs 500 crore of daily net assets). Reg 52(6A) then permits
# up to 0.30% more for inflows from beyond the top 30 cities and 0.05% more under clause
# (c), and GST on the management fee is charged over and above the cap. Applying 18% GST
# to the whole 2.60% -- not just the management-fee part it actually falls on -- gives
# (2.25 + 0.30 + 0.05) x 1.18 = 3.068, so 3.07% is a deliberately generous ceiling: no
# combination of the rate caps reaches past it. What does is a statutory levy (stamp duty
# and the like) passed through at actuals, which on a newly launched, still-tiny fund can
# annualise to several percent -- hence a warning that shows the components, not an error.
TER_CEILING_PCT = 3.07

# Placeholders AMFI publishes in its ISIN columns ("Redeemed", "NOTAPP") are not ISINs, so
# two schemes both saying "Redeemed" are not a duplicate identity. Indian mutual-fund ISINs
# are INF + 9 alphanumerics (12 characters in all).
_ISIN_RE = "^INF[A-Z0-9]{9}$"

# "Dividend" is part of genuine fund names -- "Dividend Yield Fund", "Dividend
# Opportunities Index Fund" -- and "Unclaimed Dividend/IDCW" names a liability plan, not
# an option. Those phrases are stripped before looking for an option word, so a Dividend
# Yield fund's Growth option is not flagged while "Sundaram Growth Fund - Dividend" still is.
# IDCW is also spelled out in full in older names ("Income Distribution cum Capital
# Withdrawal"), which is how 258 Principal-era codes came to be labelled "Other".
_OPTION_NAME_SQL = (
    r"regexp_replace(s.scheme_name, '(dividend\s+(yield|opportunit\w*))|(unclaimed\s+(dividend|idcw))', '', 'gi')"
    r" ~* '\m(idcw|dividend)|income\s+distribution\s+cum\s+capital\s+withdrawal'"
)

# "Direct" as a word is only ever the plan. "Regular" is not: "Regular Savings Fund",
# "Regular Dividend", "Regular Payout" are names and option frequencies. So Regular counts
# only as "Regular Plan" or as a bare dash-separated segment ("Fund - Regular").
_NAME_SAYS_DIRECT = r"s.scheme_name ~* '\mdirect\M'"
_NAME_SAYS_REGULAR = r"(s.scheme_name ~* '\mregular\s+plan\M' OR s.scheme_name ~* '(^|[-(])\s*regular\s*($|[-)])')"

# The key a Direct plan and its Regular twin share. Newer AMFI names are identical across
# plans; older ones embed the plan ("... - Direct Plan - Growth"), so the plan words are
# removed and punctuation collapsed. The same transform is applied to both sides, so it can
# only pair two schemes whose names differ by nothing but the plan.
_TWIN_KEY_SQL = (
    r"btrim(regexp_replace(regexp_replace(lower(s.scheme_name), '\m(direct|regular)(\s+plan)?\M', ' ', 'g'),"
    r" '[^a-z0-9]+', ' ', 'g'))"
)


# --- persistence --------------------------------------------------------------


def ensure_audit_table(con) -> None:
    """Idempotent, like bulk.ensure_checkpoint_table(): this module owns its table, so it
    creates it rather than depending on a schema migration having run first."""
    con.execute("CREATE SEQUENCE IF NOT EXISTS data_audit_run_seq")
    con.execute("""
        CREATE TABLE IF NOT EXISTS data_audit_runs (
            run_id         BIGINT NOT NULL,
            run_at         TIMESTAMPTZ NOT NULL,
            trigger        TEXT NOT NULL,
            run_elapsed_ms INTEGER,
            position       SMALLINT NOT NULL,
            check_name     TEXT NOT NULL,
            title          TEXT,
            severity       TEXT NOT NULL,
            status         TEXT NOT NULL,
            count          BIGINT,
            description    TEXT,
            samples        JSONB NOT NULL DEFAULT '[]'::jsonb,
            detail         JSONB,
            elapsed_ms     INTEGER,
            PRIMARY KEY (run_id, check_name)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_data_audit_runs_run ON data_audit_runs (run_id DESC)")


def _prune_history(con, keep: int) -> None:
    con.execute(
        """DELETE FROM data_audit_runs
           WHERE run_id < (SELECT min(run_id) FROM (
               SELECT DISTINCT run_id FROM data_audit_runs ORDER BY run_id DESC LIMIT %s) newest)""",
        (keep,),
    )


# --- helpers ------------------------------------------------------------------


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        # A NaN NAV is itself the finding; null would hide what was actually stored.
        return str(v)
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
        return float(v)
    return v


def _label(code: Any, name: Any, plan: Any, option: Any) -> str:
    if name is None or (isinstance(name, str) and not name.strip()):
        name = "(no scheme row)"
    return format_scheme_display_name(name, plan, option, code)


def _sample(code, name, plan, option, **values) -> Dict[str, Any]:
    return {
        "scheme_code": int(code) if code is not None else None,
        "label": _label(code, name, plan, option),
        "values": {k: _jsonable(v) for k, v in values.items()},
    }


def _table_exists(con, name: str) -> bool:
    return con.execute("SELECT to_regclass(%s) IS NOT NULL", (name,)).fetchone()[0]


def _scheme_columns(con) -> set:
    rows = con.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = current_schema() AND table_name = 'schemes'"""
    ).fetchall()
    return {r[0] for r in rows}


class _Ctx:
    """What every check needs to know about the database's shape, read once per run."""

    def __init__(self, con):
        # summary_table is a rebuildable cache and can be briefly absent (a fresh database
        # before its first build). "Active" is then the empty set rather than a crash.
        # NOT MATERIALIZED: a CTE referenced twice is otherwise materialized without an
        # index and scanned once per outer row -- that alone made the ISIN check take 1.5s.
        if _table_exists(con, "summary_table"):
            self.active_cte = "active AS NOT MATERIALIZED (SELECT scheme_code FROM summary_table WHERE is_active)"
        else:
            self.active_cte = "active AS NOT MATERIALIZED (SELECT NULL::bigint AS scheme_code WHERE false)"
        self.scheme_columns = _scheme_columns(con)


def _result(count: Optional[int], samples: List[dict], status: Optional[str] = None,
            detail: Optional[dict] = None) -> Dict[str, Any]:
    if status is None:
        status = "pass" if not count else "flagged"
    return {"count": count, "samples": samples, "status": status, "detail": detail}


# --- checks -------------------------------------------------------------------
# Each takes (con, ctx) and returns _result(...). Identity checks (1, 2, 3, 7) run over
# every scheme, not only active ones: a wrong label on a wound-up fund still mislabels
# the owner's past transactions in it. Samples list active schemes first.


def _check_option_vs_isin(con, ctx) -> Dict[str, Any]:
    rows = con.execute(f"""
        WITH {ctx.active_cte}
        SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type,
               a.scheme_code IS NOT NULL AS active, s.isin, s.isin_reinvestment,
               count(*) OVER () AS total
        FROM schemes s LEFT JOIN active a ON a.scheme_code = s.scheme_code
        WHERE s.isin_reinvestment ~ %s AND s.option_type IS DISTINCT FROM 'IDCW'
        ORDER BY active DESC, s.scheme_code
        LIMIT %s""", (_ISIN_RE, SAMPLE_LIMIT)).fetchall()
    return _result(rows[0][-1] if rows else 0, [
        _sample(c, n, p, o, option_type=o, isin=i, isin_reinvestment=ir, active=a)
        for c, n, p, o, a, i, ir, _ in rows])


def _check_option_vs_name(con, ctx) -> Dict[str, Any]:
    # Only this direction: "Growth" is part of genuine fund names ("Nippon India Growth
    # Fund"), so a name saying Growth proves nothing about the option. "Other" is included
    # because it is the parser's "could not tell", and the name here does tell.
    rows = con.execute(f"""
        WITH {ctx.active_cte}
        SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type,
               a.scheme_code IS NOT NULL AS active,
               count(*) FILTER (WHERE s.option_type = 'Growth') OVER () AS growth,
               count(*) OVER () AS total
        FROM schemes s LEFT JOIN active a ON a.scheme_code = s.scheme_code
        WHERE s.option_type IN ('Growth', 'Other') AND {_OPTION_NAME_SQL}
        ORDER BY s.option_type = 'Growth' DESC, active DESC, s.scheme_code
        LIMIT %s""", (SAMPLE_LIMIT,)).fetchall()
    total = rows[0][-1] if rows else 0
    growth = rows[0][-2] if rows else 0
    return _result(total, [
        _sample(c, n, p, o, scheme_name=n, option_type=o, active=a) for c, n, p, o, a, _, _ in rows],
        detail={"labelled_growth": int(growth), "labelled_other": int(total - growth)})


def _check_plan_vs_name(con, ctx) -> Dict[str, Any]:
    rows = con.execute(f"""
        WITH {ctx.active_cte}
        SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type,
               a.scheme_code IS NOT NULL AS active, count(*) OVER () AS total
        FROM schemes s LEFT JOIN active a ON a.scheme_code = s.scheme_code
        WHERE ({_NAME_SAYS_DIRECT} AND NOT {_NAME_SAYS_REGULAR} AND s.plan_type = 'Regular')
           OR ({_NAME_SAYS_REGULAR} AND NOT {_NAME_SAYS_DIRECT} AND s.plan_type = 'Direct')
        ORDER BY active DESC, s.scheme_code
        LIMIT %s""", (SAMPLE_LIMIT,)).fetchall()
    return _result(rows[0][-1] if rows else 0, [
        _sample(c, n, p, o, scheme_name=n, plan_type=p, active=a) for c, n, p, o, a, _ in rows])


def _check_direct_ter_above_regular(con, ctx) -> Dict[str, Any]:
    # One row per offending Direct plan, paired with its cheapest Regular twin (the
    # starkest contradiction), so a fund with several IDCW variants counts once per plan.
    rows = con.execute(f"""
        WITH {ctx.active_cte},
        x AS (
            SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.expense_ratio,
                   {_TWIN_KEY_SQL} AS twin_key
            FROM schemes s JOIN active a ON a.scheme_code = s.scheme_code
            WHERE s.ter_status = 'official' AND s.expense_ratio IS NOT NULL
              AND s.plan_type IN ('Direct', 'Regular') AND COALESCE(s.option_type, '') <> ''
        ),
        pairs AS (
            SELECT DISTINCT ON (d.scheme_code)
                   d.scheme_code, d.scheme_name, d.plan_type, d.option_type, d.expense_ratio AS direct_ter,
                   r.scheme_code AS r_code, r.scheme_name AS r_name, r.plan_type AS r_plan,
                   r.option_type AS r_option, r.expense_ratio AS regular_ter
            FROM x d JOIN x r
              ON r.twin_key = d.twin_key AND r.option_type = d.option_type
             AND d.plan_type = 'Direct' AND r.plan_type = 'Regular'
            WHERE d.expense_ratio > r.expense_ratio + 0.005
            ORDER BY d.scheme_code, r.expense_ratio
        )
        SELECT *, count(*) OVER () AS total FROM pairs
        ORDER BY direct_ter - regular_ter DESC, scheme_code
        LIMIT %s""", (SAMPLE_LIMIT,)).fetchall()
    return _result(rows[0][-1] if rows else 0, [
        _sample(c, n, p, o, direct_ter_pct=dt, regular_twin=_label(rc, rn, rp, ro), regular_ter_pct=rt)
        for c, n, p, o, dt, rc, rn, rp, ro, rt, _ in rows])


def _check_ter_bounds(con, ctx) -> Dict[str, Any]:
    rows = con.execute(f"""
        WITH {ctx.active_cte}
        SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type,
               a.scheme_code IS NOT NULL AS active, s.expense_ratio, s.ter_base_expense_ratio,
               s.ter_brokerage_cost_pct, s.ter_transaction_cost_pct, s.ter_statutory_levies_pct,
               s.ter_as_of_date, count(*) OVER () AS total
        FROM schemes s LEFT JOIN active a ON a.scheme_code = s.scheme_code
        WHERE s.ter_status = 'official'
          AND (s.expense_ratio < 0 OR s.expense_ratio > %s
               OR LEAST(s.ter_base_expense_ratio, s.ter_brokerage_cost_pct,
                        s.ter_transaction_cost_pct, s.ter_statutory_levies_pct) < 0)
        ORDER BY active DESC, abs(s.expense_ratio) DESC NULLS LAST, s.scheme_code
        LIMIT %s""", (TER_CEILING_PCT, SAMPLE_LIMIT)).fetchall()
    return _result(rows[0][-1] if rows else 0, [
        _sample(c, n, p, o, ter_pct=t, base_pct=b, brokerage_pct=br, transaction_pct=tr,
                statutory_levies_pct=sl, as_of=d, active=a)
        for c, n, p, o, a, t, b, br, tr, sl, d, _ in rows],
        detail={"ceiling_pct": TER_CEILING_PCT})


def _check_ter_components(con, ctx) -> Dict[str, Any]:
    # AMFI discloses the TER and its four parts separately; the parts must add up to the
    # whole. A mismatch means a column landed in the wrong field on ingest.
    rows = con.execute(f"""
        WITH {ctx.active_cte}
        SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type,
               a.scheme_code IS NOT NULL AS active, s.expense_ratio,
               s.ter_base_expense_ratio + s.ter_brokerage_cost_pct + s.ter_transaction_cost_pct
                 + s.ter_statutory_levies_pct AS parts_sum,
               count(*) OVER () AS total
        FROM schemes s LEFT JOIN active a ON a.scheme_code = s.scheme_code
        WHERE s.ter_status = 'official' AND s.expense_ratio IS NOT NULL
          AND s.ter_base_expense_ratio IS NOT NULL AND s.ter_brokerage_cost_pct IS NOT NULL
          AND s.ter_transaction_cost_pct IS NOT NULL AND s.ter_statutory_levies_pct IS NOT NULL
          AND abs(s.expense_ratio - (s.ter_base_expense_ratio + s.ter_brokerage_cost_pct
                  + s.ter_transaction_cost_pct + s.ter_statutory_levies_pct)) > 0.011
        ORDER BY active DESC, s.scheme_code
        LIMIT %s""", (SAMPLE_LIMIT,)).fetchall()
    return _result(rows[0][-1] if rows else 0, [
        _sample(c, n, p, o, ter_pct=t, sum_of_parts_pct=round(ps, 4) if ps is not None else None, active=a)
        for c, n, p, o, a, t, ps, _ in rows])


def _check_stale_ter(con, ctx) -> Dict[str, Any]:
    rows = con.execute(f"""
        WITH {ctx.active_cte}
        SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type, s.expense_ratio,
               s.ter_as_of_date, CURRENT_DATE - s.ter_as_of_date AS age_days,
               count(*) OVER () AS total
        FROM schemes s JOIN active a ON a.scheme_code = s.scheme_code
        WHERE s.ter_status = 'official' AND s.ter_as_of_date < CURRENT_DATE - %s
        ORDER BY s.ter_as_of_date, s.scheme_code
        LIMIT %s""", (STALE_TER_DAYS, SAMPLE_LIMIT)).fetchall()
    return _result(rows[0][-1] if rows else 0, [
        _sample(c, n, p, o, ter_pct=t, as_of=d, age_days=age) for c, n, p, o, t, d, age, _ in rows],
        detail={"max_age_days": STALE_TER_DAYS})


def _isin_conflicts(con, ctx, both_active: bool) -> Dict[str, Any]:
    """Pairs of scheme codes claiming the same ISIN, split by whether both are active.

    Measured on the live data (2026-09-25): 819 such pairs, and in not one are both codes
    active. They are AMFI re-coding a scheme after a merger or rename -- Principal's funds
    under Sundaram, say -- with the old, dead code still holding the ISIN its successor now
    publishes. That is split history, not a corrupt row, and filing it as an error would
    make "errors must be zero" permanently false and teach the reader to ignore it. Two
    *active* codes claiming one ISIN is the corruption: two live rows for one security."""
    active_filter = ("(aa.scheme_code IS NOT NULL AND ab.scheme_code IS NOT NULL)" if both_active
                     else "NOT (aa.scheme_code IS NOT NULL AND ab.scheme_code IS NOT NULL)")
    rows = con.execute(f"""
        WITH {ctx.active_cte},
        conflicts AS (
            SELECT a.scheme_code AS code_a, b.scheme_code AS code_b, a.isin AS shared,
                   'same ISIN' AS kind
            FROM schemes a JOIN schemes b ON b.isin = a.isin AND b.scheme_code > a.scheme_code
            WHERE a.isin ~ %(re)s
            UNION ALL
            SELECT a.scheme_code, b.scheme_code, a.isin, 'ISIN is the other''s reinvestment ISIN'
            FROM schemes a JOIN schemes b ON b.isin_reinvestment = a.isin AND b.scheme_code <> a.scheme_code
            WHERE a.isin ~ %(re)s
            UNION ALL
            SELECT a.scheme_code, b.scheme_code, a.isin_reinvestment, 'same reinvestment ISIN'
            FROM schemes a JOIN schemes b
              ON b.isin_reinvestment = a.isin_reinvestment AND b.scheme_code > a.scheme_code
            WHERE a.isin_reinvestment ~ %(re)s
        ),
        labelled AS (
            SELECT c.*, sa.scheme_name AS na, sa.plan_type AS pa, sa.option_type AS oa,
                   sb.scheme_name AS nb, sb.plan_type AS pb, sb.option_type AS ob,
                   aa.scheme_code IS NOT NULL AS a_active, ab.scheme_code IS NOT NULL AS b_active,
                   -- One security has one plan and one option; codes sharing an ISIN but
                   -- disagreeing on either means at least one of the labels is wrong.
                   (sa.plan_type IS DISTINCT FROM sb.plan_type
                    OR sa.option_type IS DISTINCT FROM sb.option_type) AS labels_disagree
            FROM conflicts c
            JOIN schemes sa ON sa.scheme_code = c.code_a
            JOIN schemes sb ON sb.scheme_code = c.code_b
            LEFT JOIN active aa ON aa.scheme_code = c.code_a
            LEFT JOIN active ab ON ab.scheme_code = c.code_b
            WHERE {active_filter}
        )
        SELECT code_a, na, pa, oa, code_b, nb, pb, ob, shared, kind, a_active, b_active, labels_disagree,
               count(*) FILTER (WHERE labels_disagree) OVER () AS disagreeing,
               count(*) OVER () AS total
        FROM labelled
        ORDER BY labels_disagree DESC, (a_active OR b_active) DESC, shared, code_a
        LIMIT %(lim)s""", {"re": _ISIN_RE, "lim": SAMPLE_LIMIT}).fetchall()
    return _result(rows[0][-1] if rows else 0, [
        _sample(ca, na, pa, oa, isin=sh, conflict=kind, other_scheme=_label(cb, nb, pb, ob),
                this_active=a_act, other_active=b_act, labels_disagree=dis)
        for ca, na, pa, oa, cb, nb, pb, ob, sh, kind, a_act, b_act, dis, _, _ in rows],
        detail={"labels_disagree": int(rows[0][-2]) if rows else 0})


def _check_duplicate_isin(con, ctx) -> Dict[str, Any]:
    return _isin_conflicts(con, ctx, both_active=True)


def _check_isin_recoded(con, ctx) -> Dict[str, Any]:
    return _isin_conflicts(con, ctx, both_active=False)


def _check_malformed_isin(con, ctx) -> Dict[str, Any]:
    rows = con.execute(f"""
        WITH {ctx.active_cte}
        SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type,
               a.scheme_code IS NOT NULL AS active, s.isin, s.isin_reinvestment,
               count(*) OVER () AS total
        FROM schemes s LEFT JOIN active a ON a.scheme_code = s.scheme_code
        WHERE (COALESCE(btrim(s.isin), '') <> '' AND s.isin !~ %(re)s)
           OR (COALESCE(btrim(s.isin_reinvestment), '') <> '' AND s.isin_reinvestment !~ %(re)s)
        ORDER BY active DESC, s.scheme_code
        LIMIT %(lim)s""", {"re": _ISIN_RE, "lim": SAMPLE_LIMIT}).fetchall()
    return _result(rows[0][-1] if rows else 0, [
        _sample(c, n, p, o, isin=i, isin_reinvestment=ir, active=a) for c, n, p, o, a, i, ir, _ in rows])


def _orphans_in(con, table: str, date_col: str) -> Tuple[int, List[dict]]:
    """Scheme codes present in `table` but not in schemes.

    `SELECT DISTINCT scheme_code FROM nav_history` reads all ~27M rows (3.2s measured on
    the live database). The recursive CTE is a loose index scan: it asks the primary key
    for "the next code after this one" once per distinct code, ~25k index probes instead
    of 27M rows -- 0.23s on the same data. The per-orphan row counts are only computed
    for the sampled codes, so a wholesale orphaning cannot turn this into a full scan."""
    rows = con.execute(f"""
        WITH RECURSIVE codes AS (
            (SELECT scheme_code FROM {table} ORDER BY scheme_code LIMIT 1)
            UNION ALL
            SELECT (SELECT t.scheme_code FROM {table} t WHERE t.scheme_code > c.scheme_code
                    ORDER BY t.scheme_code LIMIT 1)
            FROM codes c WHERE c.scheme_code IS NOT NULL
        ),
        orphans AS (
            SELECT c.scheme_code FROM codes c
            WHERE c.scheme_code IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM schemes s WHERE s.scheme_code = c.scheme_code)
        ),
        page AS (
            SELECT scheme_code, count(*) OVER () AS total FROM orphans ORDER BY scheme_code LIMIT %s
        )
        SELECT p.scheme_code, p.total, st.n, st.first_date, st.last_date
        FROM page p CROSS JOIN LATERAL (
            SELECT count(*) AS n, min({date_col}) AS first_date, max({date_col}) AS last_date
            FROM {table} WHERE scheme_code = p.scheme_code
        ) st
        ORDER BY p.scheme_code""", (SAMPLE_LIMIT,)).fetchall()
    total = rows[0][1] if rows else 0
    return total, [_sample(c, None, None, None, table=table, rows=n, first_date=f, last_date=l)
                   for c, _, n, f, l in rows]


def _check_orphans(con, ctx) -> Dict[str, Any]:
    nav_total, nav_samples = _orphans_in(con, "nav_history", "nav_date")
    ter_total, ter_samples = _orphans_in(con, "ter_history", "ter_date")
    return _result(nav_total + ter_total, (nav_samples + ter_samples)[:SAMPLE_LIMIT],
                   detail={"nav_history_codes": nav_total, "ter_history_codes": ter_total})


def _check_bad_recent_nav(con, ctx) -> Dict[str, Any]:
    # Anchored on the newest NAV held, not today: a database that has not synced for a week
    # should still audit its own last 30 days rather than an empty window. NaN needs its own
    # test because Postgres orders NaN above every number, so `nav <= 0` never catches it.
    #
    # A segregated portfolio holds paper written off after a credit event, and AMFI
    # publishes its NAV as exactly 0 every day (100 such schemes on 2026-09-25). That is
    # the true price, not a corrupt row, so an exact zero there is counted in `detail`
    # rather than as an error. Anything else on one -- negative, NaN, empty -- still counts.
    recent = f"""
        WITH {ctx.active_cte},
        recent AS MATERIALIZED (
            SELECT n.scheme_code, n.nav_date, n.nav, s.scheme_name, s.plan_type, s.option_type,
                   COALESCE(n.nav = 0 AND s.scheme_name ~* 'segregat', false) AS written_off
            FROM nav_history n
            JOIN active a ON a.scheme_code = n.scheme_code
            LEFT JOIN schemes s ON s.scheme_code = n.scheme_code
            WHERE n.nav_date >= (SELECT max(nav_date) FROM nav_history) - %s
              AND (n.nav IS NULL OR n.nav <= 0 OR n.nav = 'NaN'::float8)
        )"""
    bad_rows, bad_schemes, written_off = con.execute(recent + """
        SELECT count(*) FILTER (WHERE NOT written_off),
               count(DISTINCT scheme_code) FILTER (WHERE NOT written_off),
               count(DISTINCT scheme_code) FILTER (WHERE written_off)
        FROM recent""", (RECENT_NAV_DAYS,)).fetchone()
    rows = con.execute(recent + """
        SELECT scheme_code, scheme_name, plan_type, option_type, nav_date, nav
        FROM recent WHERE NOT written_off
        ORDER BY nav_date DESC, scheme_code
        LIMIT %s""", (RECENT_NAV_DAYS, SAMPLE_LIMIT)).fetchall()
    return _result(int(bad_rows), [_sample(c, n, p, o, nav_date=d, nav=v) for c, n, p, o, d, v in rows],
                   detail={"window_days": RECENT_NAV_DAYS, "schemes": int(bad_schemes),
                           "segregated_zero_nav_schemes_excluded": int(written_off)})


def _check_unlabelled_active(con, ctx) -> Dict[str, Any]:
    rows = con.execute(f"""
        WITH {ctx.active_cte}
        SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type,
               COALESCE(btrim(s.plan_type), '') = '' AS no_plan,
               COALESCE(btrim(s.option_type), '') = '' AS no_option,
               count(*) OVER () AS total
        FROM schemes s JOIN active a ON a.scheme_code = s.scheme_code
        WHERE COALESCE(btrim(s.plan_type), '') = '' OR COALESCE(btrim(s.option_type), '') = ''
        ORDER BY s.fund_house, s.scheme_name, s.scheme_code
        LIMIT %s""", (SAMPLE_LIMIT,)).fetchall()
    return _result(rows[0][-1] if rows else 0, [
        _sample(c, n, p, o, missing=" and ".join(m for m, flag in (("plan", np_), ("option", no)) if flag))
        for c, n, p, o, np_, no, _ in rows])


def _check_nav_restatement(con, ctx) -> Dict[str, Any]:
    row = None
    if _table_exists(con, "sync_meta"):
        row = con.execute("SELECT value FROM sync_meta WHERE key = 'nav_restatement_last'").fetchone()
    if not row or not row[0]:
        return _result(None, [], status="not_run",
                       detail={"reason": "The NAV restatement step has not recorded a run yet."})
    try:
        payload = json.loads(row[0])
        suspects = payload.get("suspect") or []
        count = int(payload.get("suspect_count", len(suspects)))
    except (ValueError, TypeError, AttributeError) as e:
        return _result(None, [], status="error",
                       detail={"reason": f"nav_restatement_last is not the expected JSON: {e}"})

    shown = [s for s in suspects if isinstance(s, dict)][:SAMPLE_LIMIT]
    codes = []
    for s in shown:
        try:
            codes.append(int(s.get("scheme_code")))
        except (TypeError, ValueError):
            pass
    names = {}
    if codes:
        names = {r[0]: r[1:] for r in con.execute(
            "SELECT scheme_code, scheme_name, plan_type, option_type FROM schemes WHERE scheme_code = ANY(%s)",
            (codes,)).fetchall()}
    samples = []
    for s in shown:
        try:
            code = int(s.get("scheme_code"))
        except (TypeError, ValueError):
            code = None
        n, p, o = names.get(code, (None, None, None))
        samples.append(_sample(code, n, p, o, nav_date=s.get("nav_date"), stored=s.get("stored"),
                               amfi=s.get("amfi"), ratio=s.get("ratio")))
    detail = {k: payload.get(k) for k in ("ran_at", "window_days", "checked", "restated", "gap_filled", "split_scaled")}
    return _result(count, samples, detail=detail)


def _check_legacy_provenance(con, ctx) -> Dict[str, Any]:
    present = [c for c in ("plan_source", "option_source") if c in ctx.scheme_columns]
    if not present:
        return _result(None, [], status="not_run",
                       detail={"reason": "schemes has no plan_source/option_source columns yet."})
    # A NULL source on a NULL value is an absent label (the unlabelled check covers it),
    # not a value of unknown origin, so only populated values are counted.
    conds = []
    if "plan_source" in present:
        conds.append("(COALESCE(btrim(s.plan_type), '') <> '' AND s.plan_source IS NULL)")
    if "option_source" in present:
        conds.append("(COALESCE(btrim(s.option_type), '') <> '' AND s.option_source IS NULL)")
    plan_src = "s.plan_source" if "plan_source" in present else "NULL::text"
    option_src = "s.option_source" if "option_source" in present else "NULL::text"
    rows = con.execute(f"""
        WITH {ctx.active_cte}
        SELECT s.scheme_code, s.scheme_name, s.plan_type, s.option_type,
               {plan_src}, {option_src}, count(*) OVER () AS total
        FROM schemes s JOIN active a ON a.scheme_code = s.scheme_code
        WHERE {' OR '.join(conds)}
        ORDER BY s.scheme_code
        LIMIT %s""", (SAMPLE_LIMIT,)).fetchall()
    return _result(rows[0][-1] if rows else 0, [
        _sample(c, n, p, o, plan_type=p, plan_source=ps, option_type=o, option_source=os_)
        for c, n, p, o, ps, os_, _ in rows], detail={"columns": present})


# (name, title, severity, description, fn). Order is the order the page shows them.
CHECKS: Sequence[Tuple[str, str, str, str, Callable]] = (
    ("option_vs_isin", "Option contradicts ISIN evidence", "error",
     "A registrar issues a dividend-reinvestment ISIN only to a scheme with an IDCW option, so a "
     "scheme holding one but labelled otherwise has the wrong option, and every return and tax "
     "view that depends on Growth vs IDCW is wrong for it.", _check_option_vs_isin),
    ("plan_vs_name", "Plan contradicts scheme name", "error",
     "The scheme's own name says Direct (or Regular Plan) while its plan field says the opposite; "
     "the plan decides which TER is charged and which twin it is compared with, so one of the two "
     "is wrong.", _check_plan_vs_name),
    ("direct_ter_above_regular", "Direct TER above its Regular twin", "error",
     "A Direct plan leaves out the distributor's commission, so its TER can never legitimately "
     "exceed the Regular plan of the same fund and option; when it does, the two plans' labels "
     "or TERs have been swapped.", _check_direct_ter_above_regular),
    ("duplicate_isin", "Duplicate ISIN between active schemes", "error",
     "An ISIN identifies exactly one security, so two active scheme codes sharing one (or one's "
     "ISIN being the other's reinvestment ISIN) means one of them carries another fund's identity.",
     _check_duplicate_isin),
    ("orphan_history", "History rows with no scheme", "error",
     "NAV or TER history for a scheme code that is not in the schemes table; nothing enforces "
     "this link, and such rows are invisible to every page while still counted in totals.",
     _check_orphans),
    ("bad_recent_nav", "Missing or non-positive NAV (last 30 days)", "error",
     "A NAV is a price per unit and must be positive; a zero, negative, NaN or empty NAV on an "
     "active scheme turns every return that spans it into a -100% or division-by-zero result. "
     "(A segregated portfolio of written-off paper legitimately publishes exactly 0 and is not counted.)",
     _check_bad_recent_nav),
    ("option_vs_name", "Option contradicts scheme name", "warning",
     "The name says IDCW or Dividend (or spells out Income Distribution cum Capital Withdrawal) while "
     "the option says Growth, or the undecided \"Other\". Names are weaker evidence than ISINs "
     "(\"Dividend Yield\" funds and unclaimed-dividend plans are excluded), so each needs a look "
     "rather than an automatic fix.", _check_option_vs_name),
    ("ter_out_of_bounds", "TER outside SEBI's bounds", "warning",
     f"An official TER below zero, or above {TER_CEILING_PCT}%: SEBI's highest slab (2.25%) plus "
     "the permitted 0.30% and 0.05% additions, with 18% GST on the lot. Above that, only a "
     "statutory levy passed through at actuals (typical of a days-old fund) or a bad row can explain it.",
     _check_ter_bounds),
    ("ter_components_mismatch", "TER parts do not add up", "warning",
     "AMFI publishes the TER together with its base, brokerage, transaction and statutory-levy "
     "parts; when the parts do not sum to the total, a column landed in the wrong field.",
     _check_ter_components),
    ("stale_official_ter", f"Official TER older than {STALE_TER_DAYS} days", "warning",
     "AMFI republishes every scheme's TER each month, so an active scheme whose official TER is "
     f"over {STALE_TER_DAYS} days old has stopped being matched, and the cost shown for it may no "
     "longer be what it charges.", _check_stale_ter),
    ("malformed_isin", "Malformed ISIN", "warning",
     "An ISIN field holding something other than a 12-character INF... code (AMFI sometimes "
     "publishes placeholders like \"Redeemed\"); the scheme cannot be matched by ISIN.",
     _check_malformed_isin),
    ("nav_restatement", "NAVs AMFI has restated", "warning",
     "Stored NAVs that disagree with what AMFI now publishes for the same date and could not be "
     "explained as a unit split; each one is a price the app may be computing returns from "
     "incorrectly.", _check_nav_restatement),
    ("isin_recoded", "ISIN shared with a retired scheme code", "info",
     "The same ISIN under an old and a new scheme code, usually AMFI re-coding a fund after a merger "
     "or rename; the fund's price history is split across the two codes. Pairs whose plan or option "
     "labels disagree are listed first, since one security cannot have two.", _check_isin_recoded),
    ("unlabelled_active", "Active scheme with no plan or option", "info",
     "AMFI's file leaves plan or option blank for these and nothing has settled them yet; they "
     "cannot be paired with a twin or given a plan-specific TER.", _check_unlabelled_active),
    ("legacy_provenance", "Plan/option of unknown origin", "info",
     "Active schemes whose plan or option predates provenance tracking, so nothing records whether "
     "AMFI stated it or it was inferred.", _check_legacy_provenance),
)


# --- public API ---------------------------------------------------------------


def run_data_audit(trigger: str = "manual") -> Dict[str, Any]:
    """Runs every check, stores the run, and returns it in latest_audit()'s shape.

    A check that raises is recorded as status "error" with the exception text rather
    than aborting the run: one broken query must not hide the other findings, and a
    check that cannot run is itself something the owner has to see."""
    started = time.perf_counter()
    run_at = datetime.datetime.now(datetime.timezone.utc)
    con = get_connection()
    try:
        ensure_audit_table(con)
        ctx = _Ctx(con)
        checks: List[Dict[str, Any]] = []
        for name, title, severity, description, fn in CHECKS:
            t0 = time.perf_counter()
            try:
                res = fn(con, ctx)
            except Exception as e:  # noqa: BLE001 -- see docstring
                logger.exception("Data audit check %s failed", name)
                res = _result(None, [], status="error", detail={"reason": f"{type(e).__name__}: {e}"})
            checks.append({
                "name": name, "title": title, "severity": severity, "description": description,
                "status": res["status"], "count": None if res["count"] is None else int(res["count"]),
                "samples": res["samples"], "detail": res["detail"],
                "elapsed_ms": int(round((time.perf_counter() - t0) * 1000)),
            })
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))

        # One transaction, so latest_audit() never reads a run that is half written.
        con.begin()
        try:
            run_id = con.execute("SELECT nextval('data_audit_run_seq')").fetchone()[0]
            for pos, c in enumerate(checks):
                con.execute(
                    """INSERT INTO data_audit_runs (run_id, run_at, trigger, run_elapsed_ms, position,
                           check_name, title, severity, status, count, description, samples, detail, elapsed_ms)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)""",
                    (run_id, run_at, trigger, elapsed_ms, pos, c["name"], c["title"], c["severity"],
                     c["status"], c["count"], c["description"], json.dumps(c["samples"], default=str),
                     json.dumps(c["detail"], default=str) if c["detail"] is not None else None,
                     c["elapsed_ms"]),
                )
            _prune_history(con, MAX_RUNS_KEPT)
            con.commit()
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()

    run = _assemble(run_id, run_at, trigger, elapsed_ms, checks)
    s = run["summary"]
    logger.info(f"Data audit ({trigger}) finished in {elapsed_ms} ms: {s['errors']} error check(s) flagged, "
                f"{s['warnings']} warning(s), {s['check_failures']} check(s) could not run.")
    return run


def latest_audit() -> Dict[str, Any]:
    """The most recent stored run, or {"empty": True} when there has never been one."""
    con = get_connection()
    try:
        if not _table_exists(con, "data_audit_runs"):
            return {"empty": True}
        rows = con.execute(
            """SELECT run_id, run_at, trigger, run_elapsed_ms, check_name, title, severity, status,
                      count, description, samples, detail, elapsed_ms
               FROM data_audit_runs
               WHERE run_id = (SELECT max(run_id) FROM data_audit_runs)
               ORDER BY position"""
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return {"empty": True}
    run_id, run_at, trigger, run_elapsed_ms = rows[0][:4]
    checks = [{
        "name": r[4], "title": r[5], "severity": r[6], "status": r[7],
        "count": None if r[8] is None else int(r[8]), "description": r[9],
        "samples": r[10] or [], "detail": r[11], "elapsed_ms": r[12],
    } for r in rows]
    return _assemble(run_id, run_at, trigger, run_elapsed_ms, checks)


def _assemble(run_id, run_at, trigger, elapsed_ms, checks) -> Dict[str, Any]:
    flagged = lambda sev: sum(1 for c in checks if c["severity"] == sev and c["status"] == "flagged")  # noqa: E731
    # Always UTC: read back from TIMESTAMPTZ the offset is the server's session timezone,
    # so the same run would otherwise serialize differently fresh and from storage.
    if isinstance(run_at, datetime.datetime):
        run_at = run_at.astimezone(datetime.timezone.utc).isoformat()
    return {
        "empty": False,
        "run_id": int(run_id),
        "run_at": run_at,
        "trigger": trigger,
        "elapsed_ms": elapsed_ms,
        "summary": {
            "errors": flagged("error"),
            "error_rows": sum(c["count"] or 0 for c in checks if c["severity"] == "error"),
            "warnings": flagged("warning"),
            "infos": flagged("info"),
            "check_failures": sum(1 for c in checks if c["status"] == "error"),
        },
        "checks": checks,
    }
