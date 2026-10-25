"""Global financial news: feed ingestion, classification, storage and the read path.

One sweep (the daemon runs one every SWEEP_INTERVAL_SECONDS; POST /api/news/refresh can
ask for one sooner, at most once a minute, and never alongside another):

  1. Relabel stored rows the current classifier has not seen (classifier_version), so a
     rule change reaches the rows already stored, not only new ones.
  2. Fetch every feed in parallel under one deadline: an honest User-Agent, conditional
     GET, a size cap and a strict XML parse. A feed that answers 200 with nothing usable
     counts as failing, not as fine.
  3. Turn items into articles. Dates resolve in the feed's own time zone (RBI sends naive
     IST). Items with no usable date, from beyond the retention window, or dated in the
     future are dropped, and so are a feed's routine releases (RBI money-market
     operations, SEBI recovery notices). Text is cleaned, and category, region,
     sentiment and keywords are assigned.
  4. Merge duplicates. The same source with the same title close in time is the same
     article and is dropped. The same story from another outlet joins that story's
     cluster, which the UI shows as "+N more sources" rather than a second card.
  5. Upsert in one transaction, prune past NEWS_RETENTION_DAYS (every sweep, so the window
     holds), and record per-feed health for /api/news/sources.

Primary sources (central banks, regulators) keep a fixed category and region and are
always sentiment-neutral: an RBI T-bill "cut-off" is not bad news, and a Fed release is
about the Fed whatever its words.

Feed choice is in FEEDS, with why each one is there. Google News is deliberately absent:
its links are redirects through Google, which learns what is read, its "summaries" are
other outlets' headlines, and its edition follows the server's IP.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import datetime
import email.utils
import functools
import hashlib
import html
from html.parser import HTMLParser
import logging
import math
import re
import threading
import time
from typing import Any, NamedTuple, Optional, Sequence
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from zoneinfo import ZoneInfo

from cachetools import TTLCache

from app.core.config import settings
from app.db.connection import get_connection

logger = logging.getLogger("news_service")
logger.setLevel(logging.INFO)

UTC = datetime.timezone.utc

# --- Policy -----------------------------------------------------------------------------

# The owner's window: articles are kept, searched and shown for 30 days, and no longer.
NEWS_RETENTION_DAYS = 30
SWEEP_INTERVAL_SECONDS = 1200
# After a sweep in which no feed answered (internet down), try again sooner.
RETRY_AFTER_FAILED_SWEEP_SECONDS = 300
# The daemon's first sweep waits at least this long after startup, and longer when the
# stored data is still fresh (see _initial_delay_seconds).
MIN_STARTUP_DELAY_SECONDS = 5.0
# A manual refresh within this long of the last sweep returns that sweep's result.
MIN_MANUAL_REFRESH_SECONDS = 60
# Clock skew we forgive: a pubDate up to this far ahead of our fetch is clamped to the
# fetch time. Further ahead means the date is wrong (a time zone bug), so it is dropped.
FUTURE_TOLERANCE = datetime.timedelta(minutes=10)
# The UI stops calling the feed "Live" when no sweep has succeeded for this long.
STALE_DATA_AFTER = datetime.timedelta(minutes=60)
DEFAULT_STALE_FEED_DAYS = 7

MAX_FEED_BYTES = 5 * 1024 * 1024  # the largest feed today is ~150 KB
FEED_SOCKET_TIMEOUT = 10.0  # per socket operation
FEED_DEADLINE_SECONDS = 20.0  # per feed, total: a host trickling bytes cannot stall a sweep
SWEEP_DEADLINE_SECONDS = 45.0  # for all feeds together
FETCH_WORKERS = 6
# Backoff after repeated failures, longer than the sweep interval so it actually skips sweeps.
BACKOFF_BASE_SECONDS = 1500
BACKOFF_MAX_SECONDS = 6 * 3600
USER_AGENT = "myFinonce/0.1 (personal RSS reader)"

SUMMARY_MAX_DEFAULT = 600
# Bump when classification or text cleaning changes: stored rows below it get relabelled.
CLASSIFIER_VERSION = 3

# Duplicate handling (see _assign_clusters).
SAME_SOURCE_DUP_WINDOW = datetime.timedelta(hours=12)
# An outlet re-posting its own story with a reworded headline ("India's ..." prepended):
# stricter than the cross-outlet threshold, since one outlet's distinct stories on one
# topic share many words.
SAME_SOURCE_NEAR_DUP_JACCARD = 0.8
NEAR_DUP_WINDOW = datetime.timedelta(hours=48)
NEAR_DUP_JACCARD = 0.6
NEAR_DUP_MIN_TOKENS = 4

# The category and region contract with the frontend (components/news/types.ts mirrors
# these, and both test suites pin the same literal lists).
CATEGORY_LABELS = {
    "central_banks": "Central Banks",
    "macro": "Macro",
    "equities": "Equities",
    "commodities_forex": "FX & Commodities",
}
CATEGORY_SLUGS = tuple(CATEGORY_LABELS)
REGION_LABELS = {
    "global": "Global",
    "india": "India",
    "us": "US",
    "uk": "UK",
    "europe": "Europe",
    "asia": "Asia-Pacific",
}
REGION_SLUGS = tuple(REGION_LABELS)


# --- Feeds ------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Feed:
    name: str  # unique; the key for health and the `feed` column
    source: str  # the publisher, as shown on cards
    url: str
    category: str  # default, or fixed when official/fixed_category
    region: str  # default, or fixed when official
    official: bool = False  # primary source: fixed category and region, neutral sentiment
    fixed_category: bool = False  # a specialist outlet (oil, FX): category fixed, region inferred
    tz: str = "UTC"  # applied only to dates that carry no zone
    date_formats: tuple[str, ...] = ()  # strptime formats tried before the generic ones
    date_only: bool = False  # the feed's dates carry no real time of day
    paywall: Optional[str] = None  # "partial" (some articles) or "soft" (metered)
    exclude_title_patterns: tuple[str, ...] = ()
    exclude_url_patterns: tuple[str, ...] = ()
    max_items: int = 60
    summary_max: int = SUMMARY_MAX_DEFAULT
    stale_after_days: int = DEFAULT_STALE_FEED_DAYS


# RBI's feed is mostly daily operations: money-market tables, auction results and
# penalties on individual co-operative banks. Policy, circulars and statements stay.
# Kept free of \b and other Python-only syntax: migration 0012 applies the same list in
# Postgres (where \b means backspace) to the rows stored before it.
RBI_ROUTINE_TITLE_PATTERNS = (
    r"^Money Market Operations",
    r"Auction Result",
    r"^Result of ",
    r"^Detailed Result",
    r"^RBI to conduct ",
    r"T-Bill",
    r"Treasury Bills?",
    r"Redemption Price",
    r"^Premature Redemption",
    r"^Conversion/Switch",
    r"^Auction of ",
    r"^Underwriting Auction",
    r"^(Weekly Statistical Supplement|Reserve Money|Money Supply)",
    r"Monetary Penalty",
    r"Certificate of Registration",
    r"Section 35 ?A",
    r"^Lending and Deposit Rates",
)

# Tested from inside the backend container on 2026-10-15 with USER_AGENT; see the grill
# report's "Recommended feed list" and its Responder section for each verdict. Hard
# paywalls (FT, Bloomberg, WSJ, Nikkei) are left out by the owner's decision. Metered
# (MarketWatch) and partial ones (CNBC, Livemint, ET) stay, since most of their articles
# are free; `paywall` labels them in the preview drawer and the sources panel.
FEEDS: tuple[Feed, ...] = (
    # Central banks and regulators: primary sources.
    Feed("Fed: Monetary policy", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml",
         "central_banks", "us", official=True, stale_after_days=60),
    Feed("Fed: Speeches", "Federal Reserve", "https://www.federalreserve.gov/feeds/speeches.xml",
         "central_banks", "us", official=True, stale_after_days=30),
    Feed("ECB: Press", "ECB", "https://www.ecb.europa.eu/rss/press.html",
         "central_banks", "europe", official=True, stale_after_days=30),
    Feed("Bank of England: News", "Bank of England", "https://www.bankofengland.co.uk/rss/news",
         "central_banks", "uk", official=True, stale_after_days=30),
    # BIS dates are midnight UTC whatever the release time.
    Feed("BIS: Press releases", "BIS", "https://www.bis.org/doclist/all_pressrels.rss",
         "central_banks", "global", official=True, date_only=True, stale_after_days=45),
    # Naive IST timestamps ("Thu, 15 Oct 2026 09:00:00"), and summaries that are tables.
    Feed("RBI: Press releases", "Reserve Bank of India", "https://www.rbi.org.in/pressreleases_rss.xml",
         "central_banks", "india", official=True, tz="Asia/Kolkata",
         exclude_title_patterns=RBI_ROUTINE_TITLE_PATTERNS, summary_max=300),
    # "14 Oct, 2026 +0530": a date with no time. 28 of 30 items are recovery notices and
    # orders naming individuals (with PANs); only press releases and circulars stay.
    Feed("SEBI", "SEBI", "https://www.sebi.gov.in/sebirss.xml",
         "equities", "india", official=True, tz="Asia/Kolkata", date_formats=("%d %b, %Y %z",),
         date_only=True, exclude_url_patterns=(r"/enforcement/",), stale_after_days=14),
    # Global media.
    Feed("CNBC: World", "CNBC", "https://www.cnbc.com/id/100727362/device/rss/rss.html",
         "macro", "global", paywall="partial"),
    Feed("CNBC: Economy", "CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html",
         "macro", "us", paywall="partial"),
    Feed("MarketWatch: Top stories", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories",
         "equities", "us", paywall="soft"),
    Feed("BBC: Business", "BBC", "https://feeds.bbci.co.uk/news/business/rss.xml", "macro", "global"),
    # Its description carries the article's opening paragraphs and related links.
    Feed("The Guardian: Business", "The Guardian", "https://www.theguardian.com/uk/business/rss",
         "macro", "global", summary_max=300),
    Feed("DW: Business", "DW", "https://rss.dw.com/rdf/rss-en-bus", "macro", "europe"),
    Feed("CNA: Business", "CNA", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936",
         "macro", "asia"),
    Feed("OilPrice.com", "OilPrice.com", "https://oilprice.com/rss/main",
         "commodities_forex", "global", fixed_category=True),
    Feed("FXStreet: News", "FXStreet", "https://www.fxstreet.com/rss/news",
         "commodities_forex", "global", fixed_category=True),
    # India media.
    Feed("Livemint: Markets", "Livemint", "https://www.livemint.com/rss/markets", "equities", "india", paywall="partial"),
    Feed("Livemint: Economy", "Livemint", "https://www.livemint.com/rss/economy", "macro", "india", paywall="partial"),
    Feed("Economic Times: Markets", "Economic Times",
         "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "equities", "india", paywall="partial"),
    Feed("Economic Times: Economy", "Economic Times",
         "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms", "macro", "india", paywall="partial"),
)

_FEED_BY_NAME = {f.name: f for f in FEEDS}
# Several feeds share a publisher; the first one's defaults stand in for rows stored
# before the `feed` column existed.
_FEED_BY_SOURCE: dict[str, Feed] = {}
for _f in FEEDS:
    _FEED_BY_SOURCE.setdefault(_f.source, _f)


@functools.lru_cache(maxsize=None)
def _compile_patterns(patterns: tuple[str, ...]) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def _fallback_feed(source: str) -> Feed:
    return Feed(name="", source=source or "", url="", category="macro", region="global")


# --- Text -------------------------------------------------------------------------------


class SecureTextExtractor(HTMLParser):
    """HTML parser that drops scripts, styles, frames and objects, decodes entities and
    keeps only text, for safe plain-text excerpts."""

    DISALLOWED_TAGS = frozenset({
        "script", "style", "iframe", "object", "svg", "noscript", "applet",
        "embed", "frame", "frameset",
    })

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._ignore_depth = 0
        self._text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag.lower() in self.DISALLOWED_TAGS:
            self._ignore_depth += 1

    def handle_endtag(self, tag: str):
        if tag.lower() in self.DISALLOWED_TAGS and self._ignore_depth > 0:
            self._ignore_depth -= 1

    def handle_data(self, data: str):
        if self._ignore_depth == 0:
            self._text_chunks.append(data)

    def get_text(self) -> str:
        return _collapse_ws(" ".join(self._text_chunks))


# Zero-width characters some publishers sprinkle into text (the Guardian does).
_ZERO_WIDTH = re.compile(r"[​-‍⁠﻿]")


def _collapse_ws(text: str) -> str:
    # \s in a str pattern also matches NBSP, so a decoded &nbsp; becomes a plain space.
    return re.sub(r"\s+", " ", _ZERO_WIDTH.sub("", text)).strip()


# An entity still in the text after the HTML pass. Livemint escapes twice ("&amp;nbsp;"
# inside CDATA), so one decode leaves a literal "&nbsp;" on screen.
_LEFTOVER_ENTITY = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]{1,31}|#\d{1,7}|#[xX][0-9a-fA-F]{1,6});")


def clean_html_text(raw_html: str) -> str:
    """Plain text from HTML: harmful tags dropped, entities decoded, whitespace collapsed."""
    if not raw_html:
        return ""
    parser = SecureTextExtractor()
    try:
        parser.feed(raw_html)
        parser.close()
        text = parser.get_text()
        if _LEFTOVER_ENTITY.search(text):
            # One more decode, of entities only: nothing is parsed as markup again.
            text = _collapse_ws(html.unescape(text))
        return text
    except Exception:
        cleaned = re.sub(
            r"<(script|style|iframe|object|svg|noscript)[^>]*>.*?</\1>",
            "",
            raw_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        return _collapse_ws(cleaned)


def strip_publisher_suffix(title: str, source: str) -> str:
    """Drops a trailing " - Publisher" / " | Publisher" naming the item's own source, which
    would otherwise make one story hash differently per outlet."""
    if not title or not source:
        return title
    m = re.match(r"^(.*\S)\s+[-|–—]\s+(.+)$", title)
    if m and m.group(2).strip().lower() == source.strip().lower():
        return m.group(1)
    return title


def clean_title_text(raw_title: str, source: str) -> str:
    return strip_publisher_suffix(clean_html_text(raw_title), source)


def _clean_summary(raw_summary: str, title: str, feed: Feed) -> str:
    summary = clean_html_text(raw_summary or "")
    # SEBI repeats the title as the description; showing it twice is noise.
    if summary.lower() == (title or "").lower():
        return ""
    if len(summary) > feed.summary_max:
        cut = summary[: feed.summary_max].rsplit(" ", 1)[0].rstrip(",;:-")
        summary = cut + "…"
    return summary


def validate_article_url(url: str) -> bool:
    """Only http(s) URLs with a dotted host: no javascript:, data: or file: links."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() not in ("http", "https"):
            return False
        if not parsed.netloc or "." not in parsed.netloc:
            return False
        return True
    except Exception:
        return False


_TRACKING_KEYS = frozenset({
    "oc", "ref", "fbclid", "gclid", "ved", "usqp", "src", "_ga", "mc_cid", "mc_eid",
    # DW's and BBC's RSS tags.
    "maca", "at_medium", "at_campaign", "at_link_origin", "at_ptr_name",
    "cmpid", "ncid", "xtor",
})


def canonicalize_url(url: str) -> str:
    """Normalises a URL (lowercase scheme and host, no default port, no trailing slash,
    no repeated slashes, no fragment, tracking parameters stripped) while keeping the
    parameters that identify the article."""
    if not url:
        return ""
    url = url.strip()
    try:
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()

        if scheme == "http" and netloc.endswith(":80"):
            netloc = netloc[:-3]
        elif scheme == "https" and netloc.endswith(":443"):
            netloc = netloc[:-4]

        # The ECB's feed links read "europa.eu//press/...".
        path = re.sub(r"/{2,}", "/", parsed.path)
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [
            (k, v) for k, v in query_pairs
            if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_KEYS
        ]
        filtered.sort(key=lambda x: (x[0], x[1]))
        return urllib.parse.urlunsplit((scheme, netloc, path, urllib.parse.urlencode(filtered), ""))
    except Exception:
        return url


# Stop words for the title signature and the near-duplicate token sets.
_STOP_WORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but",
    "by", "can", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from",
    "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just",
    "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once",
    "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she",
    "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them",
    "themselves", "then", "there", "these", "they", "this", "those", "through", "to", "too",
    "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which",
    "while", "who", "whom", "why", "will", "with", "you", "your", "yours", "yourself",
    "yourselves", "says", "said", "amid", "amidst", "new", "per", "via",
    "us", "statement", "update", "updates", "report", "reports", "brief", "summary",
})


def title_tokens(title: str) -> list[str]:
    """Content words of a title: lowercased, apostrophes dropped (India's == Indias),
    punctuation split, stop words and single characters removed."""
    if not title:
        return []
    t = re.sub(r"['’`]", "", title.lower())
    clean = re.sub(r"[^\w\s]", " ", t)
    return [w for w in clean.split() if len(w) > 1 and w not in _STOP_WORDS]


def _title_numbers(title: str) -> frozenset[str]:
    return frozenset(re.findall(r"\d+(?:[.,]\d+)*", title or ""))


def compute_title_signature(title: str) -> str:
    """A 16-character hash of the title's sorted content words, for exact wire duplicates."""
    if not title:
        return ""
    tokens = sorted(title_tokens(title))
    normalized = " ".join(tokens) or title.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# --- Classification ---------------------------------------------------------------------
#
# Terms match on word boundaries ("oil" is not in "toilet", "gold" is not in "Goldman",
# "rbi" is not in "orbital"). Each category scores 2 per distinct term in the title and 1
# per distinct term in the summary; the highest score wins, ties go to the feed's own
# default. Role terms, not names: "Shaktikanta Das" and "Jerome Powell" rot.

# Ambiguous as lowercase words ("us", "fed up", "eu"): matched with their case.
_CASE_SENSITIVE_TERMS = frozenset({"US", "Fed", "EU"})

CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "central_banks": (
        "central bank", "central banks", "central banker", "central bankers",
        "rbi", "reserve bank", "rbi governor", "Fed", "federal reserve", "fomc", "fed chair",
        "ecb", "european central bank", "bank of england", "boe", "boj", "bank of japan",
        "pboc", "people's bank of china", "snb", "swiss national bank", "rba", "bank of canada",
        "monetary policy", "monetary policy committee", "mpc", "policy rate", "repo rate",
        "reverse repo", "rate hike", "rate hikes", "rate cut", "rate cuts", "interest rate",
        "interest rates", "rate decision", "basis points", "bps", "quantitative easing",
        "quantitative tightening", "dovish", "hawkish",
    ),
    "commodities_forex": (
        "crude", "crude oil", "brent", "wti", "oil", "oil prices", "opec", "opec+",
        "natural gas", "lng", "gas prices", "gold", "silver", "copper", "platinum", "palladium",
        "aluminium", "aluminum", "iron ore", "lithium", "commodity", "commodities", "metals",
        "precious metals", "diesel", "gasoline", "petrol", "fuel prices", "wheat", "soybeans",
        "coffee", "cocoa", "forex", "fx", "currency", "currencies", "exchange rate", "rupee",
        "dollar", "dollar index", "dxy", "yen", "yuan", "renminbi", "sterling", "pound",
        "swiss franc", "usd/inr", "usdinr", "eur/usd", "gbp/usd", "usd/jpy", "aud/usd",
        "usd/cad", "usd/chf", "nzd/usd", "xau/usd", "xag/usd", "bitcoin", "crypto",
        "cryptocurrency",
    ),
    "equities": (
        "stock", "stocks", "share", "shares", "equity", "equities", "stock market",
        "stock markets", "sensex", "nifty", "bse", "nse", "wall street", "dow", "dow jones",
        "nasdaq", "s&p 500", "s&p", "ftse", "dax", "nikkei", "hang seng", "kospi", "stoxx",
        "cac 40", "ipo", "ipos", "listing", "earnings", "quarterly results", "q1 results",
        "q2 results", "q3 results", "q4 results", "profit", "net profit", "revenue",
        "market cap", "valuation", "buyback", "dividend", "bull run", "bear market",
        "bull market", "smallcap", "small-cap", "midcap", "mid-cap", "largecap", "large-cap",
        "blue chip", "blue-chip", "mutual fund", "mutual funds", "etf", "etfs", "fii", "fiis",
        "fpi", "fpis", "brokerage", "target price",
    ),
    "macro": (
        "gdp", "cpi", "wpi", "inflation", "deflation", "fiscal deficit", "trade deficit",
        "current account", "budget", "fiscal", "unemployment", "jobless", "jobs report",
        "payrolls", "nonfarm payrolls", "recession", "economy", "economic", "economies",
        "growth forecast", "imf", "world bank", "pmi", "industrial output", "iip",
        "retail sales", "consumer spending", "consumer confidence", "tariff", "tariffs",
        "trade war", "trade deal", "exports", "imports", "bond yields", "treasury yields",
        "yields", "bonds", "gilts", "debt", "sovereign debt", "credit rating", "wages",
        "housing", "manufacturing",
    ),
}
_CATEGORY_PRIORITY = ("central_banks", "commodities_forex", "equities", "macro")

REGION_TERMS: dict[str, tuple[str, ...]] = {
    "india": (
        "india", "indian", "indians", "rbi", "sebi", "nifty", "sensex", "rupee", "rupees",
        "mumbai", "delhi", "new delhi", "bengaluru", "bse", "nse", "dalal street", "crore",
        "lakh", "gst",
    ),
    "us": (
        "US", "u.s.", "united states", "america", "american", "americans", "Fed",
        "federal reserve", "fomc", "wall street", "dow", "dow jones", "nasdaq", "s&p 500",
        "s&p", "washington", "white house", "nonfarm payrolls",
    ),
    "uk": (
        "uk", "u.k.", "britain", "british", "bank of england", "boe", "ftse", "london",
        "sterling", "gilt", "gilts", "england", "scotland",
    ),
    "europe": (
        "europe", "european", "eurozone", "euro zone", "euro area", "ecb", "EU",
        "european union", "germany", "german", "france", "french", "italy", "italian",
        "spain", "spanish", "netherlands", "dutch", "brussels", "frankfurt", "paris",
        "berlin", "dax", "stoxx", "cac 40", "switzerland", "swiss", "snb", "sweden",
        "norway", "poland",
    ),
    "asia": (
        "asia", "asian", "asia-pacific", "china", "chinese", "beijing", "shanghai",
        "shenzhen", "hong kong", "japan", "japanese", "tokyo", "nikkei", "boj",
        "bank of japan", "yen", "yuan", "renminbi", "pboc", "people's bank of china", "korea",
        "korean", "seoul", "kospi", "taiwan", "taiwanese", "singapore", "indonesia",
        "malaysia", "thailand", "vietnam", "philippines", "hang seng", "australia",
        "australian", "rba", "asx", "new zealand",
    ),
}


class _TermSet:
    """A list of terms compiled into two alternations (case-insensitive and
    case-sensitive), longest first so "crude oil" wins over "crude"."""

    def __init__(self, terms: Sequence[str]):
        ci = sorted({t.lower() for t in terms if t not in _CASE_SENSITIVE_TERMS}, key=len, reverse=True)
        cs = sorted({t for t in terms if t in _CASE_SENSITIVE_TERMS}, key=len, reverse=True)
        self._ci = self._alternation(ci, re.IGNORECASE)
        self._cs = self._alternation(cs, 0)

    @staticmethod
    def _alternation(terms: list[str], flags: int) -> Optional[re.Pattern]:
        if not terms:
            return None
        body = "|".join(re.escape(t) for t in terms)
        return re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])", flags)

    def find(self, text: str) -> list[str]:
        """Distinct matched terms in order of appearance (case-insensitive ones lowercased)."""
        if not text:
            return []
        hits: list[tuple[int, str]] = []
        if self._ci:
            hits += [(m.start(), m.group(0).lower()) for m in self._ci.finditer(text)]
        if self._cs:
            hits += [(m.start(), m.group(0)) for m in self._cs.finditer(text)]
        seen: dict[str, None] = {}
        for _, term in sorted(hits):
            seen.setdefault(term, None)
        return list(seen)


_CATEGORY_SETS = {cat: _TermSet(terms) for cat, terms in CATEGORY_TERMS.items()}
_REGION_SETS = {reg: _TermSet(terms) for reg, terms in REGION_TERMS.items()}


def _normalise_for_matching(text: str) -> str:
    return (text or "").replace("’", "'").replace("‘", "'")


# A headline's subject is its main clause: in "SK Hynix shares plunge amid Fed rate-hike
# fears" the story is the shares, the Fed is the cause.
_SUBORDINATE = re.compile(r"\s(?:as|amid|amidst|after|despite|ahead of|while|following)\s", re.IGNORECASE)


def _score(sets: dict[str, _TermSet], title: str, summary: str) -> dict[str, int]:
    """3 per distinct term in the title's main clause, 1 per term in the rest of the title
    or in the summary."""
    parts = _SUBORDINATE.split(title, maxsplit=1)
    main, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    scores = {}
    for key, ts in sets.items():
        in_main = set(ts.find(main))
        scores[key] = 3 * len(in_main) + len(set(ts.find(rest)) - in_main) + len(ts.find(summary))
    return scores


def infer_category(title: str, summary: str, default_cat: str) -> str:
    """Highest-scoring category; a tie goes to the feed's default, else to the priority
    order; no term at all keeps the default."""
    scores = _score(_CATEGORY_SETS, _normalise_for_matching(title), _normalise_for_matching(summary))
    best = max(scores.values())
    if best == 0:
        return default_cat or "macro"
    tied = [c for c in _CATEGORY_PRIORITY if scores[c] == best]
    return default_cat if default_cat in tied else tied[0]


def infer_region(title: str, summary: str, default_region: str) -> str:
    """Highest-scoring region; a tie keeps the feed's default if it is among the tied,
    and is otherwise "global" (a story about several regions)."""
    scores = _score(_REGION_SETS, _normalise_for_matching(title), _normalise_for_matching(summary))
    best = max(scores.values())
    if best == 0:
        return default_region or "global"
    tied = [r for r, s in scores.items() if s == best]
    if default_region in tied:
        return default_region
    return tied[0] if len(tied) == 1 else "global"


_ACRONYM_STOPLIST = frozenset({
    "THE", "AND", "FOR", "NOT", "NEW", "TOP", "ALL", "OUT", "CEO", "CFO", "CTO", "PAN", "RC",
    "NO", "LTD", "INC", "PLC", "LIVE", "WATCH", "VIDEO", "NEWS", "OK", "TV", "AM", "PM",
    "IST", "GMT", "EST", "EDT", "ET", "UTC", "VS", "QA",
})


def extract_keywords(title: str, summary: str) -> list[str]:
    """Up to 8 chips: known terms from the title, then the summary, then title acronyms."""
    t, s = _normalise_for_matching(title), _normalise_for_matching(summary)
    found: dict[str, None] = {}
    for text in (t, s):
        for ts in _CATEGORY_SETS.values():
            for term in ts.find(text):
                found.setdefault(term.upper(), None)
    for match in re.findall(r"(?<![A-Za-z0-9])[A-Z]{2,6}(?![A-Za-z0-9])", title or ""):
        if match not in _ACRONYM_STOPLIST:
            found.setdefault(match, None)
    return list(found)[:8]


# --- Sentiment --------------------------------------------------------------------------
#
# Deliberately small and directional. "cut", "inflation", "high", "rise" and "down" are
# not unigrams any more: "rate cut" is not bad news, "inflation eases" is good news, and
# "prices rise" depends on who is asking. Phrases are scored first and masked out, so
# their words are not counted again as unigrams.

_POSITIVE_WORDS = frozenset({
    "rally", "rallies", "rallied", "rallying", "gain", "gains", "gained", "jump", "jumps",
    "jumped", "bullish", "boost", "boosts", "boosted", "outperform", "outperforms",
    "outperformed", "optimism", "optimistic", "recovery", "recovers", "recovered",
    "rebound", "rebounds", "rebounded", "upbeat", "strong", "stronger", "strongest",
    "upgrade", "upgrades", "upgraded", "climb", "climbs", "climbed", "lift", "lifts",
    "lifted", "positive", "record-high", "upside",
})
# The big moves count double: "crashes 6%" outweighs a passing "up 1%".
_STRONG_POSITIVE_WORDS = frozenset({
    "surge", "surges", "surged", "surging", "soar", "soars", "soared", "soaring",
    "skyrocket", "skyrockets", "skyrocketed",
})

_NEGATIVE_WORDS = frozenset({
    "fall", "falls", "fell", "falling", "drop", "drops", "dropped", "dropping", "decline",
    "declines", "declined", "declining", "loss", "losses", "war", "crisis", "bearish",
    "fear", "fears", "sink", "sinks", "sank", "sinking", "recession", "downturn", "default",
    "defaults", "defaulted", "warning", "warns", "warned", "slowdown", "weak", "weaker",
    "weakens", "weakened", "weakness", "slide", "slides", "slid", "sliding", "downgrade",
    "downgrades", "downgraded", "selloff", "slip", "slips", "slipped", "bankruptcy",
    "bankrupt", "layoffs", "shock", "turmoil", "fraud", "worries", "worry", "concerns",
    "negative", "record-low", "fined", "failure", "failures", "threat", "threats",
    "rattle", "rattles", "rattled", "underperform", "underperforms", "underperformance",
    "curbs",
})
_STRONG_NEGATIVE_WORDS = frozenset({
    "crash", "crashes", "crashed", "plunge", "plunges", "plunged", "plunging", "collapse",
    "collapses", "collapsed", "tumble", "tumbles", "tumbled", "slump", "slumps", "slumped",
})

_NEGATORS = frozenset({"no", "not", "never", "without", "avoid", "avoids", "avoided", "averts", "averted"})

_INFLATION_SUBJECT = r"(?:inflation|cpi|wpi|price pressures?|price growth)"
_EASE = (r"(?:eases?|eased|easing|cools?|cooled|cooling|slows?|slowed|slowing|moderates?|moderated|"
         r"moderating|softens?|softened|falls?|fell|dips?|dipped|declines?|declined|drops?|dropped|retreats?)")
_HEAT = (r"(?:rises?|rose|rising|jumps?|jumped|accelerates?|accelerated|accelerating|surges?|surged|"
         r"spikes?|spiked|hot|hotter|heats? up|climbs?|climbed|picks? up|quickens?|quickened|"
         r"hits? (?:a )?(?:record|\d+-(?:year|month)|multi-year) high)")
_UP_MOVE = r"(?:close[sd]?|end(?:s|ed)?|finish(?:es|ed)?|trade[sd]?|open(?:s|ed)?|settle[sd]?)"

_SENTIMENT_PHRASES: tuple[tuple[re.Pattern, int], ...] = tuple(
    (re.compile(p, re.IGNORECASE), w)
    for p, w in (
        # Not news about direction: masked out so their words do not count either way.
        (r"\brate (?:cut|hike)s?\b", 0),
        (r"\bcut-offs?\b", 0),
        (r"\bprices? (?:set to |to |could |may |will )?(?:rise|rises|rising|rose)\b", 0),
        # Inflation: easing is good news for markets, heating up is bad.
        (rf"\b{_INFLATION_SUBJECT}\b[^.;:]{{0,30}}?\b{_EASE}\b", +1),
        (rf"\b(?:easing|cooling|cooler|softer|slower|lower)\s+{_INFLATION_SUBJECT}\b", +1),
        (rf"\b{_INFLATION_SUBJECT}\b[^.;:]{{0,30}}?\b{_HEAT}", -1),
        (rf"\b(?:hot|hotter|sticky|rising|stubborn|higher)\s+{_INFLATION_SUBJECT}\b", -1),
        # Jobs.
        (r"\b(?:unemployment|jobless(?: rate| claims)?)\b[^.;:]{0,25}?\b(?:rises?|rose|jumps?|jumped|climbs?|climbed|surges?|surged)\b", -1),
        (r"\b(?:unemployment|jobless(?: rate| claims)?)\b[^.;:]{0,25}?\b(?:falls?|fell|drops?|dropped|declines?|declined|eases?|eased)\b", +1),
        # Growth.
        (r"\b(?:growth|gdp|economy|output)\b[^.;:]{0,25}?\b(?:slows?|slowed|slowing|contracts?|contracted|shrinks?|shrank|weakens?|weakened|stalls?|stalled)\b", -1),
        (r"\b(?:growth|gdp|economy|output)\b[^.;:]{0,25}?\b(?:accelerates?|accelerated|beats?|rebounds?|rebounded|expands?|expanded|picks? up|strengthens?)\b", +1),
        # Market moves with a number: "down 2%", "up 12%".
        (r"\bdown\s+(?:by\s+)?\d[\d.,]*\s*(?:%|per ?cent|pts?|points|bps)", -1),
        (r"\bup\s+(?:by\s+)?\d[\d.,]*\s*(?:%|per ?cent|pts?|points|bps)", +1),
        (rf"\b{_UP_MOVE}\s+(?:sharply\s+|slightly\s+|marginally\s+|modestly\s+)?higher\b", +1),
        (rf"\b{_UP_MOVE}\s+(?:sharply\s+|slightly\s+|marginally\s+|modestly\s+)?lower\b", -1),
        # Jobs and outlooks being cut.
        (r"\b(?:job|jobs|workforce)\s+cuts?\b", -1),
        (r"\bcuts?\s+(?:\d[\d,]*\s+)?(?:jobs|staff|workers|forecasts?|outlook|guidance)\b", -1),
        (r"\blay(?:s|ing)?\s+off\b", -1),
        (r"\bsell[- ]?offs?\b|\bsell(?:s|ing)?\s+off\b", -1),
        (r"\b(?:beats?|tops?)\s+(?:estimates|expectations|forecasts)\b", +1),
        (r"\bmiss(?:es|ed)?\s+(?:estimates|expectations|forecasts)\b", -1),
        # Brokers moving targets.
        (r"\b(?:raises?|raised|hikes?|hiked|lifts?|lifted|ups)\b[^.;:,|]{0,30}?\btargets?\b", +1),
        (r"\b(?:cuts?|lowers?|lowered|slashes?|slashed|trims?|trimmed)\b[^.;:,|]{0,30}?\btargets?\b", -1),
        # Costs going up is bad for whoever pays them.
        (r"\bcosts?\s+(?:soar|soars|soared|surge|surges|surged|jump|jumps|jumped|spike|spikes|spiked|rise|rises|rose)\b", -1),
        (r"\bat risk\b|\bbad news\b", -1),
        (r"\bgood news\b", +1),
    )
)
# "X jumps 7% despite a 19% fall this year": the news is the main clause.
_CONCESSIVE = re.compile(r"\b(?:despite|in spite of|notwithstanding)\b", re.IGNORECASE)
# Highs and lows mean the opposite when the subject is inflation; those cases are left
# to the inflation phrases above.
_HIGH_LOW_PHRASES: tuple[tuple[re.Pattern, int], ...] = (
    (re.compile(r"\b(?:record|all-time|fresh|lifetime|\d+-(?:year|month|week)|multi-year)\s+highs?\b", re.IGNORECASE), +1),
    (re.compile(r"\b(?:record|all-time|fresh|lifetime|\d+-(?:year|month|week)|multi-year)\s+lows?\b", re.IGNORECASE), -1),
)
_INFLATION_SUBJECT_RE = re.compile(rf"\b{_INFLATION_SUBJECT}\b", re.IGNORECASE)


def _sentiment_score(text: str) -> int:
    if not text:
        return 0
    text = _CONCESSIVE.split(text, maxsplit=1)[0]
    score = 0
    masked = text
    phrases = _SENTIMENT_PHRASES
    if not _INFLATION_SUBJECT_RE.search(text):
        phrases = phrases + _HIGH_LOW_PHRASES
    for rx, weight in phrases:
        def _mask(m: re.Match) -> str:
            nonlocal score
            score += weight
            return " " * len(m.group(0))
        masked = rx.sub(_mask, masked)
    # Hyphenated words stay whole, so "cut-off" and "sell-off" are not "cut" and "sell".
    tokens = re.findall(r"[a-z]+(?:[-'][a-z]+)*", masked.lower())
    for i, tok in enumerate(tokens):
        if i and tokens[i - 1] in _NEGATORS:
            continue
        if tok in _STRONG_POSITIVE_WORDS:
            score += 2
        elif tok in _POSITIVE_WORDS:
            score += 1
        elif tok in _STRONG_NEGATIVE_WORDS:
            score -= 2
        elif tok in _NEGATIVE_WORDS:
            score -= 1
    return score


def classify_sentiment(title: str, summary: str) -> str:
    """Positive, negative or neutral for a headline; the title counts double, and only the
    start of the summary is read (long bodies drift off topic)."""
    score = 2 * _sentiment_score(title) + _sentiment_score((summary or "")[:300])
    if score > 0:
        return "positive"
    if score < 0:
        return "negative"
    return "neutral"


class Labels(NamedTuple):
    category: str
    region: str
    sentiment: str
    keywords: list[str]


def classify_article(title: str, summary: str, feed: Feed) -> Labels:
    if feed.official:
        category, region, sentiment = feed.category, feed.region, "neutral"
    else:
        category = feed.category if feed.fixed_category else infer_category(title, summary, feed.category)
        region = infer_region(title, summary, feed.region)
        sentiment = classify_sentiment(title, summary)
    return Labels(category, region, sentiment, extract_keywords(title, summary))


# --- Dates ------------------------------------------------------------------------------

_GENERIC_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d %b %Y %H:%M:%S",
    "%d %b %Y",
    "%d-%m-%Y %H:%M:%S",
)


def _parse_one_date(s: str, date_formats: Sequence[str]) -> Optional[datetime.datetime]:
    for fmt in date_formats:
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return email.utils.parsedate_to_datetime(s)  # naive when the string has no zone
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in _GENERIC_DATE_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_date_ladder(
    date_strs: Sequence[Optional[str]],
    tz: str = "UTC",
    date_formats: Sequence[str] = (),
) -> Optional[datetime.datetime]:
    """The first parseable date among the candidates, as an aware UTC datetime.

    A value with no zone is read in the feed's own `tz` (RBI's "Thu, 15 Oct 2026 09:00:00"
    is IST). No parseable date returns None: there is no fallback to the HTTP
    Last-Modified time or to now, which would stamp a whole backlog with one time and
    float it to the top of a time-ordered feed."""
    zone = ZoneInfo(tz)
    for raw in date_strs:
        s = (raw or "").strip()
        if not s:
            continue
        dt = _parse_one_date(s, date_formats)
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=zone)
        return dt.astimezone(UTC)
    return None


def _to_local_midnight(dt: datetime.datetime, tz: str) -> datetime.datetime:
    zone = ZoneInfo(tz)
    local = dt.astimezone(zone)
    return datetime.datetime.combine(local.date(), datetime.time(0), zone).astimezone(UTC)


# --- XML --------------------------------------------------------------------------------

_CDATA_SPLIT = re.compile(r"(<!\[CDATA\[.*?\]\]>)", re.DOTALL)
_BARE_AMPERSAND = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")


def preclean_xml(xml_text: str) -> str:
    """Strips a BOM and escapes bare ampersands outside CDATA sections.

    Used only when the strict parse fails, and the only repair attempted: a feed that is
    still not well-formed after it is rejected, not salvaged item by item. CDATA is left
    alone because "&nbsp;" inside it is already literal text that the HTML cleaner
    decodes; escaping it would surface a literal "&nbsp;"."""
    xml_text = xml_text.lstrip("﻿").strip()
    parts = _CDATA_SPLIT.split(xml_text)
    return "".join(p if p.startswith("<![CDATA[") else _BARE_AMPERSAND.sub("&amp;", p) for p in parts)


class FeedError(Exception):
    """A fetch or parse failure. The message is what /api/news/sources shows."""

    def __init__(self, message: str, http_status: Optional[int] = None, retry_after: Optional[float] = None):
        super().__init__(message)
        self.http_status = http_status
        self.retry_after = retry_after


def _decode(body: bytes, charset: Optional[str]) -> str:
    m = re.match(rb"\s*<\?xml[^>]*encoding=[\"']([A-Za-z0-9._-]+)[\"']", body[:200])
    for enc in (charset, m.group(1).decode("ascii") if m else None, "utf-8"):
        if not enc:
            continue
        try:
            return body.decode(enc, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")


def parse_feed_document(content: bytes | str, charset: Optional[str] = None) -> ET.Element:
    """Strict XML parse; on failure, one retry after preclean_xml, else FeedError."""
    try:
        return ET.fromstring(content)
    except ET.ParseError as first:
        text = content if isinstance(content, str) else _decode(content, charset)
        try:
            return ET.fromstring(preclean_xml(text))
        except ET.ParseError:
            raise FeedError(f"malformed XML ({first})") from None


def _local_tag(elem: ET.Element) -> str:
    return elem.tag.split("}")[-1].lower() if isinstance(elem.tag, str) else ""


_DATE_TAG_PRIORITY = ("pubdate", "published", "date", "issued", "updated", "modified")
_SUMMARY_TAG_PRIORITY = ("description", "summary", "encoded", "content")
_IGNORED_ATOM_RELS = frozenset({"self", "enclosure", "edit", "replies", "via", "related"})


class _ItemFields(NamedTuple):
    title: str
    link: str
    summary: str
    dates: list[str]


def _item_fields(elem: ET.Element) -> _ItemFields:
    title: Optional[str] = None
    rss_link: Optional[str] = None
    atom_alternate: Optional[str] = None
    atom_other: Optional[str] = None
    guid: Optional[str] = None
    summaries: dict[str, str] = {}
    dates: dict[str, str] = {}
    for child in elem:
        tag = _local_tag(child)
        text = child.text or ""
        if tag == "title":
            if title is None:
                title = text
        elif tag == "link":
            href = child.attrib.get("href")
            if href is not None:
                # Atom: several <link>s; the article is rel="alternate" (or no rel).
                rel = (child.attrib.get("rel") or "alternate").lower()
                if rel == "alternate":
                    atom_alternate = atom_alternate or href
                elif rel not in _IGNORED_ATOM_RELS:
                    atom_other = atom_other or href
            elif text.strip() and rss_link is None:
                rss_link = text
        elif tag == "guid":
            guid = text
        elif tag in _SUMMARY_TAG_PRIORITY:
            if text.strip():
                summaries.setdefault(tag, text)
        elif tag in _DATE_TAG_PRIORITY:
            if text.strip():
                dates.setdefault(tag, text)
    link = rss_link or atom_alternate or atom_other or ""
    if not link and guid and guid.strip().lower().startswith(("http://", "https://")):
        link = guid
    summary = next((summaries[t] for t in _SUMMARY_TAG_PRIORITY if t in summaries), "")
    return _ItemFields(title or "", link.strip(), summary, [dates[t] for t in _DATE_TAG_PRIORITY if t in dates])


@dataclasses.dataclass
class ParsedFeed:
    articles: list[dict]
    items_parsed: int = 0
    newest_item_at: Optional[datetime.datetime] = None
    dropped: dict[str, int] = dataclasses.field(default_factory=dict)


def _is_excluded(feed: Feed, title: str, url: str) -> bool:
    if any(rx.search(title) for rx in _compile_patterns(feed.exclude_title_patterns)):
        return True
    return any(rx.search(url) for rx in _compile_patterns(feed.exclude_url_patterns))


def parse_feed_xml(
    content: bytes | str,
    feed: Feed,
    fetched_at: Optional[datetime.datetime] = None,
    charset: Optional[str] = None,
) -> ParsedFeed:
    """Parses one feed document into articles, applying the feed's rules.

    Raises FeedError when the document is not well-formed XML."""
    fetched_at = fetched_at or datetime.datetime.now(UTC)
    root = parse_feed_document(content, charset)
    items = [e for e in root.iter() if _local_tag(e) in ("item", "entry")][: feed.max_items]
    out = ParsedFeed(articles=[], items_parsed=len(items))
    oldest_allowed = fetched_at - datetime.timedelta(days=NEWS_RETENTION_DAYS)

    def drop(reason: str) -> None:
        out.dropped[reason] = out.dropped.get(reason, 0) + 1

    for elem in items:
        fields = _item_fields(elem)
        title = clean_title_text(fields.title, feed.source)
        if not title:
            drop("no_title")
            continue
        if not validate_article_url(fields.link):
            drop("bad_link")
            continue
        published = parse_date_ladder(fields.dates, feed.tz, feed.date_formats)
        if published is None:
            drop("undated")
            continue
        if feed.date_only:
            published = _to_local_midnight(published, feed.tz)
        if published > fetched_at + FUTURE_TOLERANCE:
            logger.warning(
                "News feed %s: dropped an item dated %s, %s ahead of the fetch (a time zone problem?): %r",
                feed.name, published.isoformat(), published - fetched_at, fields.dates[:1],
            )
            drop("future")
            continue
        published = min(published, fetched_at)
        if out.newest_item_at is None or published > out.newest_item_at:
            out.newest_item_at = published
        if published < oldest_allowed:
            drop("too_old")
            continue
        url = canonicalize_url(fields.link)
        if _is_excluded(feed, title, url):
            drop("excluded")
            continue
        summary = _clean_summary(fields.summary, title, feed)
        labels = classify_article(title, summary, feed)
        out.articles.append({
            "id": hashlib.md5(url.encode("utf-8")).hexdigest(),
            "title": title,
            "summary": summary,
            "source": feed.source,
            "feed": feed.name,
            "official": feed.official,
            "url": url,
            "category": labels.category,
            "region": labels.region,
            "published_at": published,
            "date_only": feed.date_only,
            "sentiment": labels.sentiment,
            "keywords": labels.keywords,
            "title_signature": compute_title_signature(title),
        })
    return out


# --- Fetching ---------------------------------------------------------------------------


class HttpResult(NamedTuple):
    status: int
    body: Optional[bytes]  # None on 304 Not Modified
    etag: Optional[str]
    last_modified: Optional[str]
    charset: Optional[str]


def _retry_after_seconds(headers: Any) -> Optional[float]:
    value = (headers.get("Retry-After") if headers is not None else None) or ""
    if value.strip().isdigit():
        return float(value.strip())
    return None


def _gunzip_capped(body: bytes) -> bytes:
    try:
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        out = d.decompress(body, MAX_FEED_BYTES + 1)
    except zlib.error as e:
        raise FeedError(f"bad gzip body ({e})") from None
    if len(out) > MAX_FEED_BYTES or d.unconsumed_tail:
        raise FeedError(f"decompressed body larger than {MAX_FEED_BYTES} bytes")
    return out


def _http_get(feed: Feed, etag: Optional[str] = None, last_modified: Optional[str] = None) -> HttpResult:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": ("application/rss+xml, application/atom+xml, application/rdf+xml, "
                   "application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1"),
        "Accept-Encoding": "gzip",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    req = urllib.request.Request(feed.url, headers=headers)
    deadline = time.monotonic() + FEED_DEADLINE_SECONDS
    try:
        resp = urllib.request.urlopen(req, timeout=FEED_SOCKET_TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return HttpResult(304, None, etag, last_modified, None)
        raise FeedError(f"HTTP {e.code} {e.reason}", http_status=e.code,
                        retry_after=_retry_after_seconds(e.headers)) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", None) or e
        raise FeedError(f"network error: {reason}") from None
    with resp:
        declared = (resp.headers.get("Content-Length") or "").strip()
        if declared.isdigit() and int(declared) > MAX_FEED_BYTES:
            raise FeedError(f"response too large ({declared} bytes)", http_status=resp.status)
        read = getattr(resp, "read1", None) or resp.read  # read1: one socket read, so the deadline is checked often
        chunks: list[bytes] = []
        total = 0
        while True:
            if time.monotonic() > deadline:
                raise FeedError(f"took longer than {FEED_DEADLINE_SECONDS:.0f}s", http_status=resp.status)
            try:
                chunk = read(65536)
            except OSError as e:
                raise FeedError(f"network error while reading: {e}", http_status=resp.status) from None
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FEED_BYTES:
                raise FeedError(f"response larger than {MAX_FEED_BYTES} bytes", http_status=resp.status)
            chunks.append(chunk)
        body = b"".join(chunks)
        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
            body = _gunzip_capped(body)
        return HttpResult(resp.status, body, resp.headers.get("ETag"), resp.headers.get("Last-Modified"),
                          resp.headers.get_content_charset())


@dataclasses.dataclass
class FetchOutcome:
    feed: Feed
    ok: bool
    not_modified: bool = False
    skipped: bool = False  # in backoff: not attempted this sweep
    http_status: Optional[int] = None
    error: Optional[str] = None
    retry_after: Optional[float] = None
    articles: list[dict] = dataclasses.field(default_factory=list)
    items_parsed: int = 0
    newest_item_at: Optional[datetime.datetime] = None
    dropped: dict[str, int] = dataclasses.field(default_factory=dict)
    etag: Optional[str] = None
    last_modified: Optional[str] = None


def fetch_feed(feed: Feed, etag: Optional[str] = None, last_modified: Optional[str] = None) -> FetchOutcome:
    """Fetches and parses one feed. Never raises: every failure is an outcome."""
    fetched_at = datetime.datetime.now(UTC)
    try:
        res = _http_get(feed, etag, last_modified)
        if res.status == 304:
            return FetchOutcome(feed, ok=True, not_modified=True, http_status=304, etag=etag, last_modified=last_modified)
        parsed = parse_feed_xml(res.body or b"", feed, fetched_at, res.charset)
        if parsed.items_parsed == 0:
            raise FeedError("answered, but with no items", http_status=res.status)
        if parsed.newest_item_at is None and not parsed.dropped.get("future"):
            # Every item undated (or unusable): a date-format problem, not a quiet feed.
            raise FeedError(f"none of its {parsed.items_parsed} items was usable ({parsed.dropped})",
                            http_status=res.status)
        return FetchOutcome(
            feed, ok=True, http_status=res.status, articles=parsed.articles,
            items_parsed=parsed.items_parsed, newest_item_at=parsed.newest_item_at,
            dropped=parsed.dropped, etag=res.etag, last_modified=res.last_modified,
        )
    except FeedError as e:
        return FetchOutcome(feed, ok=False, http_status=e.http_status, error=str(e), retry_after=e.retry_after)
    except Exception as e:  # a parser bug must cost one feed, not the sweep
        logger.exception("News feed %s: unexpected error", feed.name)
        return FetchOutcome(feed, ok=False, error=f"unexpected error: {type(e).__name__}: {e}")


# --- Per-feed health --------------------------------------------------------------------


@dataclasses.dataclass
class FeedStatus:
    # "pending" (not tried since the backend started), "ok", "stale" (answers, but its
    # newest item is older than the feed's stale_after_days), "failing", or "backoff"
    # (failing, and skipped until backoff_until).
    state: str = "pending"
    last_attempt_at: Optional[datetime.datetime] = None
    last_ok_at: Optional[datetime.datetime] = None
    http_status: Optional[int] = None
    items_parsed: int = 0
    items_kept: int = 0
    items_new: int = 0
    newest_item_at: Optional[datetime.datetime] = None
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    backoff_until: Optional[datetime.datetime] = None
    dropped: dict[str, int] = dataclasses.field(default_factory=dict)
    etag: Optional[str] = None
    last_modified: Optional[str] = None


_STATUS_LOCK = threading.Lock()
_FEED_STATUS: dict[str, FeedStatus] = {f.name: FeedStatus() for f in FEEDS}


def _status_for(name: str) -> FeedStatus:
    with _STATUS_LOCK:
        return _FEED_STATUS.setdefault(name, FeedStatus())


def _backoff_seconds(failures: int, http_status: Optional[int], retry_after: Optional[float]) -> float:
    """None before the second failure in a row (one blip retries next sweep); then longer
    than a sweep interval, doubling, capped."""
    seconds = 0.0
    if failures >= 2:
        seconds = min(BACKOFF_BASE_SECONDS * 2 ** (failures - 2), BACKOFF_MAX_SECONDS)
    if http_status == 403:
        seconds = max(seconds, 3600.0)
    if http_status in (429, 503) and retry_after:
        seconds = max(seconds, min(retry_after, BACKOFF_MAX_SECONDS))
    return seconds


def _iso(dt: Optional[datetime.datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _age(dt: Optional[datetime.datetime], now: datetime.datetime) -> str:
    if dt is None:
        return "-"
    hours = (now - dt).total_seconds() / 3600
    return f"{hours:.1f}h" if hours < 72 else f"{hours / 24:.0f}d"


def _record_outcomes(outcomes: Sequence[FetchOutcome], db_ok: bool, new_by_feed: dict[str, int]) -> tuple[int, int]:
    """Updates per-feed health and logs one line per feed. Returns (feeds ok, feeds that
    answered at all, stale ones included)."""
    now = datetime.datetime.now(UTC)
    ok = answered = 0
    with _STATUS_LOCK:
        for out in outcomes:
            feed = out.feed
            st = _FEED_STATUS.setdefault(feed.name, FeedStatus())
            if out.skipped:
                st.state = "backoff"
                logger.info("News feed %-26s backoff until %s (%s)", feed.name, _iso(st.backoff_until), st.last_error)
                continue
            st.last_attempt_at = now
            st.http_status = out.http_status
            if out.ok:
                st.consecutive_failures = 0
                st.backoff_until = None
                st.last_error = None
                st.last_ok_at = now
                st.items_new = new_by_feed.get(feed.name, 0)
                if not out.not_modified:
                    st.items_parsed = out.items_parsed
                    st.items_kept = len(out.articles)
                    st.newest_item_at = out.newest_item_at
                    st.dropped = dict(out.dropped)
                    if db_ok:
                        # Only once the items are stored: a 304 must never hide items we lost.
                        st.etag, st.last_modified = out.etag, out.last_modified
                stale_before = now - datetime.timedelta(days=feed.stale_after_days)
                st.state = "stale" if st.newest_item_at and st.newest_item_at < stale_before else "ok"
                answered += 1
                if st.state == "ok":
                    ok += 1
                logger.info(
                    "News feed %-26s %-5s http=%s parsed=%d kept=%d new=%d newest=%s dropped=%s",
                    feed.name, st.state, out.http_status, out.items_parsed, len(out.articles),
                    st.items_new, _age(st.newest_item_at, now), out.dropped or "-",
                )
            else:
                st.consecutive_failures += 1
                st.last_error = out.error
                st.state = "failing"
                st.items_new = 0
                wait = _backoff_seconds(st.consecutive_failures, out.http_status, out.retry_after)
                st.backoff_until = now + datetime.timedelta(seconds=wait) if wait else None
                logger.warning(
                    "News feed %-26s FAILED (%d in a row) http=%s: %s%s",
                    feed.name, st.consecutive_failures, out.http_status, out.error,
                    f"; backing off until {st.backoff_until:%H:%M} UTC" if st.backoff_until else "",
                )
    return ok, answered


def fetch_all_feeds(feeds: Optional[Sequence[Feed]] = None) -> list[FetchOutcome]:
    """Fetches every feed not in backoff, in parallel, within SWEEP_DEADLINE_SECONDS."""
    feeds = list(feeds if feeds is not None else FEEDS)
    now = datetime.datetime.now(UTC)
    outcomes: dict[str, FetchOutcome] = {}
    jobs: list[tuple[Feed, Optional[str], Optional[str]]] = []
    for feed in feeds:
        st = _status_for(feed.name)
        if st.backoff_until and st.backoff_until > now:
            outcomes[feed.name] = FetchOutcome(feed, ok=False, skipped=True)
        else:
            jobs.append((feed, st.etag, st.last_modified))

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=FETCH_WORKERS, thread_name_prefix="news-fetch")
    try:
        futures = {executor.submit(fetch_feed, feed, etag, lm): feed for feed, etag, lm in jobs}
        try:
            for fut in concurrent.futures.as_completed(futures, timeout=SWEEP_DEADLINE_SECONDS):
                feed = futures[fut]
                outcomes[feed.name] = fut.result()
        except concurrent.futures.TimeoutError:
            for fut, feed in futures.items():
                if feed.name not in outcomes:
                    fut.cancel()
                    outcomes[feed.name] = FetchOutcome(
                        feed, ok=False, error=f"no answer within the {SWEEP_DEADLINE_SECONDS:.0f}s sweep deadline")
    finally:
        # A worker stuck in a socket read finishes (or times out) on its own; the sweep
        # does not wait for it, and its late result is discarded.
        executor.shutdown(wait=False, cancel_futures=True)
    return [outcomes[f.name] for f in feeds]


# --- Storing ----------------------------------------------------------------------------


@dataclasses.dataclass
class _Candidate:
    id: str
    cluster_id: str
    source: str
    signature: str
    tokens: frozenset[str]
    numbers: frozenset[str]
    published_at: datetime.datetime


def _candidate(id_: str, cluster_id: Optional[str], source: str, signature: Optional[str], title: str,
               published_at: datetime.datetime) -> _Candidate:
    return _Candidate(id_, cluster_id or id_, source, signature or "", frozenset(title_tokens(title)),
                      _title_numbers(title), published_at)


def _title_similarity(a: _Candidate, b: _Candidate) -> float:
    """Share of content words in common (Jaccard), or 0 when either title is too short to
    judge or the numbers conflict ("25 bps" vs "50 bps" are different stories)."""
    if len(a.tokens) < NEAR_DUP_MIN_TOKENS or len(b.tokens) < NEAR_DUP_MIN_TOKENS:
        return 0.0
    if a.numbers and b.numbers and a.numbers != b.numbers:
        return 0.0
    union = len(a.tokens | b.tokens)
    return len(a.tokens & b.tokens) / union if union else 0.0


def _assign_clusters(new_articles: list[dict], stored: list[_Candidate]) -> tuple[list[dict], int, int]:
    """Gives each new article a cluster_id, or drops it as a duplicate.

    - Same source, same title signature (or a reworded headline, SAME_SOURCE_NEAR_DUP_JACCARD),
      published within SAME_SOURCE_DUP_WINDOW: the same article at another URL, or an old
      item resurfacing. Dropped.
    - Another source, same signature or near-duplicate title, within NEAR_DUP_WINDOW: the
      same story. Joins that story's cluster, so it shows as one card with "+N sources".
    - Otherwise: a cluster of its own.

    The signature check spans the whole retention window (the stored candidates include
    every row with a matching signature), keyed on publication time rather than on "now".
    Returns (articles to insert, duplicates dropped, articles merged into a cluster)."""
    pool: list[_Candidate] = list(stored)
    by_sig: dict[str, list[int]] = {}
    by_token: dict[str, list[int]] = {}

    def index(i: int) -> None:
        c = pool[i]
        if c.signature:
            by_sig.setdefault(c.signature, []).append(i)
        for tok in c.tokens:
            by_token.setdefault(tok, []).append(i)

    for i in range(len(pool)):
        index(i)

    kept: list[dict] = []
    duplicates = merged = 0
    # Primary sources first, then oldest first, so a cluster's first member is the original.
    for art in sorted(new_articles, key=lambda a: (not a.get("official"), a["published_at"])):
        me = _candidate(art["id"], None, art["source"], art["title_signature"], art["title"], art["published_at"])
        duplicate = False
        match: Optional[_Candidate] = None
        for i in by_sig.get(me.signature, []) if me.signature else []:
            c = pool[i]
            gap = abs(me.published_at - c.published_at)
            if c.source == me.source:
                if gap <= SAME_SOURCE_DUP_WINDOW:
                    duplicate = True
                    break
            elif gap <= NEAR_DUP_WINDOW and match is None:
                match = c
        if not duplicate and len(me.tokens) >= NEAR_DUP_MIN_TOKENS:
            shared: dict[int, int] = {}
            for tok in me.tokens:
                for i in by_token.get(tok, ()):
                    shared[i] = shared.get(i, 0) + 1
            need = max(2, math.ceil(NEAR_DUP_JACCARD * len(me.tokens)) - 1)
            for i, n in sorted(shared.items(), key=lambda kv: -kv[1]):
                if n < need:
                    break
                c = pool[i]
                gap = abs(me.published_at - c.published_at)
                if c.source == me.source:
                    if gap <= SAME_SOURCE_DUP_WINDOW and _title_similarity(me, c) >= SAME_SOURCE_NEAR_DUP_JACCARD:
                        duplicate = True
                        break
                elif match is None and gap <= NEAR_DUP_WINDOW and _title_similarity(me, c) >= NEAR_DUP_JACCARD:
                    match = c
        if duplicate:
            duplicates += 1
            continue
        art["cluster_id"] = match.cluster_id if match else art["id"]
        if match:
            merged += 1
        me.cluster_id = art["cluster_id"]
        pool.append(me)
        index(len(pool) - 1)
        kept.append(art)
    return kept, duplicates, merged


_UPSERT_SQL = """
INSERT INTO news_articles (
    id, title, summary, source, url, category, region, published_at, sentiment, keywords,
    title_signature, fetched_at, cluster_id, feed, date_only, classifier_version
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title,
    summary = EXCLUDED.summary,
    category = EXCLUDED.category,
    region = EXCLUDED.region,
    sentiment = EXCLUDED.sentiment,
    keywords = EXCLUDED.keywords,
    title_signature = EXCLUDED.title_signature,
    feed = EXCLUDED.feed,
    date_only = EXCLUDED.date_only,
    classifier_version = EXCLUDED.classifier_version,
    fetched_at = EXCLUDED.fetched_at
RETURNING id, (xmax = 0) AS inserted
"""


def _row(art: dict, fetched_at: datetime.datetime) -> tuple:
    return (
        art["id"], art["title"], art["summary"], art["source"], art["url"], art["category"],
        art["region"], art["published_at"], art["sentiment"], art.get("keywords") or [],
        art.get("title_signature"), fetched_at, art.get("cluster_id") or art["id"],
        art.get("feed"), bool(art.get("date_only")), CLASSIFIER_VERSION,
    )


def _store_articles(articles: list[dict], fetched_at: datetime.datetime) -> dict:
    """Dedupes, clusters and upserts one sweep's articles in a single transaction.

    Rows already stored are refreshed (title, summary, labels, fetched_at) but keep their
    first-seen published_at and their cluster."""
    unique: dict[str, dict] = {}
    for art in articles:
        unique.setdefault(art["id"], art)

    con = get_connection()
    try:
        ids = list(unique)
        existing = {r[0] for r in con.execute("SELECT id FROM news_articles WHERE id = ANY(%s)", (ids,)).fetchall()}
        new = [a for a in unique.values() if a["id"] not in existing]
        stored: list[_Candidate] = []
        if new:
            sigs = sorted({a["title_signature"] for a in new if a["title_signature"]})
            lo = min(a["published_at"] for a in new) - NEAR_DUP_WINDOW
            hi = max(a["published_at"] for a in new) + NEAR_DUP_WINDOW
            rows = con.execute(
                "SELECT id, cluster_id, source, title_signature, title, published_at FROM news_articles "
                "WHERE title_signature = ANY(%s) OR published_at BETWEEN %s AND %s",
                (sigs, lo, hi),
            ).fetchall()
            stored = [_candidate(*r) for r in rows]
        kept_new, duplicates, merged = _assign_clusters(new, stored)

        rows = [_row(a, fetched_at) for a in kept_new] + [_row(unique[i], fetched_at) for i in ids if i in existing]
        results: list[tuple[str, bool]] = []
        if rows:
            con.begin()
            cur = con.raw_connection.cursor()
            try:
                cur.executemany(_UPSERT_SQL, rows, returning=True)
                while True:
                    r = cur.fetchone()
                    if r is not None:
                        results.append((r[0], bool(r[1])))
                    if not cur.nextset():
                        break
            finally:
                cur.close()
            con.commit()
    finally:
        con.close()

    feed_of = {a["id"]: a.get("feed") for a in unique.values()}
    new_by_feed: dict[str, int] = {}
    for id_, inserted in results:
        if inserted:
            name = feed_of.get(id_) or ""
            new_by_feed[name] = new_by_feed.get(name, 0) + 1
    inserted_count = sum(1 for _, ins in results if ins)
    return {
        "inserted": inserted_count,
        "updated": len(results) - inserted_count,
        "duplicates": duplicates,
        "clustered": merged,
        "new_by_feed": new_by_feed,
    }


def prune_old_articles() -> int:
    """Deletes rows past the retention window. Runs on every sweep, whatever the sweep
    found, so the window holds even while every feed is down."""
    con = get_connection()
    try:
        con.execute(
            "DELETE FROM news_articles WHERE published_at < now() - make_interval(days => %s)",
            (NEWS_RETENTION_DAYS,),
        )
        return con.rowcount or 0
    finally:
        con.close()


def relabel_outdated_rows(batch: int = 5000) -> int:
    """Re-cleans and reclassifies stored rows from an older classifier_version, and deletes
    the ones today's feed rules exclude. Returns rows changed."""
    con = get_connection()
    try:
        rows = con.execute(
            "SELECT id, title, summary, source, url, feed FROM news_articles "
            "WHERE classifier_version < %s LIMIT %s",
            (CLASSIFIER_VERSION, batch),
        ).fetchall()
        if not rows:
            return 0
        updates, deletes = [], []
        for id_, title, summary, source, url, feed_name in rows:
            feed = _FEED_BY_NAME.get(feed_name or "") or _FEED_BY_SOURCE.get(source) or _fallback_feed(source)
            new_title = clean_title_text(title, source) or title
            if feed.name and _is_excluded(feed, new_title, url):
                deletes.append(id_)
                continue
            new_summary = _clean_summary(summary or "", new_title, feed)
            labels = classify_article(new_title, new_summary, feed)
            updates.append((
                new_title, new_summary, labels.category, labels.region, labels.sentiment,
                labels.keywords, compute_title_signature(new_title), feed.name or feed_name,
                CLASSIFIER_VERSION, id_,
            ))
        con.begin()
        if deletes:
            con.execute("DELETE FROM news_articles WHERE id = ANY(%s)", (deletes,))
        if updates:
            con.executemany(
                "UPDATE news_articles SET title = %s, summary = %s, category = %s, region = %s, "
                "sentiment = %s, keywords = %s, title_signature = %s, feed = %s, classifier_version = %s "
                "WHERE id = %s",
                updates,
            )
        con.commit()
        logger.info("News relabel: %d rows reclassified, %d deleted by feed rules.", len(updates), len(deletes))
        return len(updates) + len(deletes)
    finally:
        con.close()


# --- Sweep ------------------------------------------------------------------------------

# Single-flight: the daemon waits for it, a manual refresh does not (it answers "busy").
_INGEST_LOCK = threading.Lock()
_SWEEP_STATE_LOCK = threading.Lock()
_LAST_SWEEP: dict[str, Any] = {"finished_mono": None, "finished_at": None, "result": None}
_LAST_OK_AT: Optional[datetime.datetime] = None  # last sweep with >= 1 feed ok and the DB written
_LAST_OK_SEEDED = False  # seeded once from max(fetched_at), so a restart is not "never updated"
_NEXT_SWEEP_AT: Optional[datetime.datetime] = None


def _safely(what: str, fn) -> int:
    try:
        return fn()
    except Exception as e:
        logger.error("News %s failed: %s", what, e)
        return 0


def ingest_and_store_feeds(blocking: bool = True, min_interval: Optional[float] = None) -> dict:
    """Runs one sweep, unless one is running (non-blocking callers get status "busy") or
    one finished within `min_interval` seconds (callers get that sweep's result, with
    status "recent")."""
    if not _INGEST_LOCK.acquire(blocking=blocking):
        return {"status": "busy", "message": "A news sweep is already running."}
    try:
        with _SWEEP_STATE_LOCK:
            finished, last = _LAST_SWEEP["finished_mono"], _LAST_SWEEP["result"]
        if min_interval and finished is not None and time.monotonic() - finished < min_interval:
            return {**(last or {}), "status": "recent",
                    "seconds_since": round(time.monotonic() - finished, 1)}
        return _sweep()
    finally:
        _INGEST_LOCK.release()


def _sweep() -> dict:
    global _LAST_OK_AT
    t0 = time.perf_counter()
    relabelled = _safely("relabel", relabel_outdated_rows)
    outcomes = fetch_all_feeds()
    fetched_at = datetime.datetime.now(UTC)
    articles = [a for o in outcomes if o.ok for a in o.articles]

    store: dict[str, Any] = {"inserted": 0, "updated": 0, "duplicates": 0, "clustered": 0, "new_by_feed": {}}
    db_ok = True
    if articles:
        try:
            store = _store_articles(articles, fetched_at)
        except Exception as e:
            db_ok = False
            logger.error("News ingestion: storing %d articles failed: %s", len(articles), e)
    pruned = _safely("prune", prune_old_articles)
    feeds_ok, feeds_answered = _record_outcomes(outcomes, db_ok, store["new_by_feed"])
    if store["inserted"] or store["updated"] or pruned or relabelled:
        _cache_clear()

    finished_at = datetime.datetime.now(UTC)
    # A sweep succeeded when some feed answered and what it sent was stored.
    swept_ok = feeds_answered > 0 and db_ok
    result = {
        "status": "ok" if swept_ok else "failed",
        "feeds_ok": feeds_ok,
        "feeds_total": len(outcomes),
        "fetched": len(articles),
        "inserted": store["inserted"],
        "updated": store["updated"],
        "duplicates": store["duplicates"],
        "clustered": store["clustered"],
        "pruned": pruned,
        "relabelled": relabelled,
        "finished_at": finished_at.isoformat(),
        "time_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    with _SWEEP_STATE_LOCK:
        _LAST_SWEEP.update(finished_mono=time.monotonic(), finished_at=finished_at, result=result)
        if swept_ok:
            _LAST_OK_AT = finished_at
    logger.info(
        "News sweep: %d/%d feeds ok, %d items, %d new, %d updated, %d duplicates dropped, "
        "%d merged into clusters, %d pruned, %d relabelled in %sms",
        feeds_ok, len(outcomes), len(articles), store["inserted"], store["updated"],
        store["duplicates"], store["clustered"], pruned, relabelled, result["time_ms"],
    )
    return result


def request_manual_refresh() -> dict:
    """The refresh button: never two sweeps at once, never more than one a minute."""
    return ingest_and_store_feeds(blocking=False, min_interval=MIN_MANUAL_REFRESH_SECONDS)


# --- Read path --------------------------------------------------------------------------

# Isolated from app.core.cache's registries on purpose: a NAV sync must not flush it.
_NEWS_L1_CACHE: TTLCache = TTLCache(maxsize=32, ttl=900)
# What each query last returned, served (marked offline) when the database is unreachable.
_LAST_GOOD: TTLCache = TTLCache(maxsize=64, ttl=86400)
# TTLCache is not thread-safe, and API threads read it while the sweep clears it.
_CACHE_LOCK = threading.Lock()


def _cache_clear() -> None:
    with _CACHE_LOCK:
        _NEWS_L1_CACHE.clear()


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _query_articles(category: Optional[str], region: Optional[str], limit: int, search: str) -> dict:
    global _LAST_OK_AT, _LAST_OK_SEEDED
    where = ["published_at >= now() - make_interval(days => %s)"]
    params: list[Any] = [NEWS_RETENTION_DAYS]
    if category:
        where.append("category = %s")
        params.append(category)
    if region:
        where.append("region = %s")
        params.append(region)
    if search:
        pat = f"%{_escape_like(search)}%"
        where.append("(title ILIKE %s ESCAPE '\\' OR summary ILIKE %s ESCAPE '\\' OR source ILIKE %s ESCAPE '\\')")
        params.extend([pat, pat, pat])
    # One card per story: the cluster's earliest matching member, with the others listed.
    query = f"""
        WITH matched AS (
            SELECT id, title, summary, source, url, category, region, published_at, sentiment,
                   keywords, fetched_at, date_only, feed, COALESCE(cluster_id, id) AS cid
            FROM news_articles
            WHERE {' AND '.join(where)}
        ), ranked AS (
            SELECT *, row_number() OVER (PARTITION BY cid ORDER BY published_at, id) AS rn
            FROM matched
        )
        SELECT id, title, summary, source, url, category, region, published_at, sentiment,
               keywords, fetched_at, date_only, feed, cid, count(*) OVER () AS total
        FROM ranked
        WHERE rn = 1
        ORDER BY published_at DESC, id
        LIMIT %s
    """
    params.append(limit)
    con = get_connection()
    try:
        rows = con.execute(query, params).fetchall()
        cids = [r[13] for r in rows]
        related: dict[str, list[dict]] = {}
        if cids:
            for cid, id_, source, url, title, pub in con.execute(
                "SELECT COALESCE(cluster_id, id), id, source, url, title, published_at FROM news_articles "
                "WHERE COALESCE(cluster_id, id) = ANY(%s) ORDER BY published_at, id",
                (cids,),
            ).fetchall():
                related.setdefault(cid, []).append(
                    {"id": id_, "source": source, "url": url, "title": title, "published_at": _iso(pub)})
        if not _LAST_OK_SEEDED:
            row = con.execute("SELECT max(fetched_at) FROM news_articles").fetchone()
            with _SWEEP_STATE_LOCK:
                if _LAST_OK_AT is None and row and row[0]:
                    _LAST_OK_AT = row[0]
                _LAST_OK_SEEDED = True
    finally:
        con.close()

    articles = []
    for r in rows:
        feed = _FEED_BY_NAME.get(r[12] or "") or _FEED_BY_SOURCE.get(r[3])
        others = [x for x in related.get(r[13], []) if x["id"] != r[0]]
        articles.append({
            "id": r[0],
            "title": r[1],
            "summary": r[2] or "",
            "source": r[3],
            "url": r[4],
            "category": r[5],
            "region": r[6],
            "published_at": _iso(r[7]),
            "sentiment": r[8] or "neutral",
            "keywords": r[9] or [],
            "fetched_at": _iso(r[10]),
            "date_only": bool(r[11]),
            "paywall": feed.paywall if feed else None,
            "official": bool(feed and feed.official),
            "related": others,
        })
    return {
        "articles": articles,
        "total": int(rows[0][14]) if rows else 0,
        "cached_at": datetime.datetime.now(UTC).isoformat(),
    }


def _feed_counts() -> dict[str, int]:
    with _STATUS_LOCK:
        states = [st.state for st in _FEED_STATUS.values()]
    return {
        "feeds_total": len(states),
        "feeds_ok": states.count("ok"),
        "feeds_stale": states.count("stale"),
        "feeds_failing": states.count("failing") + states.count("backoff"),
        "feeds_pending": states.count("pending"),
    }


def _freshness() -> dict:
    with _SWEEP_STATE_LOCK:
        ok_at = _LAST_OK_AT
        last_at = _LAST_SWEEP["finished_at"]
    now = datetime.datetime.now(UTC)
    return {
        "last_sweep_at": _iso(last_at),
        "last_sweep_ok_at": _iso(ok_at),
        "is_stale": ok_at is None or now - ok_at > STALE_DATA_AFTER,
        **_feed_counts(),
    }


def get_cached_news(
    category: Optional[str] = None,
    region: Optional[str] = None,
    limit: int = 50,
    search: Optional[str] = None,
) -> dict:
    """Articles for the feed, newest first, one card per story. Served from a 15-minute
    cache that every sweep that changed something clears; when the database is
    unreachable, the last result for the same query is served and marked offline."""
    cat = category if category in CATEGORY_LABELS else None
    reg = region if region in REGION_LABELS else None
    search_key = (search or "").strip()[:100]
    key = (cat, reg, limit, search_key.lower())

    with _CACHE_LOCK:
        result = _NEWS_L1_CACHE.get(key)
    is_offline = False
    if result is None:
        try:
            result = _query_articles(cat, reg, limit, search_key)
            with _CACHE_LOCK:
                _NEWS_L1_CACHE[key] = result
                _LAST_GOOD[key] = result
        except Exception as e:
            logger.error("Error querying news articles: %s", e)
            is_offline = True
            with _CACHE_LOCK:
                result = _LAST_GOOD.get(key)
            result = result or {"articles": [], "total": 0, "cached_at": None}

    fresh = _freshness()
    articles = result["articles"]
    return {
        "status": "ok",
        "total": result["total"],
        "count": len(articles),
        "is_offline": is_offline,
        "is_stale": is_offline or fresh["is_stale"],
        "articles": articles,
        "last_updated": articles[0]["published_at"] if articles else None,
        "cached_at": result.get("cached_at") if is_offline else None,
        "retention_days": NEWS_RETENTION_DAYS,
        **{k: v for k, v in fresh.items() if k != "is_stale"},
    }


def get_sources_status() -> dict:
    """Per-feed health, plus what each feed has stored within the retention window."""
    stored: dict[str, tuple[int, Optional[datetime.datetime]]] = {}
    try:
        con = get_connection()
        try:
            for name, n, newest in con.execute(
                "SELECT feed, count(*), max(published_at) FROM news_articles GROUP BY feed"
            ).fetchall():
                stored[name or ""] = (int(n), newest)
        finally:
            con.close()
    except Exception as e:
        logger.warning("Could not count stored news per feed: %s", e)

    feeds = []
    with _STATUS_LOCK:
        for feed in FEEDS:
            st = _FEED_STATUS.get(feed.name) or FeedStatus()
            n, newest_stored = stored.get(feed.name, (0, None))
            feeds.append({
                "name": feed.name,
                "source": feed.source,
                "url": feed.url,
                "category": feed.category,
                "region": feed.region,
                "official": feed.official,
                "paywall": feed.paywall,
                "state": st.state,
                "last_attempt_at": _iso(st.last_attempt_at),
                "last_ok_at": _iso(st.last_ok_at),
                "http_status": st.http_status,
                "items_parsed": st.items_parsed,
                "items_kept": st.items_kept,
                "items_new": st.items_new,
                "newest_item_at": _iso(st.newest_item_at),
                "consecutive_failures": st.consecutive_failures,
                "last_error": st.last_error,
                "backoff_until": _iso(st.backoff_until),
                "dropped": dict(st.dropped),
                "stored": n,
                "stored_newest_at": _iso(newest_stored),
                "stale_after_days": feed.stale_after_days,
            })
    with _SWEEP_STATE_LOCK:
        next_at = _NEXT_SWEEP_AT
    fresh = _freshness()
    return {
        "feeds": feeds,
        **{k: v for k, v in fresh.items() if k != "is_stale"},
        "is_stale": fresh["is_stale"],
        "next_sweep_at": _iso(next_at),
        "daemon_running": is_news_daemon_running(),
        "retention_days": NEWS_RETENTION_DAYS,
    }


def get_health_summary() -> dict:
    """The /api/health block: in-memory only, so health never waits on the database."""
    fresh = _freshness()
    return {
        "feeds_ok": fresh["feeds_ok"],
        "feeds_total": fresh["feeds_total"],
        "feeds_failing": fresh["feeds_failing"],
        "last_sweep_at": fresh["last_sweep_at"],
        "last_successful_sweep_at": fresh["last_sweep_ok_at"],
        "is_stale": fresh["is_stale"],
        "daemon_running": is_news_daemon_running(),
    }


# --- Daemon -----------------------------------------------------------------------------

_STOP_EVENT = threading.Event()
_NEWS_DAEMON_THREAD: threading.Thread | None = None
_NEWS_DAEMON_LOCK = threading.Lock()


def _set_next_sweep(seconds_from_now: float) -> None:
    global _NEXT_SWEEP_AT
    with _SWEEP_STATE_LOCK:
        _NEXT_SWEEP_AT = datetime.datetime.now(UTC) + datetime.timedelta(seconds=seconds_from_now)


def _initial_delay_seconds(now: Optional[datetime.datetime] = None) -> float:
    """How long the first sweep after startup waits: until a sweep interval after the
    newest stored fetch, so a restart (or a --reload) with fresh data does not refetch
    every feed; at least MIN_STARTUP_DELAY_SECONDS."""
    now = now or datetime.datetime.now(UTC)
    try:
        con = get_connection()
        try:
            row = con.execute("SELECT max(fetched_at) FROM news_articles").fetchone()
        finally:
            con.close()
    except Exception as e:
        logger.warning("News daemon: could not read the last fetch time (%s); sweeping soon.", e)
        return MIN_STARTUP_DELAY_SECONDS
    last = row[0] if row else None
    if last is None:
        return MIN_STARTUP_DELAY_SECONDS
    due = last + datetime.timedelta(seconds=SWEEP_INTERVAL_SECONDS)
    return max(MIN_STARTUP_DELAY_SECONDS, (due - now).total_seconds())


def _news_daemon_worker():
    logger.info("News ingestion daemon worker started.")
    delay = _initial_delay_seconds()
    _set_next_sweep(delay)
    if _STOP_EVENT.wait(MIN_STARTUP_DELAY_SECONDS):
        return
    # A classifier change should reach stored rows now, not at the first sweep.
    with _INGEST_LOCK:
        _safely("relabel", relabel_outdated_rows)
    remaining = delay - MIN_STARTUP_DELAY_SECONDS
    if remaining > 0:
        logger.info("News daemon: stored news is fresh; first sweep in %.0fs.", remaining)
        if _STOP_EVENT.wait(remaining):
            return

    while not _STOP_EVENT.is_set():
        try:
            res = ingest_and_store_feeds(blocking=True, min_interval=MIN_MANUAL_REFRESH_SECONDS)
            sleep_sec = SWEEP_INTERVAL_SECONDS if res.get("status") in ("ok", "recent") else RETRY_AFTER_FAILED_SWEEP_SECONDS
        except Exception as e:
            logger.error("Unexpected error in news daemon worker: %s", e)
            sleep_sec = RETRY_AFTER_FAILED_SWEEP_SECONDS
        _set_next_sweep(sleep_sec)
        if _STOP_EVENT.wait(sleep_sec):
            break
    logger.info("News ingestion daemon worker terminated cleanly.")


def is_news_daemon_running() -> bool:
    with _NEWS_DAEMON_LOCK:
        return bool(_NEWS_DAEMON_THREAD and _NEWS_DAEMON_THREAD.is_alive())


def ensure_news_daemon_running() -> None:
    """Starts the news daemon thread if enabled in config."""
    global _NEWS_DAEMON_THREAD
    if not settings.enable_news_daemon:
        logger.info("News daemon NOT started: enable_news_daemon is false.")
        return
    with _NEWS_DAEMON_LOCK:
        if _NEWS_DAEMON_THREAD is None or not _NEWS_DAEMON_THREAD.is_alive():
            _STOP_EVENT.clear()
            _NEWS_DAEMON_THREAD = threading.Thread(
                target=_news_daemon_worker,
                name="News_Ingestion_Daemon",
                daemon=True,
            )
            _NEWS_DAEMON_THREAD.start()
            logger.info("News ingestion daemon thread started successfully.")


def stop_news_daemon() -> None:
    """Signals the news daemon thread to stop and waits briefly for it."""
    _STOP_EVENT.set()
    with _NEWS_DAEMON_LOCK:
        thread = _NEWS_DAEMON_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=2.0)
