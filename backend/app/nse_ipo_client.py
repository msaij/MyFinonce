"""NSE's public-issue (IPO) data: the feeds behind nseindia.com/market-data/all-upcoming-issues-ipo
and each issue's /market-data/issue-information page.

The endpoints are undocumented -- they are what those pages themselves call. NSE only
answers its /api/ paths for a client holding the cookies its HTML pages set, so a session
first loads the listing page and then reuses those cookies; when they lapse (NSE rotates
them within minutes) the API returns 401/403 or an HTML error page, and the session is
re-primed once before giving up.

Nothing from NSE is stored anywhere -- no table, no file, and deliberately no in-process
cache either: every request is fetched from NSE afresh and discarded once returned. Only
the session's cookies are held (in memory), since NSE refuses the feeds without them.
"""

from __future__ import annotations

import datetime
import html
import logging
import re
import threading
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("nse_ipo_client")

NSE_BASE = "https://www.nseindia.com"
LISTING_PAGE = f"{NSE_BASE}/market-data/all-upcoming-issues-ipo"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": LISTING_PAGE,
}


class NseUnavailable(RuntimeError):
    """NSE could not be reached, or refused the request even with fresh cookies."""


class _NseSession:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self._lock = threading.Lock()
        self._session: Optional[requests.Session] = None

    def _prime(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(_HEADERS)
        s.get(LISTING_PAGE, timeout=self.timeout, headers={"Accept": "text/html,application/xhtml+xml"})
        self._session = s
        return s

    def get_json(self, path: str, params: Optional[Dict[str, str]] = None) -> Any:
        last_err: Optional[Exception] = None
        for attempt in range(2):
            with self._lock:
                s = self._session if (self._session is not None and attempt == 0) else None
                try:
                    s = s or self._prime()
                except requests.RequestException as e:
                    raise NseUnavailable(f"could not open an NSE session: {e}") from e
            try:
                resp = s.get(f"{NSE_BASE}{path}", params=params, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.json()
                last_err = RuntimeError(f"HTTP {resp.status_code}")
            except (requests.RequestException, ValueError) as e:  # ValueError: an HTML error page instead of JSON
                last_err = e
            logger.info(f"NSE {path} attempt {attempt + 1} failed ({last_err}); re-priming the session")
        raise NseUnavailable(f"NSE {path} failed: {last_err}")


_nse = _NseSession()


# --- value cleaning ---------------------------------------------------------------------

def _num(v: Any) -> Optional[float]:
    """NSE numbers arrive as strings in three shapes: '14976743', '1.4976743E7' and the
    Indian-grouped '1,97,03,310'. Blank and '-' mean absent."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "NA", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(v: Any) -> Optional[int]:
    n = _num(v)
    return int(round(n)) if n is not None else None


def _date(v: Any) -> Optional[str]:
    """'25-Sep-2026' / '24-SEP-2026' -> '2026-09-25'."""
    if not v or not isinstance(v, str) or v.strip() in ("-", ""):
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d %b %Y"):
        try:
            return datetime.datetime.strptime(v.strip().title(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _text(v: Any) -> str:
    """NSE wraps many values in literal double quotes ('"Rs. 2,00,000"')."""
    s = html.unescape(str(v or "")).strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1].strip()
    return s


_ANCHOR = re.compile(r"<a\s+href=['\"]?([^'\" >]+)['\"]?[^>]*>(.*?)</a>", re.I | re.S)


def _info_item(title: Any, value: Any) -> Dict[str, Optional[str]]:
    """One Issue Information row. A value is plain text, a bare URL (a document on
    nsearchives), or an HTML anchor; links come back separated so the page never has to
    render NSE's HTML."""
    text = _text(value)
    url: Optional[str] = None
    m = _ANCHOR.search(text)
    if m:
        url, text = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
    elif re.match(r"^https?://\S+$", text):
        url = text
        text = text.rsplit("/", 1)[-1] or text
    return {"title": _text(title) if title is not None else None, "value": text, "url": url}


# --- listing tabs -----------------------------------------------------------------------

def current_issues() -> List[Dict[str, Any]]:
    """The listing page's 'Current' tab: issues open for bidding, with NSE bid totals."""
    rows = _nse.get_json("/api/ipo-current-issue") or []
    return [
        {
            "company": r.get("companyName"),
            "symbol": r.get("symbol"),
            "series": r.get("series"),
            "issue_start": _date(r.get("issueStartDate")),
            "issue_end": _date(r.get("issueEndDate")),
            "status": r.get("status"),
            "price_range": r.get("issuePrice"),
            "issue_size": _int(r.get("issueSize")),
            "shares_offered": _int(r.get("noOfSharesOffered")),
            "shares_bid": _int(r.get("noOfsharesBid")),
            "subscription_times": _num(r.get("noOfTime")),
        }
        for r in rows
    ]


def upcoming_issues() -> List[Dict[str, Any]]:
    """The 'Upcoming Issues' feed. NSE's own tab keeps only status 'Forthcoming'; every row
    is returned here with its status so nothing the feed carries is dropped."""
    rows = _nse.get_json("/api/all-upcoming-issues", {"category": "ipo"}) or []
    return [
        {
            "company": r.get("companyName"),
            "symbol": r.get("symbol"),
            "series": r.get("series"),
            "issue_start": _date(r.get("issueStartDate")),
            "issue_end": _date(r.get("issueEndDate")),
            "status": r.get("status"),
            "price_range": r.get("issuePrice"),
            "issue_size": _int(r.get("issueSize")),
        }
        for r in rows
    ]


# --- one issue --------------------------------------------------------------------------

def _bid_rows(rows: List[Dict[str, Any]], offered_key: str, bid_key: str, times_key: str) -> List[Dict[str, Any]]:
    out = []
    for r in rows or []:
        if r.get("srNo") == "Sr.No.":  # the consolidated table ships its own header as row 0
            continue
        out.append({
            "sr_no": r.get("srNo"),
            "category": r.get("category"),
            "shares_offered": _int(r.get(offered_key)),
            "shares_bid": _int(r.get(bid_key)),
            "subscription_times": _num(r.get(times_key)),
        })
    return out


def _demand_graph(g: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not g:
        return None
    cumulative = {str(k): _int(v) for k, v in (g.get("plotData") or {}).items()}
    points = []
    for p in g.get("graphData") or []:
        price = str(p.get("type"))
        points.append({
            "price": price,
            # graphData is the quantity bid at exactly this price, in lakh shares.
            "qty_lakh": _num(p.get("value")),
            "cumulative_qty": cumulative.get(price, cumulative.get(price.replace("off", "Off"))),
        })
    return {
        "heading": _text(g.get("heading1")),
        "subheading": _text(g.get("heading2")),
        "timestamp": g.get("timestamp"),
        "total_bids": _int(g.get("totalBidRecieved") or g.get("TOTAL_BIDS")),
        "total_issue_size": _int(g.get("totalIssueSize")),
        "bids_at_cutoff": _int(g.get("totalBidAtCutOff")),
        "times_subscribed": _num(g.get("noOfTimesIssueSubscribed")),
        "note": _text(g.get("note")),
        "graph_logic_url": g.get("graphLogic"),
        "points": points,
    }


def _demand_data(rows: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    return [
        {"price": r.get("price"), "cumulative_qty": _int(r.get("cumQty")), "timestamp": r.get("timestamp")}
        for r in rows or []
        if r.get("price") not in (None, "", "-")  # a forthcoming issue ships one "-" placeholder row
    ]


def issue_detail(symbol: str, series: Optional[str] = None) -> Dict[str, Any]:
    """Everything on NSE's issue-information page for one issue: Issue Information, NSE and
    Consolidated Bid Details, the NSE and All-exchange Demand Graphs and Demand Data."""
    params = {"symbol": symbol}
    if series:
        params["series"] = series
    d = _nse.get_json("/api/ipo-detail", params) or {}

    info = [_info_item(i.get("title"), i.get("value")) for i in (d.get("issueInfo") or {}).get("dataList") or []]
    # Row 0 is the company's name with no value; untitled rows are notices (SEBI circulars).
    heading = next((i["title"] for i in info if i["title"] and not i["value"] and not i["url"]), None)
    notices = [i["value"] for i in info if not i["title"] and i["value"]]
    fields = [i for i in info if i["title"] and (i["value"] or i["url"])]

    active = d.get("activeCat") or {}
    meta = d.get("metaInfo") or {}
    return {
        "symbol": symbol,
        "series": series,
        "heading": heading,
        "notices": notices,
        "issue_info": fields,
        "listing": {
            "isin": meta.get("isin"),
            "industry": meta.get("industry"),
            "listing_date": meta.get("listingDate"),
        } if meta else None,
        "bid_details_nse": _bid_rows(d.get("bidDetails"), "noOfSharesOffered", "noOfsharesBid", "noOfTime"),
        "bid_details_consolidated": _bid_rows(active.get("dataList"), "noOfShareOffered", "noOfSharesBid", "noOfTotalMeant"),
        # "Updated as on null" before bidding opens.
        "consolidated_updated": None if "null" in str(active.get("updateTime") or "null") else active.get("updateTime"),
        "demand_graph_nse": _demand_graph(d.get("demandGraph")),
        "demand_graph_all": _demand_graph(d.get("demandGraphALL")),
        "demand_data_nse": _demand_data(d.get("demandDataNSE")),
        "demand_data_all": _demand_data(d.get("demandDataBSE")),
    }
