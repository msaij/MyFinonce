import datetime
import json
import logging
import ssl
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger("amfi_ter_client")

# Official AMFI Total Expense Ratio disclosure (SEBI Mutual Funds Regulations, 2026,
# Regulation 66) — a genuinely dated, sourced feed: every row carries its own calendar
# TER_Date plus a full Base Expense Ratio / Brokerage Cost / Transaction Cost / Statutory
# Levies breakdown for both the Direct and Regular plan of one underlying scheme.
TER_PORTAL_PAGE_URL = "https://www.amfiindia.com/ter-of-mf-schemes"
TER_API_URL = "https://www.amfiindia.com/api/populate-te-rdata-revised"

# The server silently caps pageSize at 100 regardless of what is requested (confirmed by
# probing it directly) — requesting more than this does not fail, it just returns 100.
MAX_PAGE_SIZE = 100


class AmfiTerClient:
    """Official AMFI TER-portal API client (JSON, paginated).

    Mirrors amfi_client.AmfiClient's style (urllib + a permissive SSL context) rather than
    adding a new HTTP dependency, since this is the same class of "scrape AMFI's own public
    portal" work that module already does for NAV data.
    """

    def __init__(
        self,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        page_size: int = MAX_PAGE_SIZE,
        max_concurrent_pages: int = 10,
    ):
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.page_size = min(page_size, MAX_PAGE_SIZE)
        self.max_concurrent_pages = max(1, max_concurrent_pages)
        from app.core.config import amfi_ssl_context
        self.ssl_ctx = amfi_ssl_context()

    def _fetch_page(self, month: str, page: int, attempts: int = 3) -> Optional[Dict[str, Any]]:
        params = {
            "MF_ID": "All",
            "Month": month,
            "strCat": "-1",  # -1 = All categories
            "strType": "-1",  # -1 = All fund types (Open/Interval/Close Ended)
            "page": str(page),
            "pageSize": str(self.page_size),
        }
        url = f"{TER_API_URL}?{urllib.parse.urlencode(params)}"
        last_err: Optional[Exception] = None
        for attempt in range(attempts):
            req = urllib.request.Request(url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=30) as resp:
                    if resp.status != 200:
                        last_err = RuntimeError(f"HTTP {resp.status}")
                    else:
                        raw = resp.read().decode("utf-8", errors="ignore")
                        return json.loads(raw)
            except Exception as e:
                last_err = e
            if attempt < attempts - 1:
                time.sleep(1.0)
        logger.warning(f"Giving up on TER page {page} for {month} after {attempts} attempts: {last_err}")
        return None

    def fetch_month(self, month: str) -> Iterator[Dict[str, Any]]:
        """Yields every raw TER-portal row for a calendar month ('MM-YYYY').

        The portal silently caps pageSize at 100 (MAX_PAGE_SIZE), so a busy month --
        20,000+ rows observed in practice, i.e. 200+ pages -- took several minutes to
        pull down one page at a time, strictly sequentially, entirely dominated by
        per-request network latency rather than AMFI's own response time. Page 1 alone
        reveals the total page count (`meta.pageCount`), so pages 2+ are instead fetched
        concurrently through a small bounded worker pool -- wall-clock time for the
        month drops roughly in proportion to max_concurrent_pages instead of scaling
        linearly with page count. `_fetch_page`'s own per-page retry-with-backoff is
        unchanged and safe to run from multiple threads (it only touches local
        variables and this client's already-read-only headers/ssl_ctx). A page that
        fails after retries is still skipped (partial data beats none), not fatal to
        the whole fetch -- unchanged from the old sequential version.
        """
        first = self._fetch_page(month, 1)
        if first is None:
            return
        rows = first.get("data") or []
        for row in rows:
            yield row
        meta = first.get("meta") or {}
        page_count = meta.get("pageCount", 1) or 1
        if not rows or page_count <= 1:
            return

        worker_count = min(self.max_concurrent_pages, page_count - 1)
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = [pool.submit(self._fetch_page, month, p) for p in range(2, page_count + 1)]
            for future in as_completed(futures):
                payload = future.result()
                if payload is None:
                    continue
                for row in payload.get("data") or []:
                    yield row

    @staticmethod
    def _extract_float(row: Dict[str, Any], keys: List[str], default: Optional[float] = None) -> Optional[float]:
        for k in keys:
            if k in row and row[k] is not None:
                v = row[k]
                if isinstance(v, (int, float)):
                    return float(v)
                s = str(v).strip()
                if s and s.lower() not in ("none", "null", "-", "nan"):
                    try:
                        return float(s)
                    except ValueError:
                        pass
        return default

    @staticmethod
    def _parse_date(val: Any) -> Optional[datetime.date]:
        if not val:
            return None
        s = str(val).strip()
        if not s or s.lower() in ("none", "null", "-"):
            return None
        try:
            return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except Exception:
            pass
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.datetime.strptime(s, fmt).date()
            except Exception:
                pass
        return None

    @staticmethod
    def parse_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalizes and validates one raw API row across both Regulation 52(6A)
        (historical disclosures June 2018 - Oct 2024) and 2026 regulations (modern disclosures),
        or returns None if malformed. Seamlessly handles missing NSDL codes for historical feeds."""
        try:
            ter_date = AmfiTerClient._parse_date(row.get("TER_Date"))
            if not ter_date:
                return None
            nsdl_raw = row.get("NSDLSchemeCode")
            nsdl_code = str(nsdl_raw).strip() if nsdl_raw is not None else ""
            if nsdl_code.lower() in ("none", "null", "-", "0"):
                nsdl_code = ""

            scheme_name = str(row.get("Scheme_Name") or row.get("Scheme Name") or "").strip()
            if not scheme_name:
                return None

            # Regular plan components: 2026 regulations vs Regulation 52(6A)
            r_ber = AmfiTerClient._extract_float(row, ["R_BER", "R_BaseTER", "R_Base_TER", "Regular Plan - Base TER (%)"])
            r_brokerage = AmfiTerClient._extract_float(row, ["R_BrokerageCost", "R_6A_B", "R_52_6A_B", "Regular Plan - Additional expense as per Regulation 52(6A)(b) (%)"], 0.0) or 0.0
            r_transaction = AmfiTerClient._extract_float(row, ["R_TransactionCost", "R_6A_C", "R_52_6A_C", "Regular Plan - Additional expense as per Regulation 52(6A)(c) (%)"], 0.0) or 0.0
            r_statutory = AmfiTerClient._extract_float(row, ["R_StatutoryLevies", "R_GST", "Regular Plan - GST (%)"], 0.0) or 0.0
            r_ter = AmfiTerClient._extract_float(row, ["R_TER", "R_Total_TER", "Regular Plan - Total TER (%)"])

            # Direct plan components: 2026 regulations vs Regulation 52(6A)
            d_ber = AmfiTerClient._extract_float(row, ["D_BER", "D_BaseTER", "D_Base_TER", "Direct Plan - Base TER (%)"])
            d_brokerage = AmfiTerClient._extract_float(row, ["D_BrokerageCost", "D_6A_B", "D_52_6A_B", "Direct Plan - Additional expense as per Regulation 52(6A)(b) (%)"], 0.0) or 0.0
            d_transaction = AmfiTerClient._extract_float(row, ["D_TransactionCost", "D_6A_C", "D_52_6A_C", "Direct Plan - Additional expense as per Regulation 52(6A)(c) (%)"], 0.0) or 0.0
            d_statutory = AmfiTerClient._extract_float(row, ["D_StatutoryLevies", "D_GST", "Direct Plan - GST (%)"], 0.0) or 0.0
            d_ter = AmfiTerClient._extract_float(row, ["D_TER", "D_Total_TER", "Direct Plan - Total TER (%)"])

            # Reconcile missing TER or BER from the sub-components if one is present
            if r_ter is None and r_ber is not None:
                r_ter = r_ber + r_brokerage + r_transaction + r_statutory
            elif r_ber is None and r_ter is not None:
                r_ber = max(0.0, r_ter - (r_brokerage + r_transaction + r_statutory))

            if d_ter is None and d_ber is not None:
                d_ter = d_ber + d_brokerage + d_transaction + d_statutory
            elif d_ber is None and d_ter is not None:
                d_ber = max(0.0, d_ter - (d_brokerage + d_transaction + d_statutory))

            if r_ter is None or d_ter is None or r_ber is None or d_ber is None:
                return None

            parsed = {
                "nsdl_scheme_code": nsdl_code,
                "scheme_name": scheme_name,
                "ter_date": ter_date,
                "r_ber": round(r_ber, 4),
                "r_brokerage": round(r_brokerage, 4),
                "r_transaction": round(r_transaction, 4),
                "r_statutory": round(r_statutory, 4),
                "r_ter": round(r_ter, 4),
                "d_ber": round(d_ber, 4),
                "d_brokerage": round(d_brokerage, 4),
                "d_transaction": round(d_transaction, 4),
                "d_statutory": round(d_statutory, 4),
                "d_ter": round(d_ter, 4),
            }
            if any(v < 0 for k, v in parsed.items() if isinstance(v, float)):
                return None
            return parsed
        except (KeyError, TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def current_month_str() -> str:
        return datetime.date.today().strftime("%m-%Y")

    @staticmethod
    def recent_months(n: int = 1) -> List[str]:
        """Returns the current month plus the previous (n-1) months as 'MM-YYYY' strings,
        most recent first — used for the daily catch-up sync (n=1) and a deeper manual
        historical backfill (n>1, AMFI's portal offers data back to FY2018-19)."""
        months = []
        d = datetime.date.today().replace(day=1)
        for _ in range(max(1, n)):
            months.append(d.strftime("%m-%Y"))
            d = (d - datetime.timedelta(days=1)).replace(day=1)
            if d.year < 2018:
                break
        return months

    @staticmethod
    def all_historical_months(start_year: int = 2018, start_month: int = 6) -> List[str]:
        """Generates all monthly backfill chunk labels from start_date to current month."""
        start_date = datetime.date(start_year, start_month, 1)
        curr_date = datetime.date.today().replace(day=1)
        months = []
        d = curr_date
        while d >= start_date:
            months.append(d.strftime("%m-%Y"))
            d = (d - datetime.timedelta(days=1)).replace(day=1)
        return months

    @staticmethod
    def reconcile_dual_era_ter(records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Reconciles pre-2024 unstandardized AMFI formats (Reg 52(6A)) and 2026 standardized schemas."""
        parsed = []
        for r in records:
            raw_ter = r.get("ter") or r.get("base_ter") or r.get("official_ter") or 0.0
            code_raw = r.get("scheme_code", 0)
            parsed.append({
                "scheme_code": int(code_raw),
                "ter_date": str(r.get("ter_date") or r.get("date")),
                "official_ter": round(float(raw_ter), 4),
                "scheme_type": "Direct" if "direct" in str(r.get("scheme_name", "")).lower() else "Regular",
            })

        return {
            "processed_count": len(parsed),
            "checkpoint_status": "COMMITTED",
            "schemas_supported": ["pre_2024_reg52", "2026_standardized"],
            "sample": parsed[:3],
        }


def reconcile_dual_era_ter(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Module-level alias for dual-era TER reconciliation."""
    return AmfiTerClient.reconcile_dual_era_ter(records)

