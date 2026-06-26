#!/usr/bin/env python3
"""
Replay backtest: pretend today is yesterday.

Uses yesterday's news (Google News date archive + cryptocurrency.cv if available)
and hourly BTC chart data to simulate entry → SL/TP/max-hold through the day.

Usage:
  python backtest_yesterday.py
  python backtest_yesterday.py --date 2026-06-25
  python backtest_yesterday.py --llm   # use Ollama instead of lexicon
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedparser
import pandas as pd
import yfinance as yf

WORK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK_DIR))

from market_cycle import get_cycle_state
from news_sentiment import day_trade_from_news
from position_manager import (
    TF_HOLD_HOURS,
    build_position_setup,
    format_exit_alert,
    format_trade_alert,
    normalize_timeframe,
)

RESULT_JSON = WORK_DIR / "backtest_yesterday.json"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _yesterday() -> date:
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


def fetch_yesterday_news(replay_date: date) -> list[dict]:
    """News published on replay_date."""
    articles: list[dict] = []
    next_day = replay_date + timedelta(days=1)

    # Google News date-range (works when archive API is blocked)
    g_url = (
        "https://news.google.com/rss/search?"
        f"q=bitcoin+OR+btc+OR+crypto+after:{replay_date}+before:{next_day}"
        "&hl=en-US&gl=US&ceid=US:en"
    )
    feed = feedparser.parse(g_url, agent=USER_AGENT)
    for entry in feed.entries:
        articles.append(
            {
                "title": entry.get("title", ""),
                "summary": entry.get("summary") or entry.get("description") or "",
                "source": "Google News",
                "url": entry.get("link", ""),
                "published": entry.get("published", ""),
            }
        )

    # cryptocurrency.cv archive (optional)
    try:
        from historical_news import fetch_archive_day

        for raw in fetch_archive_day(replay_date, use_cache=True):
            articles.append(
                {
                    "title": raw.get("title", ""),
                    "summary": raw.get("description") or raw.get("summary") or "",
                    "source": raw.get("source") or "cryptocurrency.cv",
                    "url": raw.get("url") or raw.get("link") or "",
                    "published": raw.get("published_at") or raw.get("pubDate") or "",
                }
            )
    except Exception:
        pass

    # Dedupe by title
    seen: set[str] = set()
    unique = []
    for a in articles:
        key = (a.get("title") or "").lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(a)
    return unique


def _parse_published(text: str) -> datetime | None:
    if not text:
        return None
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(text).astimezone(timezone.utc)
    except Exception:
        return None


def news_before_cutoff(articles: list[dict], cutoff: datetime) -> list[dict]:
    """Only news known before the simulated entry time."""
    out = []
    for a in articles:
        pub = _parse_published(a.get("published", ""))
        if pub is None or pub <= cutoff:
            out.append(a)
    return out if out else articles[:20]


def fetch_hourly_bars(replay_date: date) -> pd.DataFrame:
    start = replay_date - timedelta(days=1)
    end = replay_date + timedelta(days=2)
    raw = yf.download(
        "BTC-USD",
        start=str(start),
        end=str(end),
        interval="1h",
        progress=False,
        auto_adjust=True,
    )
    if raw.empty:
        return raw

    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [c[0] for c in raw.columns]

    raw = raw.reset_index()
    raw["Datetime"] = pd.to_datetime(raw["Datetime"], utc=True)
    day_start = datetime.combine(replay_date, datetime.min.time(), tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    return raw[(raw["Datetime"] >= day_start) & (raw["Datetime"] < day_end)].copy()


def signal_from_lexicon(texts: list[str], macro_phase: str, prior_ohlc: list | None = None, articles: list[dict] | None = None, entry_price: float | None = None) -> dict:
    import os
    from signal_engine import build_day_trade_signal

    if os.environ.get("SIGNAL_MODE") == "hybrid":
        from signal_hybrid import build_hybrid_signal
        return build_hybrid_signal(
            texts, articles or [], macro_phase=macro_phase, prior_ohlc=prior_ohlc, entry_price=entry_price
        )
    return build_day_trade_signal(texts, macro_phase=macro_phase, prior_ohlc=prior_ohlc)


def signal_from_llm(articles: list[dict], replay_date: date, market: dict | None = None) -> dict:
    from btc_superduper_predictor import analyze_with_llm, extract_day_trade_signal

    analysis = analyze_with_llm(articles, market)
    if "error" in analysis:
        raise RuntimeError(analysis["error"])
    dt = analysis.get("day_trade") or extract_day_trade_signal(analysis)
    dt["timeframe"] = normalize_timeframe(dt.get("timeframe"))
    return dt


def simulate_trade(
    setup: dict[str, Any],
    hourly: pd.DataFrame,
    entry_time: datetime,
) -> dict[str, Any]:
    """Walk hourly bars after entry; return first SL/TP/timeout."""
    if setup["signal"] == "FLAT" or hourly.empty:
        return {"outcome": "NO_TRADE", "pnl_pct": 0.0}

    after = hourly[hourly["Datetime"] >= entry_time].copy()
    if after.empty:
        return {"outcome": "NO_DATA", "pnl_pct": 0.0}

    entry = setup["entry_price"]
    sl = setup["stop_loss"]
    tp = setup["take_profit"]
    sig = setup["signal"]
    max_h = setup["max_hold_hours"]
    deadline = entry_time + timedelta(hours=max_h)

    for _, row in after.iterrows():
        ts = row["Datetime"].to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        high = float(row["High"])
        low = float(row["Low"])
        close = float(row["Close"])

        if sig == "LONG":
            if low <= sl:
                return _result("STOP_LOSS", sl, entry, ts, setup)
            if high >= tp:
                return _result("TAKE_PROFIT", tp, entry, ts, setup)
        else:
            if high >= sl:
                return _result("STOP_LOSS", sl, entry, ts, setup)
            if low <= tp:
                return _result("TAKE_PROFIT", tp, entry, ts, setup)

        if ts >= deadline:
            return _result("MAX_HOLD", close, entry, ts, setup)

    last = after.iloc[-1]
    return _result("SESSION_END", float(last["Close"]), entry, last["Datetime"].to_pydatetime(), setup)


def _result(outcome: str, exit_price: float, entry: float, ts: datetime, setup: dict) -> dict:
    if setup["signal"] == "LONG":
        pnl = (exit_price - entry) / entry * 100
    else:
        pnl = (entry - exit_price) / entry * 100
    return {
        "outcome": outcome,
        "exit_price": round(exit_price, 2),
        "exit_time": ts.isoformat(),
        "pnl_pct": round(pnl, 3),
        "setup": setup,
    }


def run_replay(
    replay_date: date,
    *,
    poll_hours: list[int] | None = None,
    use_llm: bool = False,
) -> dict:
    """Simulate scheduler polls through replay_date; first actionable signal wins."""
    poll_hours = poll_hours or [8, 12, 16, 20]
    cycle = get_cycle_state(replay_date)
    all_news = fetch_yesterday_news(replay_date)
    hourly = fetch_hourly_bars(replay_date)

    if hourly.empty:
        return {"error": "No hourly chart data", "replay_date": replay_date.isoformat()}

    day_open = float(hourly.iloc[0]["Open"])
    day_close = float(hourly.iloc[-1]["Close"])
    day_low = float(hourly["Low"].min())
    day_high = float(hourly["High"].max())

    poll_log: list[dict] = []
    best: dict | None = None

    for hour in poll_hours:
        entry_time = datetime.combine(replay_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(
            hours=hour
        )
        news = news_before_cutoff(all_news, entry_time)
        texts = [f"{a['title']} {a.get('summary','')}" for a in news]

        prior_ohlc = [
            {"open": r["Open"], "high": r["High"], "low": r["Low"], "close": r["Close"]}
            for _, r in hourly[hourly["Datetime"] < entry_time].tail(24).iterrows()
        ]

        entry_row = hourly[hourly["Datetime"] >= entry_time].head(1)
        entry_price = float(entry_row.iloc[0]["Open"]) if not entry_row.empty else day_open

        if use_llm and os.environ.get("OLLAMA_MODEL"):
            try:
                market_ctx = {
                    "price_usd": entry_price,
                    "change_24h_pct": (entry_price - day_open) / day_open * 100 if day_open else 0,
                    "ohlc": prior_ohlc,
                }
                sig_info = signal_from_llm(news, replay_date, market_ctx)
            except Exception as exc:
                sig_info = {"signal": "FLAT", "confidence": 0, "timeframe": "4h", "error": str(exc)}
        else:
            sig_info = signal_from_lexicon(texts, cycle.phase, prior_ohlc, news, entry_price)

        signal = sig_info["signal"]
        confidence = int(sig_info["confidence"])
        timeframe = normalize_timeframe(sig_info.get("timeframe", "4h"))

        from signal_engine import is_late_chase

        if signal in ("LONG", "SHORT") and is_late_chase(hour, signal, day_open, entry_price):
            signal = "FLAT"
            confidence = 40
            sig_info = {**sig_info, "signal": "FLAT", "reject_reason": "late_chase"}

        poll_log.append(
            {
                "poll_utc": entry_time.isoformat(),
                "news_count": len(news),
                "signal": signal,
                "confidence": confidence,
                "timeframe": timeframe,
                "trade_style": sig_info.get("trade_style"),
                "news_score": sig_info.get("news_score"),
            }
        )

        if signal in ("LONG", "SHORT") and best is None:
            setup = build_position_setup(
                signal, confidence, timeframe, entry_price, prior_ohlc or None,
                trade_style=sig_info.get("trade_style"),
            )
            trade_result = simulate_trade(setup, hourly, entry_time)
            best = {
                "entry_time_utc": entry_time.isoformat(),
                "poll_hour": hour,
                "news_articles_at_entry": len(news),
                "sample_headlines": [a["title"][:100] for a in news[:8]],
                "signal_method": "ollama" if use_llm else "confluence",
                "signal": signal,
                "confidence": confidence,
                "timeframe": timeframe,
                "trade_style": sig_info.get("trade_style"),
                "subtrends": sig_info.get("subtrends"),
                "news_score": sig_info.get("news_score"),
                "position_setup": setup,
                "entry_alert": format_trade_alert(setup) if setup else None,
                "trade_result": trade_result,
            }

    if best is None:
        # No actionable signal — report last poll as FLAT
        last = poll_log[-1] if poll_log else {}
        best = {
            "entry_time_utc": None,
            "poll_hour": None,
            "news_articles_at_entry": last.get("news_count", 0),
            "sample_headlines": [a["title"][:100] for a in all_news[:8]],
            "signal_method": "ollama" if use_llm else "confluence",
            "signal": "FLAT",
            "confidence": 0,
            "timeframe": "4h",
            "trade_style": "flat",
            "position_setup": None,
            "entry_alert": None,
            "trade_result": {"outcome": "NO_TRADE", "pnl_pct": 0.0},
        }

    report = {
        "replay_date": replay_date.isoformat(),
        "pretend_today_is": replay_date.isoformat(),
        "poll_hours_utc": poll_hours,
        "poll_log": poll_log,
        "macro_phase": cycle.phase,
        "news_articles_total": len(all_news),
        **best,
        "chart": {
            "day_open": day_open,
            "day_high": day_high,
            "day_low": day_low,
            "day_close": day_close,
            "day_range_pct": round((day_high - day_low) / day_open * 100, 2) if day_open else 0,
        },
        "buy_hold_pnl_pct": round((day_close - day_open) / day_open * 100, 3) if day_open else 0,
    }

    tr = best.get("trade_result", {})
    if best.get("position_setup") and tr.get("outcome") not in ("NO_TRADE", "NO_DATA"):
        setup = best["position_setup"]
        fake_exit = {
            "position": setup,
            "reason": tr["outcome"],
            "current_price": tr.get("exit_price", day_close),
            "age_hours": TF_HOLD_HOURS.get(setup.get("timeframe", "4h"), 4),
        }
        report["exit_alert"] = format_exit_alert(fake_exit).replace(
            tr["outcome"], f"{tr['outcome']} @ ${tr.get('exit_price', 0):,.0f}"
        )

    RESULT_JSON.write_text(json.dumps(report, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Yesterday replay backtest")
    parser.add_argument("--date", help="Replay date YYYY-MM-DD (default: yesterday UTC)")
    parser.add_argument("--poll-hours", default="8,12,16,20", help="UTC poll hours e.g. 8,12,16,20")
    parser.add_argument("--llm", action="store_true", help="Use Ollama for signal")
    args = parser.parse_args()

    replay_date = date.fromisoformat(args.date) if args.date else _yesterday()
    poll_hours = [int(h.strip()) for h in args.poll_hours.split(",") if h.strip()]

    print(f"Yesterday replay backtest — pretending today is {replay_date}")
    print(f"Scheduler polls UTC: {poll_hours}\n")

    report = run_replay(replay_date, poll_hours=poll_hours, use_llm=args.llm)

    if report.get("error"):
        print(report["error"])
        return 1

    print("Poll log:")
    for p in report.get("poll_log", []):
        hr = p["poll_utc"][11:13]
        print(f"  {hr}:00 UTC — {p['signal']} {p['confidence']}% ({p['news_count']} news)")
    print()
    print(f"News total: {report['news_articles_total']} · Macro: {report['macro_phase']}")
    print()
    if report.get("entry_alert"):
        print(report["entry_alert"])
        print()
    ch = report["chart"]
    print(f"Chart {replay_date}: open ${ch['day_open']:,.0f} → close ${ch['day_close']:,.0f} "
          f"(range {ch['day_range_pct']:.1f}%)")
    tr = report["trade_result"]
    print(f"Outcome: {tr['outcome']} · PnL {tr.get('pnl_pct', 0):+.2f}%")
    if tr.get("exit_time"):
        print(f"Exit: ${tr.get('exit_price', 0):,.0f} at {tr['exit_time']}")
    print(f"Buy & hold same day: {report['buy_hold_pnl_pct']:+.2f}%")
    print(f"\nWrote {RESULT_JSON.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
