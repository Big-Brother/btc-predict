#!/usr/bin/env python3
"""
News-driven day-trade backtest from 2013+.

Data sources:
  1. Kaggle CSV in work/data/ (2013–2018, ~40k articles) — REQUIRED for 2013 start
     https://www.kaggle.com/datasets/kashnitsky/news-about-major-cryptocurrencies-20132018-40k
  2. cryptocurrency.cv /api/archive (Sep 2017+) — optional, cached in work/data/news_cache/

bitcoinforecast.com: price forecasts + live Google News — NO historical news archive.

Usage:
  # After placing cryptonews.csv in work/data/:
  python backtest_news.py

  # Quick test on cached archive days only:
  python backtest_news.py --from 2017-09-01 --no-kaggle
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

WORK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK_DIR))

from backtest import load_btc_history, run_macro_backtest  # noqa: E402
from historical_news import DATA_DIR, load_all_historical_news, load_kaggle_news
from market_cycle import get_cycle_state
from news_sentiment import day_trade_from_news

SUMMARY_JSON = WORK_DIR / "backtest_news_summary.json"
EQUITY_CSV = WORK_DIR / "backtest_news_equity.csv"


def build_daily_news_index(news: pd.DataFrame) -> dict[date, list[str]]:
    by_day: dict[date, list[str]] = {}
    for _, row in news.iterrows():
        d = row["date"]
        text = f"{row.get('title', '')} {row.get('summary', '')}"
        by_day.setdefault(d, []).append(text)
    return by_day


def run_news_backtest(
    prices: pd.DataFrame,
    news_by_day: dict[date, list[str]],
    *,
    require_macro_alignment: bool = False,
) -> tuple[pd.DataFrame, dict]:
    df = prices.copy()
    df["phase"] = df["date"].apply(lambda d: get_cycle_state(d).phase)

    signals = []
    for _, row in df.iterrows():
        d = row["date"]
        texts = news_by_day.get(d, [])
        if not texts:
            signals.append({"signal": "FLAT", "confidence": 0, "trade_style": "flat", "news_count": 0})
            continue
        sig, conf, trade_style = day_trade_from_news(texts, macro_phase=row["phase"])
        if require_macro_alignment:
            macro_bear = row["phase"] == "downtrend"
            macro_ok = (macro_bear and sig == "SHORT") or (not macro_bear and sig == "LONG") or sig == "FLAT"
            if not macro_ok:
                sig = "FLAT"
                conf = 30
        signals.append(
            {"signal": sig, "confidence": conf, "trade_style": trade_style, "news_count": len(texts)}
        )

    sig_df = pd.DataFrame(signals)
    df = pd.concat([df.reset_index(drop=True), sig_df], axis=1)

    position_map = {"LONG": 1.0, "SHORT": -1.0, "FLAT": 0.0}
    df["news_position"] = df["signal"].map(position_map).fillna(0.0)
    df["macro_position"] = df["phase"].map({"bull": 1.0, "downtrend": -1.0})

    df["daily_return"] = df["close"].pct_change().clip(-0.35, 0.35)

    # Day trade: only active on signal days; flat = cash
    df["news_strategy_return"] = df["news_position"].shift(1) * df["daily_return"]
    df["macro_strategy_return"] = df["macro_position"].shift(1) * df["daily_return"]

    df["news_equity"] = (1 + df["news_strategy_return"].fillna(0)).cumprod()
    df["macro_equity"] = (1 + df["macro_strategy_return"].fillna(0)).cumprod()
    df["buy_hold_equity"] = (1 + df["daily_return"].fillna(0)).cumprod()

    years = max((df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25, 0.01)
    active = df[df["news_count"] > 0]

    def stats(col: str) -> dict:
        eq = df[col]
        ret = df[col.replace("_equity", "_strategy_return")]
        total = eq.iloc[-1] - 1
        cagr = eq.iloc[-1] ** (1 / years) - 1 if eq.iloc[-1] > 0 else 0
        peak = eq.cummax()
        mdd = float(((eq - peak) / peak).min())
        sharpe = float(ret.dropna().mean() / ret.dropna().std() * (365**0.5)) if ret.dropna().std() else 0
        return {
            "total_return_pct": round(total * 100, 2),
            "cagr_pct": round(cagr * 100, 2),
            "max_drawdown_pct": round(mdd * 100, 2),
            "sharpe": round(sharpe, 2),
        }

    signal_counts = df["signal"].value_counts().to_dict()
    macro_aligned_rate = 0.0
    if (df["signal"] != "FLAT").any():
        def _macro_aligned(row):
            if row["signal"] == "FLAT":
                return True
            bear = row["phase"] == "downtrend"
            return (bear and row["signal"] == "SHORT") or (not bear and row["signal"] == "LONG")

        macro_aligned_rate = float(df.apply(_macro_aligned, axis=1).loc[df["signal"] != "FLAT"].mean())

    summary = {
        "backtest_type": "news_day_trade_lexicon",
        "data_from": str(df["date"].iloc[0]),
        "data_to": str(df["date"].iloc[-1]),
        "years": round(years, 2),
        "news_article_days": int((df["news_count"] > 0).sum()),
        "total_news_days_in_sample": len(active),
        "signal_distribution": signal_counts,
        "macro_aligned_rate": round(macro_aligned_rate, 3),
        "news_strategy": stats("news_equity"),
        "macro_strategy": stats("macro_equity"),
        "buy_hold": {
            "total_return_pct": round((df["buy_hold_equity"].iloc[-1] - 1) * 100, 2),
        },
        "sources": {
            "kaggle_dir": str(DATA_DIR),
            "kaggle_required_for_2013": True,
            "cryptocurrency_cv_archive_from": "2017-09-01",
            "bitcoinforecast_com": "NO archive — live Google News only on forecast site",
        },
        "note": (
            "Lexicon proxy for LLM day trades. Download Kaggle CSV to work/data/ for 2013–2017 coverage. "
            "True LLM replay on 4000+ days is not run here (use --sample-llm-days for spot checks)."
        ),
    }
    return df, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="News day-trade backtest")
    parser.add_argument("--from", dest="date_from", default="2013-01-01")
    parser.add_argument("--to", dest="date_to", default=None)
    parser.add_argument("--no-kaggle", action="store_true", help="Skip Kaggle CSV load")
    parser.add_argument("--macro-aligned-only", action="store_true")
    args = parser.parse_args()

    start = date.fromisoformat(args.date_from)
    end = date.fromisoformat(args.date_to) if args.date_to else datetime.now(timezone.utc).date()

    print("News day-trade backtest")
    print(f"Period: {start} → {end}\n")

    kaggle = pd.DataFrame()
    if not args.no_kaggle:
        kaggle = load_kaggle_news()
        if kaggle.empty:
            print(f"⚠ No Kaggle CSV in {DATA_DIR}/")
            print("  Download: https://www.kaggle.com/datasets/kashnitsky/news-about-major-cryptocurrencies-20132018-40k")
            print("  Place cryptonews.csv (or cryptonews1.csv + cryptonews2.csv) in work/data/\n")
        else:
            print(f"Kaggle news: {len(kaggle)} BTC-related rows ({kaggle['date'].min()} → {kaggle['date'].max()})")

    news = kaggle if not kaggle.empty else load_all_historical_news(start, end, use_archive_cache=True)
    if news.empty:
        print("No historical news loaded. Cannot run news backtest.")
        return 1

    news = news[(news["date"] >= start) & (news["date"] <= end)]
    print(f"News sample: {len(news)} articles on {news['date'].nunique()} days\n")

    prices = load_btc_history()
    prices = prices[(prices["date"] >= start) & (prices["date"] <= end)].reset_index(drop=True)
    print(f"Price bars: {len(prices)} ({prices['date'].iloc[0]} → {prices['date'].iloc[-1]})\n")

    news_by_day = build_daily_news_index(news)
    df, summary = run_news_backtest(
        prices,
        news_by_day,
        require_macro_alignment=args.macro_aligned_only,
    )

    df[
        [
            "date",
            "close",
            "phase",
            "signal",
            "confidence",
            "news_count",
            "news_equity",
            "macro_equity",
            "buy_hold_equity",
        ]
    ].to_csv(EQUITY_CSV, index=False)
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2))

    ns = summary["news_strategy"]
    ms = summary["macro_strategy"]
    print("=== NEWS DAY-TRADE (lexicon proxy) ===")
    print(f"  Days with news: {summary['news_article_days']}")
    print(f"  Signals: {summary['signal_distribution']}")
    print(f"  Macro-aligned trades: {summary['macro_aligned_rate']:.0%}")
    print(f"  Return: {ns['total_return_pct']:+,.1f}% | CAGR {ns['cagr_pct']:+.1f}% | Sharpe {ns['sharpe']}")
    print()
    print("=== MACRO 364/1064 (same period) ===")
    print(f"  Return: {ms['total_return_pct']:+,.1f}% | CAGR {ms['cagr_pct']:+.1f}% | Sharpe {ms['sharpe']}")
    print()
    print(f"Buy & hold: {summary['buy_hold']['total_return_pct']:+,.1f}%")
    print(f"\nWrote {EQUITY_CSV.name}, {SUMMARY_JSON.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
