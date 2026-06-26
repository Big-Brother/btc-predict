"""Load historical crypto news for backtesting (2013+)."""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests

WORK_DIR = Path(__file__).resolve().parent
DATA_DIR = WORK_DIR / "data"
CACHE_DIR = DATA_DIR / "news_cache"
KAGGLE_GLOB = ("cryptonews*.csv", "crypto_news*.csv", "news*.csv")

ARCHIVE_BASE = "https://cryptocurrency.cv/api/archive"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DATE_IN_URL = re.compile(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})/?")


def _parse_date_from_url(url: str) -> date | None:
    if not url:
        return None
    m = DATE_IN_URL.search(url)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _normalize_row(title: str, summary: str, pub_date: date, source: str, url: str = "") -> dict:
    return {
        "date": pub_date,
        "title": (title or "").strip(),
        "summary": (summary or "")[:700],
        "source": source or "Unknown",
        "url": url or "",
    }


def load_kaggle_news(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """
    Kaggle: news-about-major-cryptocurrencies-20132018-40k
    Drop CSV files into work/data/ (cryptonews.csv etc.)
    Columns: url, title, text, html, year, author, source (varies)
    """
    rows: list[dict] = []
    if not data_dir.exists():
        return pd.DataFrame(columns=["date", "title", "summary", "source", "url"])

    files: list[Path] = []
    for pattern in KAGGLE_GLOB:
        files.extend(data_dir.glob(pattern))
    files = sorted(set(files))

    for path in files:
        df = pd.read_csv(path, low_memory=False)
        colmap = {c.lower(): c for c in df.columns}
        title_col = colmap.get("title")
        text_col = colmap.get("text") or colmap.get("summary")
        url_col = colmap.get("url")
        year_col = colmap.get("year")
        source_col = colmap.get("source") or colmap.get("author")

        if not title_col:
            continue

        for _, r in df.iterrows():
            title = str(r.get(title_col, "") or "")
            if not title or title == "nan":
                continue
            text = str(r.get(text_col, "") or "") if text_col else ""
            url = str(r.get(url_col, "") or "") if url_col else ""
            blob = f"{title} {text}".lower()
            if "bitcoin" not in blob and "btc" not in blob:
                continue

            pub = _parse_date_from_url(url)
            if pub is None and year_col:
                try:
                    y = int(r.get(year_col))
                    pub = date(y, 6, 15)
                except (TypeError, ValueError):
                    continue
            if pub is None:
                continue

            source = str(r.get(source_col, "Kaggle") if source_col else "Kaggle")
            rows.append(_normalize_row(title, text, pub, source, url))

    if not rows:
        return pd.DataFrame(columns=["date", "title", "summary", "source", "url"])

    out = pd.DataFrame(rows)
    out["date"] = pd.to_datetime(out["date"]).dt.date
    return out.sort_values("date").reset_index(drop=True)


def fetch_archive_day(day: date, *, use_cache: bool = True) -> list[dict]:
    """cryptocurrency.cv historical archive (≈ Sep 2017 → 2025)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{day.isoformat()}.json"

    if use_cache and cache_file.exists():
        payload = json.loads(cache_file.read_text())
        return payload.get("articles", [])

    params = {"date": day.isoformat(), "limit": 50, "ticker": "BTC"}
    resp = requests.get(
        ARCHIVE_BASE,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    if resp.status_code == 429 or (
        resp.status_code == 403 and "rate" in resp.text.lower()
    ):
        raise RuntimeError(f"Archive rate-limited for {day}. Use cache or retry later.")
    resp.raise_for_status()
    payload = resp.json()
    articles = payload.get("articles", [])

    if use_cache:
        cache_file.write_text(json.dumps({"articles": articles}, indent=2))
    return articles


def load_archive_news(
    start: date,
    end: date,
    *,
    use_cache: bool = True,
    pause_sec: float = 1.2,
) -> pd.DataFrame:
    rows: list[dict] = []
    cur = start
    while cur <= end:
        try:
            articles = fetch_archive_day(cur, use_cache=use_cache)
        except Exception:
            cur = date.fromordinal(cur.toordinal() + 1)
            if pause_sec:
                time.sleep(pause_sec)
            continue

        for a in articles:
            title = a.get("title") or ""
            if not title:
                continue
            rows.append(
                _normalize_row(
                    title,
                    a.get("description") or a.get("summary") or "",
                    cur,
                    a.get("source") or "cryptocurrency.cv",
                    a.get("url") or a.get("link") or "",
                )
            )
        cur = date.fromordinal(cur.toordinal() + 1)
        if pause_sec:
            time.sleep(pause_sec)

    if not rows:
        return pd.DataFrame(columns=["date", "title", "summary", "source", "url"])
    out = pd.DataFrame(rows)
    out["date"] = pd.to_datetime(out["date"]).dt.date
    return out.sort_values("date").reset_index(drop=True)


def load_all_historical_news(
    start: date = date(2013, 1, 1),
    end: date | None = None,
    *,
    archive_start: date = date(2017, 9, 1),
    use_archive_cache: bool = True,
) -> pd.DataFrame:
    """Merge Kaggle (2013–2018) + cryptocurrency.cv archive (2017+)."""
    end = end or datetime.now().date()
    parts = [load_kaggle_news()]

    if end >= archive_start:
        arch = load_archive_news(
            max(start, archive_start),
            end,
            use_cache=use_archive_cache,
        )
        if not arch.empty:
            parts.append(arch)

    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame(columns=["date", "title", "summary", "source", "url"])

    merged = pd.concat(parts, ignore_index=True)
    merged = merged.drop_duplicates(subset=["title", "date"], keep="first")
    merged = merged[(merged["date"] >= start) & (merged["date"] <= end)]
    return merged.sort_values("date").reset_index(drop=True)
