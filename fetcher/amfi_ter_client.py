import datetime
import json
import logging
import ssl
import time
import urllib.parse
import urllib.request
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
        request_delay_seconds: float = 0.15,
    ):
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.page_size = min(page_size, MAX_PAGE_SIZE)
        self.request_delay_seconds = request_delay_seconds
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

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
        """Yields every raw TER-portal row for a calendar month ('MM-YYYY'), transparently
        paginating through the full result set. A page that fails after retries is skipped
        (partial data is better than none), not fatal to the whole fetch."""
        page = 1
        while True:
            payload = self._fetch_page(month, page)
            if payload is None:
                break
            rows = payload.get("data") or []
            for row in rows:
                yield row
            meta = payload.get("meta") or {}
            page_count = meta.get("pageCount", 1) or 1
            if not rows or page >= page_count:
                break
            page += 1
            time.sleep(self.request_delay_seconds)

    @staticmethod
    def parse_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalizes and validates one raw API row, or returns None if malformed —
        never guesses a missing/invalid figure."""
        try:
            ter_date_raw = str(row.get("TER_Date") or "")
            ter_date = datetime.datetime.fromisoformat(ter_date_raw.replace("Z", "+00:00")).date()
            nsdl_code = str(row.get("NSDLSchemeCode") or "").strip()
            scheme_name = str(row.get("Scheme_Name") or "").strip()
            if not nsdl_code or not scheme_name:
                return None
            parsed = {
                "nsdl_scheme_code": nsdl_code,
                "scheme_name": scheme_name,
                "ter_date": ter_date,
                "r_ber": float(row["R_BER"]),
                "r_brokerage": float(row["R_BrokerageCost"]),
                "r_transaction": float(row["R_TransactionCost"]),
                "r_statutory": float(row["R_StatutoryLevies"]),
                "r_ter": float(row["R_TER"]),
                "d_ber": float(row["D_BER"]),
                "d_brokerage": float(row["D_BrokerageCost"]),
                "d_transaction": float(row["D_TransactionCost"]),
                "d_statutory": float(row["D_StatutoryLevies"]),
                "d_ter": float(row["D_TER"]),
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
        return months
