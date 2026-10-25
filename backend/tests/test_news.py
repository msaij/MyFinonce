"""Tests for the global financial news feed (app/services/news_service.py, routers/news.py).

No network: feeds are the documents under tests/fixtures/news/ or built inline, served
through a patched _http_get or urlopen. The news daemon never runs here (conftest.py).

Covers, by grill-report finding:
  R1  category slugs: the label/slug contract, 422 for labels, every chip returns rows
  R2  dates: naive IST, date-only feeds, no "now"/Last-Modified fallback, future clamp/drop
  R3  30-day retention: ingest filter, prune on every sweep, no resurrection
  R4  per-feed health: 0 items is a failure, stale feeds, real insert counts, /sources
  R6  the daemon is not running under pytest
  R7/R8/R23  word-boundary classifier, labelled headlines, sentiment directions
  R9  duplicates: same-source drop, cross-source clusters, number guard
  R10 CDATA and entity decoding
  R11 single-flight sweep, throttled refresh, header guard
  R12 honest UA, size cap, deadline, malformed XML rejected
  R14 last-good fallback and freshness semantics
  R16 migration runner refuses an unknown head; migration 0012's cleanup
  R17 search escaping and source matching
  R20 the daemon's first sweep waits while stored data is fresh
  R22 RBI routine releases excluded
"""

from __future__ import annotations

import datetime
import email.message
import json
import logging
import pathlib
import threading
import time
import typing
import urllib.error
import urllib.request

from fastapi.testclient import TestClient
import pytest

from app.core import cache as app_cache
from app.core.config import settings
from app.db import connection, migrate
from app.main import app
from app.routers import news as news_router
from app.services import news_service
from app.services.news_service import (
    CATEGORY_LABELS,
    CATEGORY_SLUGS,
    FEEDS,
    REGION_SLUGS,
    FeedError,
    canonicalize_url,
    classify_article,
    classify_sentiment,
    clean_html_text,
    compute_title_signature,
    extract_keywords,
    parse_date_ladder,
    parse_feed_xml,
    preclean_xml,
    validate_article_url,
)

UTC = datetime.timezone.utc
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "news"
FEED = news_service._FEED_BY_NAME


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def rfc2822(dt: datetime.datetime) -> str:
    return dt.astimezone(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")


def rss(items: list[dict]) -> bytes:
    """A minimal RSS 2.0 document; each item: title, link, and optionally pubDate, description."""
    parts = []
    for it in items:
        parts.append(
            "<item>"
            f"<title>{it['title']}</title>"
            f"<link>{it['link']}</link>"
            + (f"<pubDate>{it['pubDate']}</pubDate>" if it.get("pubDate") else "")
            + (f"<description><![CDATA[{it['description']}]]></description>" if it.get("description") else "")
            + "</item>"
        )
    return ('<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>t</title>'
            + "".join(parts) + "</channel></rss>").encode("utf-8")


@pytest.fixture(autouse=True)
def fresh_news_state(monkeypatch):
    """Per-feed health, sweep bookkeeping and caches are module state; each test starts clean."""
    monkeypatch.setattr(news_service, "_FEED_STATUS", {f.name: news_service.FeedStatus() for f in FEEDS})
    monkeypatch.setattr(news_service, "_LAST_SWEEP", {"finished_mono": None, "finished_at": None, "result": None})
    monkeypatch.setattr(news_service, "_LAST_OK_AT", None)
    monkeypatch.setattr(news_service, "_LAST_OK_SEEDED", False)
    monkeypatch.setattr(news_service, "_NEXT_SWEEP_AT", None)
    news_service._NEWS_L1_CACHE.clear()
    news_service._LAST_GOOD.clear()
    yield
    news_service._NEWS_L1_CACHE.clear()
    news_service._LAST_GOOD.clear()


@pytest.fixture()
def client(pg_db, monkeypatch):
    monkeypatch.setattr(settings, "enable_sync_daemon", False)
    with TestClient(app) as c:
        yield c


def fake_http(documents: dict[str, bytes]):
    """A stand-in for _http_get: named feeds answer with their document, the rest fail."""
    def _get(feed, etag=None, last_modified=None):
        if feed.name in documents:
            return news_service.HttpResult(200, documents[feed.name], None, None, "utf-8")
        raise FeedError("offline in test")
    return _get


def insert_article(con, **kw) -> None:
    now = datetime.datetime.now(UTC)
    row = {
        "id": "id", "title": "A headline", "summary": "", "source": "Livemint",
        "url": "https://example.com/a", "category": "macro", "region": "global",
        "published_at": now - datetime.timedelta(hours=1), "sentiment": "neutral",
        "keywords": [], "title_signature": None, "fetched_at": now, "cluster_id": None,
        "feed": None, "date_only": False, "classifier_version": news_service.CLASSIFIER_VERSION,
    }
    row.update(kw)
    if "cluster_id" not in kw:  # legacy rows pass cluster_id=None explicitly
        row["cluster_id"] = row["id"]
    cols = ", ".join(row)
    con.execute(f"INSERT INTO news_articles ({cols}) VALUES ({', '.join(['%s'] * len(row))})", list(row.values()))


def count_rows(where: str = "TRUE", params=()) -> int:
    con = connection.get_connection()
    try:
        return con.execute(f"SELECT count(*) FROM news_articles WHERE {where}", params).fetchone()[0]
    finally:
        con.close()


# --- R6: the daemon stays off under pytest --------------------------------------------------


def test_news_daemon_is_disabled_under_pytest(client):
    assert settings.enable_news_daemon is False
    assert not news_service.is_news_daemon_running()
    assert not any(t.name == "News_Ingestion_Daemon" for t in threading.enumerate())


def test_tests_never_point_at_the_application_database(pg_db):
    assert not connection.DATABASE_URL.endswith("/mutual_funds")


# --- R10: XML pre-cleaning, entities and CDATA -----------------------------------------------


def test_preclean_xml_escapes_bare_ampersands_outside_cdata_only():
    raw = ("<title>S&P 500 gains & AT&T reports Q&A &amp; Apple &gt; Google</title>"
           "<description><![CDATA[Sensex &amp; Nifty&nbsp;rally]]></description>")
    cleaned = preclean_xml(raw)
    assert "S&amp;P 500" in cleaned and "AT&amp;T" in cleaned and "Q&amp;A" in cleaned
    assert "&amp; Apple &gt; Google" in cleaned
    assert "&amp;amp;" not in cleaned
    # CDATA is left exactly as it was: its entities are the HTML cleaner's job.
    assert "<![CDATA[Sensex &amp; Nifty&nbsp;rally]]>" in cleaned


def test_preclean_xml_strips_utf8_bom():
    cleaned = preclean_xml("﻿<rss><channel><title>News</title></channel></rss>")
    assert cleaned.startswith("<rss>")


def test_cdata_entities_decode_to_text_not_literal_entities():
    parsed = parse_feed_xml(fixture_bytes("cdata_entities.xml"), FEED["CNBC: World"],
                            fetched_at=datetime.datetime(2026, 10, 15, tzinfo=UTC))
    first, second = parsed.articles
    assert first["title"] == "Sensex & Nifty rally — S&P up"
    # A trailing "AT&T" used to sit in HTMLParser's buffer, waiting for an entity to end.
    assert first["summary"] == "Markets climbed ’sharply’ on AT&T"
    assert second["title"] == "Speech at the Central Bank of the Republic of Türkiye"
    assert second["summary"] == "Speech At the Istanbul Economic Forum & dinner"
    for art in parsed.articles:
        assert "&nbsp;" not in art["title"] + art["summary"]
        assert "&mdash;" not in art["title"] + art["summary"]


def test_double_escaped_entities_in_cdata_are_decoded():
    # Livemint, live on 2026-10-15: "&amp;nbsp;" and "&amp;amp;" inside CDATA.
    now = datetime.datetime.now(UTC)
    doc = ("<rss><channel><item><title>Economy report</title><link>https://www.livemint.com/e/1</link>"
           f"<pubDate>{rfc2822(now)}</pubDate>"
           "<description><![CDATA[Faring better, a Bain &amp;amp; Company report said.&amp;nbsp;]]></description>"
           "</item></channel></rss>")
    (art,) = parse_feed_xml(doc.encode(), FEED["Livemint: Economy"], fetched_at=now).articles
    assert art["summary"] == "Faring better, a Bain & Company report said."
    # A bare ampersand with no entity after it is text, untouched.
    assert clean_html_text("AT&T and S&P 500") == "AT&T and S&P 500"


def test_secure_text_extractor_strips_dangerous_tags():
    malicious_html = """
    <div><p>Markets rally following Fed announcement.</p>
        <script>alert('XSS execution');</script><style>body { display: none; }</style>
        <iframe src="https://evil.com/phish"></iframe><object data="malware.swf"></object>
        <svg><text>Vector payload</text></svg><p>Trading volume surged by 15%.</p></div>
    """
    clean = clean_html_text(malicious_html)
    assert "Markets rally following Fed announcement." in clean
    assert "Trading volume surged by 15%." in clean
    for bad in ("alert", "XSS", "display: none", "evil.com", "malware.swf", "Vector payload"):
        assert bad not in clean


def test_secure_text_extractor_decodes_entities_and_collapses_whitespace():
    clean = clean_html_text("<p>RBI&apos;s&nbsp;&nbsp;rate decision&amp;forecast&nbsp; &mdash; &gt; 6.5%</p>")
    assert clean == "RBI's rate decision&forecast — > 6.5%"


# --- URLs -----------------------------------------------------------------------------------


def test_validate_article_url():
    assert validate_article_url("https://www.reuters.com/markets/us/fed-rates-2026-10-14/")
    assert validate_article_url("http://rbi.org.in/press/123")
    for bad in ("javascript:alert(document.cookie)", "data:text/html;base64,PHNjcmlwdD4=",
                "file:///etc/passwd", "not_a_url", "", None):
        assert not validate_article_url(bad)  # type: ignore[arg-type]


def test_canonicalize_url_strips_tracking_params_keeps_ids():
    canon = canonicalize_url(
        "https://www.marketwatch.com/story/stocks-surge-2026?utm_source=twitter"
        "&utm_medium=social&oc=1&ref=homepage&id=482910#user-comments"
    )
    assert canon == "https://www.marketwatch.com/story/stocks-surge-2026?id=482910"
    assert canonicalize_url("https://www.dw.com/en/a/a-79571852?maca=en-rss-en-bus-2091-rdf") == \
        "https://www.dw.com/en/a/a-79571852"
    assert canonicalize_url("https://www.bbc.co.uk/news/articles/x?at_medium=RSS&at_campaign=rss") == \
        "https://www.bbc.co.uk/news/articles/x"


def test_canonicalize_url_normalizes_ports_slashes():
    assert canonicalize_url("HTTP://Example.COM:80/news/article-1/") == "http://example.com/news/article-1"
    assert canonicalize_url("HTTPS://Example.COM:443/news/article-1/") == "https://example.com/news/article-1"
    assert canonicalize_url("https://www.ecb.europa.eu//press/key/date/2026/html/x.en.html") == \
        "https://www.ecb.europa.eu/press/key/date/2026/html/x.en.html"


# --- R2: dates ------------------------------------------------------------------------------


def test_naive_rbi_time_is_read_as_ist():
    assert parse_date_ladder(["Thu, 15 Oct 2026 09:00:00"], tz="Asia/Kolkata") == \
        datetime.datetime(2026, 10, 15, 3, 30, tzinfo=UTC)


def test_aware_times_ignore_the_feed_tz():
    assert parse_date_ladder(["Wed, 14 Oct 2026 12:00:00 GMT"], tz="Asia/Kolkata") == \
        datetime.datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    assert parse_date_ladder([None, "2026-10-14T15:30:00Z"]) == datetime.datetime(2026, 10, 14, 15, 30, tzinfo=UTC)
    assert parse_date_ladder(["Wed, 14 Oct 2026 02:00:00 +0200"]) == datetime.datetime(2026, 10, 14, 0, 0, tzinfo=UTC)


def test_sebi_date_format():
    assert parse_date_ladder(["14 Oct, 2026 +0530"], tz="Asia/Kolkata", date_formats=("%d %b, %Y %z",)) == \
        datetime.datetime(2026, 10, 13, 18, 30, tzinfo=UTC)


def test_no_usable_date_is_none_not_now():
    assert parse_date_ladder([None, "", "sometime last week"]) is None


def test_undated_items_are_dropped_and_the_feed_counts_as_failing(monkeypatch):
    parsed = parse_feed_xml(fixture_bytes("undated.xml"), FEED["CNA: Business"])
    assert parsed.articles == []
    assert parsed.dropped == {"undated": 2}
    monkeypatch.setattr(news_service, "_http_get", fake_http({"CNA: Business": fixture_bytes("undated.xml")}))
    out = news_service.fetch_feed(FEED["CNA: Business"])
    assert not out.ok and "usable" in out.error


def test_rbi_feed_naive_ist_and_routine_exclusions():
    fetched = datetime.datetime(2026, 10, 15, 5, 0, tzinfo=UTC)  # 10:30 IST
    parsed = parse_feed_xml(fixture_bytes("rbi_naive_ist.xml"), FEED["RBI: Press releases"], fetched_at=fetched)
    assert parsed.items_parsed == 5
    assert parsed.dropped == {"excluded": 4}  # money market ops, T-bill, VRRR result, penalty
    (art,) = parsed.articles
    assert art["title"].startswith("Monetary Policy Statement")
    assert art["published_at"] == datetime.datetime(2026, 10, 14, 4, 30, tzinfo=UTC)  # 10:00 IST
    assert art["category"] == "central_banks" and art["region"] == "india"
    assert art["sentiment"] == "neutral"  # official: never scored
    assert "&nbsp;" not in art["summary"] and len(art["summary"]) <= 301
    # The VRRR result is 10:20 IST = 04:50 UTC: before the fetch, so not "future".
    assert parsed.newest_item_at == datetime.datetime(2026, 10, 15, 4, 50, tzinfo=UTC)


def test_far_future_dates_are_dropped_and_skew_is_clamped(caplog):
    fetched = datetime.datetime(2026, 10, 15, 5, 0, tzinfo=UTC)
    # The same RBI document read with the wrong zone: its times land up to 5h ahead.
    wrong_tz = news_service.Feed("Mis-zoned", "X", "https://x.example/feed", "macro", "global", tz="UTC")
    with caplog.at_level(logging.WARNING, logger="news_service"):
        parsed = parse_feed_xml(fixture_bytes("rbi_naive_ist.xml"), wrong_tz, fetched_at=fetched)
    assert parsed.dropped.get("future") == 2
    assert all(a["published_at"] <= fetched for a in parsed.articles)
    assert "Mis-zoned" in caplog.text
    skewed = rss([{"title": "Slightly early", "link": "https://x.example/1",
                   "pubDate": rfc2822(fetched + datetime.timedelta(minutes=5))}])
    (art,) = parse_feed_xml(skewed, FEED["BBC: Business"], fetched_at=fetched).articles
    assert art["published_at"] == fetched


def test_sebi_date_only_and_enforcement_excluded():
    fetched = datetime.datetime(2026, 10, 15, 5, 0, tzinfo=UTC)
    parsed = parse_feed_xml(fixture_bytes("sebi_date_only.xml"), FEED["SEBI"], fetched_at=fetched)
    assert parsed.dropped == {"excluded": 1}
    circular, release = parsed.articles
    assert circular["date_only"] is True
    assert circular["published_at"] == datetime.datetime(2026, 10, 13, 18, 30, tzinfo=UTC)  # 14 Oct 00:00 IST
    assert release["published_at"] == datetime.datetime(2026, 10, 12, 18, 30, tzinfo=UTC)
    assert circular["summary"] == ""  # the description only repeated the title


def test_atom_prefers_alternate_link_and_published_date():
    parsed = parse_feed_xml(fixture_bytes("atom_links.xml"), FEED["Livemint: Markets"],
                            fetched_at=datetime.datetime(2026, 10, 15, tzinfo=UTC))
    nifty, rupee = parsed.articles
    assert nifty["url"] == "https://www.livemint.com/market/nifty-record-high-12345.html"
    assert rupee["url"] == "https://www.livemint.com/market/rupee-slips-67890.html"
    assert rupee["published_at"] == datetime.datetime(2026, 10, 14, 2, 30, tzinfo=UTC)  # <published>, not <updated>


# --- R12: fetching and parsing hardening ------------------------------------------------------


def test_malformed_xml_is_rejected_not_salvaged(monkeypatch):
    with pytest.raises(FeedError, match="malformed XML"):
        parse_feed_xml(fixture_bytes("malformed.xml"), FEED["BBC: Business"])
    monkeypatch.setattr(news_service, "_http_get", fake_http({"BBC: Business": fixture_bytes("malformed.xml")}))
    out = news_service.fetch_feed(FEED["BBC: Business"])
    assert not out.ok and out.articles == [] and "malformed XML" in out.error


def test_bare_ampersands_are_repaired():
    now = datetime.datetime.now(UTC)
    doc = (f"<rss><channel><item><title>S&P 500 gains & AT&T jumps</title>"
           f"<link>https://example.com/sp?a=1&b=2</link><pubDate>{rfc2822(now)}</pubDate></item></channel></rss>")
    (art,) = parse_feed_xml(doc.encode(), FEED["BBC: Business"], fetched_at=now).articles
    assert art["title"] == "S&P 500 gains & AT&T jumps"


class FakeResponse:
    def __init__(self, chunks=(b"",), status=200, headers=None, delay=0.0):
        self._chunks = list(chunks)
        self.status = status
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v
        self._delay = delay

    def read1(self, n=-1):
        if self._delay:
            time.sleep(self._delay)
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_requests_use_an_honest_user_agent(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        seen["timeout"] = timeout
        return FakeResponse([rss([{"title": "x", "link": "https://e.com/x", "pubDate": rfc2822(datetime.datetime.now(UTC))}])])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = news_service.fetch_feed(FEED["BBC: Business"])
    assert out.ok
    assert seen["ua"] == news_service.USER_AGENT and "Mozilla" not in seen["ua"]
    assert seen["timeout"] == news_service.FEED_SOCKET_TIMEOUT


def test_oversized_responses_are_rejected(monkeypatch):
    monkeypatch.setattr(news_service, "MAX_FEED_BYTES", 1000)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResponse([b"x"], headers={"Content-Length": "6000000"}))
    out = news_service.fetch_feed(FEED["BBC: Business"])
    assert not out.ok and "too large" in out.error
    # No Content-Length: the streamed total trips the cap.
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResponse([b"x" * 600] * 3))
    out = news_service.fetch_feed(FEED["BBC: Business"])
    assert not out.ok and "larger than" in out.error and out.articles == []


def test_slow_drip_hits_the_per_feed_deadline(monkeypatch):
    monkeypatch.setattr(news_service, "FEED_DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResponse([b"<"] * 1000, delay=0.1))
    t0 = time.monotonic()
    out = news_service.fetch_feed(FEED["BBC: Business"])
    assert not out.ok and "took longer" in out.error
    assert time.monotonic() - t0 < 2.0


def test_200_with_empty_body_is_a_failure_not_ok(monkeypatch):
    # CNBC's dead search feed: HTTP 200, application/json, 0 bytes.
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResponse([b""]))
    out = news_service.fetch_feed(FEED["CNBC: World"])
    assert not out.ok
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResponse([b"<rss><channel></channel></rss>"]))
    out = news_service.fetch_feed(FEED["CNBC: World"])
    assert not out.ok and "no items" in out.error


def test_not_modified_is_ok(monkeypatch):
    def fake_urlopen(req, timeout=None):
        assert req.get_header("If-none-match") == '"abc"'
        raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", email.message.Message(), None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = news_service.fetch_feed(FEED["BBC: Business"], etag='"abc"')
    assert out.ok and out.not_modified


def test_backoff_is_longer_than_a_sweep_after_repeated_failures():
    assert news_service._backoff_seconds(1, 500, None) == 0
    assert news_service._backoff_seconds(2, 500, None) > news_service.SWEEP_INTERVAL_SECONDS
    assert news_service._backoff_seconds(3, None, None) > news_service._backoff_seconds(2, None, None)
    assert news_service._backoff_seconds(1, 403, None) >= 3600
    assert news_service._backoff_seconds(9, None, None) == news_service.BACKOFF_MAX_SECONDS


# --- R7, R8, R23: classification --------------------------------------------------------------


LABELLED = json.loads((FIXTURES / "labelled_headlines.json").read_text(encoding="utf-8"))


def test_sentiment_agrees_with_labelled_headlines():
    rows = LABELLED["sentiment"]
    got = [(r, classify_sentiment(r["title"], "")) for r in rows]
    agree = sum(1 for r, s in got if s == r["expected"])
    misses = [(r["title"], r["expected"], s) for r, s in got if s != r["expected"]]
    assert agree / len(rows) >= 0.8, misses
    for r, s in got:
        if r.get("regression"):
            assert s == r["expected"], r["title"]


def test_classification_agrees_with_labelled_headlines():
    rows = LABELLED["classification"]
    results = [(r, classify_article(r["title"], r["summary"], FEED[r["feed"]])) for r in rows]
    cat_ok = sum(1 for r, lab in results if lab.category == r["category"])
    reg_ok = sum(1 for r, lab in results if lab.region == r["region"])
    misses = [(r["title"], (r["category"], r["region"]), (lab.category, lab.region))
              for r, lab in results if (lab.category, lab.region) != (r["category"], r["region"])]
    assert cat_ok / len(rows) >= 0.9, misses
    assert reg_ok / len(rows) >= 0.8, misses
    for r, lab in results:
        if r.get("regression"):
            assert (lab.category, lab.region) == (r["category"], r["region"]), r["title"]


def test_official_feeds_are_fixed_and_neutral():
    for feed in FEEDS:
        if feed.official:
            lab = classify_article("Gold prices crash as stocks plunge", "Oil and the rupee fall", feed)
            assert (lab.category, lab.region, lab.sentiment) == (feed.category, feed.region, "neutral")


def test_keywords_match_whole_words_only():
    kws = extract_keywords("Toilet maker breaks down the window on Federal Bank", "Goldman says nothing")
    for wrong in ("OIL", "GOLD", "DOW", "FED"):
        assert wrong not in kws
    assert "FED" in extract_keywords("Fed holds rates", "")
    assert "OIL" in extract_keywords("Oil slips", "")


def test_no_named_officials_in_the_keyword_lists():
    terms = " ".join(t for ts in news_service.CATEGORY_TERMS.values() for t in ts).lower()
    terms += " ".join(t for ts in news_service.REGION_TERMS.values() for t in ts).lower()
    assert "shaktikanta" not in terms and "das" not in terms.split() and "powell" not in terms


# --- R9: duplicates ---------------------------------------------------------------------------


def _art(id_, title, source, published, official=False):
    return {"id": id_, "title": title, "source": source, "published_at": published, "official": official,
            "title_signature": compute_title_signature(title)}


def test_same_source_same_title_is_dropped_other_source_joins_the_cluster():
    t = datetime.datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    stored = [news_service._candidate("a1", "a1", "Livemint", compute_title_signature(
        "India's resilient economy draws foreign investors, says Sebi"), "India's resilient economy draws foreign investors, says Sebi", t)]
    new = [
        # The same story re-published by the same outlet at a new URL.
        _art("a2", "Indias resilient economy draws foreign investors, says SEBI", "Livemint", t + datetime.timedelta(hours=1)),
        # The same story from another outlet.
        _art("b1", "India's resilient economy draws foreign investors, says SEBI", "Economic Times", t + datetime.timedelta(hours=2)),
        # Another outlet, same words, different numbers: a different story.
        _art("c1", "RBI hikes repo rate by 25 basis points", "BBC", t),
        _art("c2", "RBI hikes repo rate by 50 basis points", "CNBC", t),
    ]
    kept, duplicates, merged = news_service._assign_clusters(new, stored)
    by_id = {a["id"]: a for a in kept}
    assert duplicates == 1 and "a2" not in by_id
    assert by_id["b1"]["cluster_id"] == "a1"
    assert by_id["c1"]["cluster_id"] != by_id["c2"]["cluster_id"]
    assert merged == 1


def test_an_outlet_rewording_its_own_headline_is_a_duplicate():
    # Seen live from ET Economy on 2026-10-15: the same story re-posted with "India's".
    t = datetime.datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
    new = [
        _art("e1", "Ultra-short-term borrowing costs spike; premium over policy rate hits 4-year high", "Economic Times", t),
        _art("e2", "India's ultra-short-term borrowing costs spike; premium over policy rate hits 4-year high",
             "Economic Times", t + datetime.timedelta(minutes=40)),
        # Same outlet, same topic, a different story: kept.
        _art("e3", "Call money rates ease as RBI injects liquidity through variable rate repo", "Economic Times",
             t + datetime.timedelta(hours=1)),
    ]
    kept, duplicates, _ = news_service._assign_clusters(new, [])
    assert duplicates == 1 and {a["id"] for a in kept} == {"e1", "e3"}


def test_near_duplicate_titles_from_two_outlets_cluster():
    t = datetime.datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    new = [
        _art("x1", "Oil prices fall as OPEC weighs larger output increase", "CNBC", t),
        _art("x2", "Oil prices fall as OPEC weighs a larger output increase next month", "OilPrice.com",
             t + datetime.timedelta(hours=3)),
        _art("y1", "Oil prices fall", "BBC", t),  # too short to merge on
    ]
    kept, _, merged = news_service._assign_clusters(new, [])
    by_id = {a["id"]: a for a in kept}
    assert by_id["x2"]["cluster_id"] == "x1" and merged == 1
    assert by_id["y1"]["cluster_id"] == "y1"


def test_title_signature_ignores_the_publisher_suffix():
    title_a = news_service.clean_title_text("Fed keeps rates unchanged at 5.25% - Reuters", "Reuters")
    title_b = "Fed keeps rates unchanged at 5.25%"
    assert compute_title_signature(title_a) == compute_title_signature(title_b)
    assert compute_title_signature("RBI hikes repo rate by 25 basis points") != \
        compute_title_signature("RBI slashes repo rate by 50 basis points")


# --- Ingest, retention, health (database) -----------------------------------------------------


def _documents(now: datetime.datetime) -> dict[str, bytes]:
    return {
        "BBC: Business": rss([
            {"title": "Oil prices fall as OPEC weighs larger output increase", "link": "https://www.bbc.co.uk/news/a1",
             "pubDate": rfc2822(now - datetime.timedelta(hours=2)), "description": "Brent crude slipped."},
            {"title": "Stocks close higher on Wall Street as tech rebounds", "link": "https://www.bbc.co.uk/news/a2",
             "pubDate": rfc2822(now - datetime.timedelta(hours=1))},
            {"title": "An old story that the feed still serves", "link": "https://www.bbc.co.uk/news/old",
             "pubDate": rfc2822(now - datetime.timedelta(days=31))},
        ]),
        "OilPrice.com": rss([
            {"title": "Oil prices fall as OPEC weighs a larger output increase next month",
             "link": "https://oilprice.com/a/1", "pubDate": rfc2822(now - datetime.timedelta(hours=1))},
        ]),
        "Fed: Monetary policy": rss([
            {"title": "Federal Reserve issues FOMC statement", "link": "https://www.federalreserve.gov/newsevents/pressreleases/monetary1.htm",
             "pubDate": rfc2822(now - datetime.timedelta(hours=3))},
        ]),
        # 200 with no items: must count as failing.
        "MarketWatch: Top stories": b"<rss><channel><title>empty</title></channel></rss>",
    }


def test_sweep_stores_counts_real_inserts_and_never_resurrects(client, monkeypatch):
    now = datetime.datetime.now(UTC)
    monkeypatch.setattr(news_service, "_http_get", fake_http(_documents(now)))

    first = news_service.ingest_and_store_feeds()
    assert first["status"] == "ok"
    assert first["inserted"] == count_rows() == 4  # the 31-day-old item never stored
    assert count_rows("title LIKE %s", ("An old story%",)) == 0
    assert first["clustered"] == 1  # OilPrice's version joins BBC's story

    second = news_service.ingest_and_store_feeds()
    assert second["inserted"] == 0 and second["updated"] == 4 and count_rows() == 4

    # The API shows one card per story, newest first, never in the future.
    body = client.get("/api/news").json()
    assert body["total"] == 3 and body["count"] == 3
    oil = next(a for a in body["articles"] if a["title"].startswith("Oil prices fall"))
    assert [r["source"] for r in oil["related"]] == ["OilPrice.com"]
    stamps = [a["published_at"] for a in body["articles"]]
    assert stamps == sorted(stamps, reverse=True)
    assert all(datetime.datetime.fromisoformat(s) <= datetime.datetime.now(UTC) for s in stamps)


def test_feed_health_is_reported(client, monkeypatch, caplog):
    now = datetime.datetime.now(UTC)
    monkeypatch.setattr(news_service, "_http_get", fake_http(_documents(now)))
    with caplog.at_level(logging.INFO, logger="news_service"):
        news_service.ingest_and_store_feeds()
    sources = {f["name"]: f for f in client.get("/api/news/sources").json()["feeds"]}
    assert len(sources) == len(FEEDS)
    assert sources["BBC: Business"]["state"] == "ok"
    assert sources["BBC: Business"]["items_parsed"] == 3 and sources["BBC: Business"]["items_kept"] == 2
    assert sources["BBC: Business"]["dropped"] == {"too_old": 1}
    assert sources["MarketWatch: Top stories"]["state"] == "failing"
    assert "no items" in sources["MarketWatch: Top stories"]["last_error"]
    assert sources["CNA: Business"]["state"] == "failing"
    # Every failure log line names its feed.
    assert "MarketWatch: Top stories" in caplog.text and "FAILED" in caplog.text
    health = client.get("/api/health").json()["news"]
    assert health["feeds_ok"] == 3 and health["feeds_total"] == len(FEEDS)
    assert health["last_successful_sweep_at"] is not None


def test_a_feed_whose_newest_item_is_old_is_stale(client, monkeypatch):
    now = datetime.datetime.now(UTC)
    docs = {"BBC: Business": rss([{"title": "Frozen feed's last story", "link": "https://www.bbc.co.uk/news/z",
                                   "pubDate": rfc2822(now - datetime.timedelta(days=10))}])}
    monkeypatch.setattr(news_service, "_http_get", fake_http(docs))
    news_service.ingest_and_store_feeds()
    st = news_service._FEED_STATUS["BBC: Business"]
    assert st.state == "stale"


def test_prune_keeps_29_days_and_runs_even_when_every_feed_fails(client, monkeypatch):
    now = datetime.datetime.now(UTC)
    con = connection.get_connection()
    insert_article(con, id="old", url="https://e.com/old", published_at=now - datetime.timedelta(days=31))
    insert_article(con, id="new", url="https://e.com/new", published_at=now - datetime.timedelta(days=29))
    con.close()
    monkeypatch.setattr(news_service, "_http_get", fake_http({}))  # internet down
    res = news_service.ingest_and_store_feeds()
    assert res["status"] == "failed" and res["pruned"] == 1
    assert count_rows("id = 'old'") == 0 and count_rows("id = 'new'") == 1


def test_last_ok_does_not_advance_when_every_feed_fails(client, monkeypatch):
    monkeypatch.setattr(news_service, "_http_get", fake_http({}))
    news_service.ingest_and_store_feeds()
    body = client.get("/api/news").json()
    assert body["last_sweep_ok_at"] is None and body["is_stale"] is True
    assert body["last_sweep_at"] is not None and body["feeds_ok"] == 0


# --- R1: category contract --------------------------------------------------------------------


def test_category_and_region_contract_is_pinned():
    # Mirrored in frontend/lib/news.test.ts: change both together.
    assert CATEGORY_LABELS == {
        "central_banks": "Central Banks",
        "macro": "Macro",
        "equities": "Equities",
        "commodities_forex": "FX & Commodities",
    }
    assert REGION_SLUGS == ("global", "india", "us", "uk", "europe", "asia")
    assert typing.get_args(news_router.CategorySlug) == CATEGORY_SLUGS
    assert typing.get_args(news_router.RegionSlug) == REGION_SLUGS
    for feed in FEEDS:
        assert feed.category in CATEGORY_SLUGS and feed.region in REGION_SLUGS, feed.name


def test_every_category_chip_returns_its_rows(client):
    con = connection.get_connection()
    for i, cat in enumerate(CATEGORY_SLUGS):
        insert_article(con, id=f"id_{cat}", url=f"https://e.com/{cat}", category=cat, title=f"Story {i}")
    con.close()
    for cat in CATEGORY_SLUGS:
        body = client.get("/api/news", params={"category": cat}).json()
        assert body["total"] == 1 and body["articles"][0]["category"] == cat
    assert client.get("/api/news").json()["total"] == len(CATEGORY_SLUGS)


def test_labels_and_unknown_values_are_rejected(client):
    for bad in ({"category": "Central Banks"}, {"category": "FX & Commodities"}, {"region": "Mars"}):
        assert client.get("/api/news", params=bad).status_code == 422


# --- R17: search ------------------------------------------------------------------------------


def test_search_escapes_wildcards_and_matches_sources(client):
    con = connection.get_connection()
    insert_article(con, id="a", url="https://e.com/a", title="Sensex climbs", source="Livemint")
    insert_article(con, id="b", url="https://e.com/b", title="Gold up 5_percent", source="CNBC")
    con.close()
    assert client.get("/api/news", params={"search": "%"}).json()["total"] == 0
    assert client.get("/api/news", params={"search": "livemint"}).json()["total"] == 1
    assert client.get("/api/news", params={"search": "5_p"}).json()["total"] == 1
    assert client.get("/api/news", params={"search": "x_p"}).json()["total"] == 0


# --- R14: offline and stale -------------------------------------------------------------------


def test_database_outage_serves_last_good_marked_offline(client, monkeypatch):
    con = connection.get_connection()
    insert_article(con, id="a", url="https://e.com/a", title="Sensex climbs")
    con.close()
    assert client.get("/api/news").json()["total"] == 1
    news_service._cache_clear()

    def boom():
        raise RuntimeError("database down")

    monkeypatch.setattr(news_service, "get_connection", boom)
    body = client.get("/api/news").json()
    assert body["is_offline"] is True and body["is_stale"] is True
    assert [a["id"] for a in body["articles"]] == ["a"] and body["cached_at"]
    # A query never served before has nothing to fall back on: empty, and says so.
    body = client.get("/api/news", params={"search": "never asked"}).json()
    assert body["is_offline"] is True and body["articles"] == []


# --- R11: single flight and refresh -----------------------------------------------------------


def test_sweeps_are_single_flight(monkeypatch, pg_db):
    calls = []

    def slow_fetch(feeds=None):
        calls.append(1)
        time.sleep(0.5)
        return []

    monkeypatch.setattr(news_service, "fetch_all_feeds", slow_fetch)
    results = []
    threads = [threading.Thread(target=lambda: results.append(news_service.ingest_and_store_feeds(blocking=False)))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert sorted(r["status"] for r in results) == ["busy", "failed"]


def test_refresh_needs_the_app_header_and_is_throttled(client, monkeypatch):
    now = datetime.datetime.now(UTC)
    monkeypatch.setattr(news_service, "_http_get", fake_http(_documents(now)))
    assert client.post("/api/news/refresh").status_code == 403
    headers = {"X-Requested-With": "myfinonce"}
    first = client.post("/api/news/refresh", headers=headers)
    assert first.status_code == 200 and first.json()["status"] == "ok"
    second = client.post("/api/news/refresh", headers=headers).json()
    assert second["status"] == "recent"
    assert second["details"]["inserted"] == first.json()["details"]["inserted"]


def test_refresh_while_a_sweep_runs_answers_202(client, monkeypatch):
    assert news_service._INGEST_LOCK.acquire(blocking=False)
    try:
        res = client.post("/api/news/refresh", headers={"X-Requested-With": "myfinonce"})
        assert res.status_code == 202 and res.json()["status"] == "busy"
    finally:
        news_service._INGEST_LOCK.release()


# --- Relabel and migration 0012 ---------------------------------------------------------------


def test_relabel_cleans_and_reclassifies_old_rows(pg_db):
    con = connection.get_connection()
    insert_article(con, id="mint", url="https://www.livemint.com/x", source="Livemint", feed=None,
                   title="Toilet maker&nbsp;shares rise after strong results", category="commodities_forex",
                   region="us", sentiment="negative", classifier_version=0)
    insert_article(con, id="rbi", url="https://www.rbi.org.in/x", source="Reserve Bank of India", feed=None,
                   title="Money Market Operations as on October 14, 2026", classifier_version=0)
    con.close()
    assert news_service.relabel_outdated_rows() == 2
    con = connection.get_connection()
    try:
        row = con.execute("SELECT title, category, region, sentiment, classifier_version, feed FROM news_articles "
                          "WHERE id = 'mint'").fetchone()
        assert row == ("Toilet maker shares rise after strong results", "equities", "india", "positive",
                       news_service.CLASSIFIER_VERSION, "Livemint: Markets")
        assert con.execute("SELECT count(*) FROM news_articles WHERE id = 'rbi'").fetchone()[0] == 0
    finally:
        con.close()
    assert news_service.relabel_outdated_rows() == 0


def test_migration_0012_cleanup_is_targeted_and_idempotent(pg_db):
    now = datetime.datetime.now(UTC)
    con = connection.get_connection()
    legacy = dict(feed=None, classifier_version=0, cluster_id=None)
    insert_article(con, id="goog", url="https://news.google.com/rss/articles/abc", source="Google News", **legacy)
    insert_article(con, id="mc", url="https://www.moneycontrol.com/x", source="Moneycontrol", **legacy)
    insert_article(con, id="ft", url="https://www.ft.com/x", source="Financial Times", **legacy)
    insert_article(con, id="fed_enf", url="https://www.federalreserve.gov/newsevents/pressreleases/enforcement20261013a.htm",
                   source="Federal Reserve", **legacy)
    insert_article(con, id="fed_mon", url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261013a.htm",
                   source="Federal Reserve", **legacy)
    insert_article(con, id="rbi_ops", url="https://www.rbi.org.in/1", source="Reserve Bank of India",
                   title="Money Market Operations as on October 14, 2026", **legacy)
    rbi_pub = now + datetime.timedelta(hours=3)  # naive IST read as UTC: in the future
    insert_article(con, id="rbi_policy", url="https://www.rbi.org.in/2", source="Reserve Bank of India",
                   title="Monetary Policy Statement", published_at=rbi_pub, fetched_at=now, **legacy)
    insert_article(con, id="future", url="https://e.com/f", source="Livemint",
                   published_at=now + datetime.timedelta(hours=2), fetched_at=now, **legacy)
    insert_article(con, id="old", url="https://e.com/o", source="Livemint",
                   published_at=now - datetime.timedelta(days=40), **legacy)
    insert_article(con, id="keep", url="https://e.com/k", source="Livemint", **legacy)
    # Rows outside news_articles must be untouched: a holdings-free sanity check on TER.
    ter_before = con.execute("SELECT count(*) FROM ter_history").fetchone()[0]

    con.execute(migrate.NEWS_RULES_CLEANUP_SQL)
    con.execute(migrate.NEWS_RULES_CLEANUP_SQL)  # a re-run changes nothing further

    remaining = {r[0]: r for r in con.execute(
        "SELECT id, published_at, cluster_id, feed FROM news_articles").fetchall()}
    assert set(remaining) == {"fed_mon", "rbi_policy", "keep"}
    assert remaining["rbi_policy"][1] == rbi_pub - datetime.timedelta(hours=5, minutes=30)
    assert remaining["keep"][2] == "keep" and remaining["keep"][3] == "Livemint: Markets"
    assert remaining["fed_mon"][3] == "Fed: Monetary policy"
    assert con.execute("SELECT count(*) FROM ter_history").fetchone()[0] == ter_before
    con.close()


# --- R16: migration runner --------------------------------------------------------------------


def test_unknown_schema_head_refuses_instead_of_rerunning(pg_db):
    con = connection.get_connection()
    try:
        con.execute("INSERT INTO ter_history (scheme_code, ter_date, total_ter_pct) VALUES (1, DATE '2026-01-01', 1.0)")
        before = con.execute("SELECT count(*) FROM ter_history").fetchone()[0]
        con.execute("DELETE FROM alembic_version")
        con.execute("INSERT INTO alembic_version (version_num) VALUES ('9999_unknown')")
    finally:
        con.close()
    with pytest.raises(RuntimeError, match="Unknown schema head"):
        migrate.run_migrations()
    con = connection.get_connection()
    try:
        assert con.execute("SELECT count(*) FROM ter_history").fetchone()[0] == before
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "9999_unknown"
    finally:
        con.close()


# --- R20: daemon schedule ---------------------------------------------------------------------


def test_first_sweep_waits_while_stored_data_is_fresh(pg_db):
    assert news_service._initial_delay_seconds() == news_service.MIN_STARTUP_DELAY_SECONDS  # empty table
    now = datetime.datetime.now(UTC)
    con = connection.get_connection()
    insert_article(con, id="a", url="https://e.com/a", fetched_at=now - datetime.timedelta(minutes=5))
    con.close()
    delay = news_service._initial_delay_seconds(now)
    assert abs(delay - (news_service.SWEEP_INTERVAL_SECONDS - 300)) < 5


# --- Cache isolation (R28) --------------------------------------------------------------------


def test_l1_cache_isolation_from_core_registries():
    assert not any(news_service._NEWS_L1_CACHE is r for r in app_cache._registries)
    key = ("test_cat", "test_reg", 10, "")
    news_service._NEWS_L1_CACHE[key] = {"cached": True}
    app_cache.clear_all_caches()
    assert key in news_service._NEWS_L1_CACHE


def test_api_news_empty(client):
    body = client.get("/api/news").json()
    assert body["status"] == "ok" and body["total"] == 0 and body["articles"] == []
    assert body["feeds_total"] == len(FEEDS) and body["feeds_pending"] == len(FEEDS)
    assert body["retention_days"] == 30
