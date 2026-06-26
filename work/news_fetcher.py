"""Live BTC news ingestion — cascade: cryptocurrency.cv → CoinGecko → RSS."""

from __future__ import annotations

import logging
import os
import re
from html import unescape
from typing import Any

import feedparser
import requests

logger = logging.getLogger(__name__)

NEWS_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CV_API_BASE = "https://cryptocurrency.cv/api/news"
CV_CATEGORIES = ("bitcoin", "general", "trading", "institutional", "etf", "macro")
CV_LIMIT = 10

COINGECKO_NEWS_URL = "https://api.coingecko.com/api/v3/news"

CRYPTO_RSS_FEEDS = (
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("CryptoSlate", "https://cryptoslate.com/feed/"),
    ("Bitcoin.com News", "https://news.bitcoin.com/feed/"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("Blockworks", "https://blockworks.co/feed"),
)

# Direct US government / central bank RSS (filtered for BTC/market relevance)
GOV_RSS_FEEDS = (
    ("SEC", "https://www.sec.gov/news/pressreleases.rss"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
)

# Google News RSS — US gov + regulation (CLARITY, GENIUS, Trump admin crypto policy)
GOOGLE_NEWS_FEEDS = (
    (
        "US Gov Crypto Regulation",
        "https://news.google.com/rss/search?q=(CLARITY+Act+OR+GENIUS+Act+OR+crypto+market+structure)"
        "+OR+digital+assets+regulation&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "SEC Crypto",
        "https://news.google.com/rss/search?q=site:sec.gov+(bitcoin+OR+crypto+OR+stablecoin+OR+ETF)"
        "&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "Treasury Crypto",
        "https://news.google.com/rss/search?q=site:home.treasury.gov+(crypto+OR+bitcoin+OR+stablecoin)"
        "&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "CFTC Crypto",
        "https://news.google.com/rss/search?q=site:cftc.gov+(crypto+OR+bitcoin+OR+virtual+currency)"
        "&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "White House Digital Assets",
        "https://news.google.com/rss/search?q=site:whitehouse.gov+(crypto+OR+digital+assets+OR+bitcoin)"
        "&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "Trump Admin Crypto",
        "https://news.google.com/rss/search?q=Trump+(crypto+OR+bitcoin+OR+stablecoin+OR+digital+assets)"
        "&hl=en-US&gl=US&ceid=US:en",
    ),
)

MACRO_RSS_FEEDS = (
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
)

MIN_ARTICLES = 5
MAX_ARTICLES = 40
RSS_ENTRIES_PER_FEED = 8
GOOGLE_NEWS_ENTRIES = 6
GOV_RSS_ENTRIES = 10

_TAG_RE = re.compile(r"<[^>]+>")
_BTC_RE = re.compile(r"bitcoin|\bbtc\b", re.I)
_MARKET_IMPACT_RE = re.compile(
    r"bitcoin|\bbtc\b|crypto|digital asset|stablecoin|blockchain|ethereum|\beth\b|"
    r"\bsec\b|cftc|treasury|fed\b|fomc|interest rate|inflation|etf|genius act|clarity act|"
    r"market structure|executive order|sanction|tariff|regulat|trump|stable coin|"
    r"depeg|mining|halving|derivatives|futures|spot etf",
    re.I,
)


class NewsFetchError(RuntimeError):
    pass


def _strip_html(text: str) -> str:
    return unescape(_TAG_RE.sub(" ", text or "")).strip()


def _normalize_article(raw: dict[str, Any], source: str | None = None) -> dict[str, Any] | None:
    title = (raw.get("title") or "").strip()
    if not title:
        return None
    summary = _strip_html(raw.get("description") or raw.get("summary") or "")[:700]
    return {
        "title": title,
        "summary": summary,
        "source": raw.get("source") or source or "Unknown",
        "url": raw.get("url") or raw.get("link"),
        "published": raw.get("published_at") or raw.get("pubDate") or raw.get("published") or raw.get("created_at"),
    }


def _dedupe_key(article: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", (article.get("title") or "").lower()).strip()


def _merge_articles(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {_dedupe_key(a) for a in existing}
    merged = list(existing)
    for article in incoming:
        key = _dedupe_key(article)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(article)
    return merged


def _session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {"User-Agent": user_agent, "Accept": "application/json, application/rss+xml, */*"}
    )
    return session


def _fetch_cryptocurrency_cv(session: requests.Session) -> list[dict[str, Any]]:
    """Primary: cryptocurrency.cv aggregator."""
    articles: list[dict[str, Any]] = []

    queries = (
        {"limit": CV_LIMIT, "coins": "BTC"},
        *[{"limit": CV_LIMIT, "category": category} for category in CV_CATEGORIES],
    )

    for params in queries:
        try:
            resp = session.get(CV_API_BASE, params=params, timeout=12)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.debug("cryptocurrency.cv %s failed: %s", params, exc)
            continue

        batch = []
        for raw in payload.get("articles", []):
            article = _normalize_article(raw)
            if article:
                batch.append(article)
        if batch:
            articles = _merge_articles(articles, batch)

    if articles:
        logger.info("cryptocurrency.cv: %s articles", len(articles))
    return articles


def _fetch_coingecko(session: requests.Session) -> list[dict[str, Any]]:
    """Fallback 1: CoinGecko news (PRO endpoint — skipped if unavailable)."""
    articles: list[dict[str, Any]] = []
    headers = {}
    api_key = os.environ.get("COINGECKO_API_KEY")
    if api_key:
        headers["x-cg-pro-api-key"] = api_key

    try:
        resp = session.get(
            COINGECKO_NEWS_URL,
            params={"per_page": 20},
            headers=headers,
            timeout=12,
        )
        if resp.status_code == 401:
            logger.info("CoinGecko news skipped: PRO API required")
            return []
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        logger.debug("CoinGecko news failed: %s", exc)
        return []

    if isinstance(payload, dict) and payload.get("status", {}).get("error_code"):
        logger.info("CoinGecko news skipped: %s", payload["status"].get("error_message"))
        return []

    for item in payload if isinstance(payload, list) else []:
        blob = f"{item.get('title', '')} {item.get('description', '')}"
        if not _BTC_RE.search(blob):
            continue
        article = _normalize_article(
            {
                "title": item.get("title"),
                "description": item.get("description"),
                "source": item.get("news_site", "CoinGecko"),
                "url": item.get("url"),
                "created_at": item.get("created_at"),
            }
        )
        if article:
            articles.append(article)

    if articles:
        logger.info("CoinGecko: %s BTC-related articles", len(articles))
    return articles


_SKIP_TITLE_RE = re.compile(
    r"sec\.gov\s*\|\s*home|home\s*-\s*sec\.gov|^search filings|edgar full text search|"
    r"^press releases\s*-\s*u\.s\. department|^role of the treasury|"
    r"^commitments of traders|^giovanni pennetta|^sanders family office",
    re.I,
)


def _is_market_relevant(article: dict[str, Any]) -> bool:
    title = (article.get("title") or "").strip()
    if _SKIP_TITLE_RE.search(title):
        return False
    blob = f"{title} {article.get('summary', '')}"
    return bool(_MARKET_IMPACT_RE.search(blob))


def _fetch_rss_feeds(
    user_agent: str,
    feeds: tuple[tuple[str, str], ...],
    *,
    per_feed: int,
    require_relevance: bool = False,
) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    for source, url in feeds:
        try:
            feed = feedparser.parse(url, agent=user_agent)
            for entry in feed.entries[:per_feed]:
                article = _normalize_article(
                    {
                        "title": entry.get("title"),
                        "description": entry.get("summary") or entry.get("description"),
                        "source": source,
                        "url": entry.get("link"),
                        "published": entry.get("published"),
                    }
                )
                if not article:
                    continue
                if require_relevance and not _is_market_relevant(article):
                    continue
                articles.append(article)
        except Exception as exc:
            logger.debug("RSS feed %s failed: %s", url, exc)
    return articles


def _fetch_crypto_rss(user_agent: str) -> list[dict[str, Any]]:
    articles = _fetch_rss_feeds(
        user_agent, CRYPTO_RSS_FEEDS, per_feed=RSS_ENTRIES_PER_FEED, require_relevance=False
    )
    if articles:
        logger.info("Crypto RSS: %s articles", len(articles))
    return articles


def _fetch_gov_and_regulatory(user_agent: str) -> list[dict[str, Any]]:
    """US gov, regulation (CLARITY/GENIUS), and macro sources that move BTC."""
    articles: list[dict[str, Any]] = []

    articles = _merge_articles(
        articles,
        _fetch_rss_feeds(
            user_agent,
            GOOGLE_NEWS_FEEDS,
            per_feed=GOOGLE_NEWS_ENTRIES,
            require_relevance=True,
        ),
    )
    articles = _merge_articles(
        articles,
        _fetch_rss_feeds(
            user_agent,
            GOV_RSS_FEEDS,
            per_feed=GOV_RSS_ENTRIES,
            require_relevance=True,
        ),
    )
    articles = _merge_articles(
        articles,
        _fetch_rss_feeds(
            user_agent,
            MACRO_RSS_FEEDS,
            per_feed=6,
            require_relevance=True,
        ),
    )

    if articles:
        logger.info("Gov/regulatory/macro: %s articles", len(articles))
    return articles


def fetch_live_news(
    *,
    min_articles: int = MIN_ARTICLES,
    max_articles: int = MAX_ARTICLES,
    user_agent: str = NEWS_USER_AGENT,
) -> list[dict[str, Any]]:
    session = _session(user_agent)

    # Gov/regulatory first so CLARITY/GENIUS/SEC items survive the cap
    articles = _fetch_gov_and_regulatory(user_agent)
    articles = _merge_articles(articles, _fetch_cryptocurrency_cv(session))
    articles = _merge_articles(articles, _fetch_coingecko(session))
    articles = _merge_articles(articles, _fetch_crypto_rss(user_agent))

    articles = articles[:max_articles]
    logger.info("Total articles loaded: %s", len(articles))

    if len(articles) < min_articles:
        raise NewsFetchError(
            f"Only {len(articles)} live articles fetched (need at least {min_articles}). "
            "Check network connectivity and news source availability."
        )

    return articles
