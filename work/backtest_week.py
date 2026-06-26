#!/usr/bin/env python3
"""
Backtest every day in a date range: news (lexicon) vs trend-only vs buy-and-hold.

Usage:
  python backtest_week.py                    # Mon–today this week (UTC)
  python backtest_week.py --from 2026-06-23 --to 2026-06-26
  python backtest_week.py --llm              # Ollama instead of lexicon (slow)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WORK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK_DIR))

from backtest_yesterday import (
    fetch_hourly_bars,
    fetch_yesterday_news,
    news_before_cutoff,
    run_replay,
    signal_from_lexicon,
    simulate_trade,
)
from market_cycle import get_cycle_state
from position_manager import build_position_setup, normalize_timeframe
from trend_context import compute_price_trends, describe_trade_style

RESULT_JSON = WORK_DIR / "backtest_week.json"


def profit_stats(pnls: list[float], outcomes: list[str] | None = None) -> dict:
    """Win rate, profit factor, avg win/loss ratio, expectancy."""
    if not pnls:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "profit_ratio": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
            "expectancy_pct": 0.0,
            "gross_profit_pct": 0.0,
            "gross_loss_pct": 0.0,
        }

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    profit_ratio = avg_win / avg_loss if avg_loss > 0 else (float("inf") if avg_win > 0 else 0.0)

    stats = {
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls), 3),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "profit_ratio": round(profit_ratio, 2) if profit_ratio != float("inf") else None,
        "avg_win_pct": round(avg_win, 3),
        "avg_loss_pct": round(avg_loss, 3),
        "expectancy_pct": round(sum(pnls) / len(pnls), 3),
        "gross_profit_pct": round(gross_profit, 3),
        "gross_loss_pct": round(gross_loss, 3),
    }
    if outcomes:
        from collections import Counter

        stats["outcomes"] = dict(Counter(outcomes))
    return stats


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _week_range(end: date | None = None) -> tuple[date, date]:
    today = end or datetime.now(timezone.utc).date()
    return _monday_of(today), today


def signal_from_trend_only(prior_ohlc: list | None, macro_phase: str) -> dict:
    """Trade the active 4h sub-trend — macro-aware fade logic."""
    from signal_engine import build_trend_signal

    return build_trend_signal(macro_phase=macro_phase, prior_ohlc=prior_ohlc)


def run_day_compare(
    replay_date: date,
    *,
    poll_hours: list[int] | None = None,
    use_llm: bool = False,
) -> dict[str, Any]:
    """One day: news replay + trend-only at first actionable poll."""
    poll_hours = poll_hours or [8, 12, 16, 20]
    news_report = run_replay(replay_date, poll_hours=poll_hours, use_llm=use_llm)

    if news_report.get("error"):
        return news_report

    cycle = get_cycle_state(replay_date)
    hourly = fetch_hourly_bars(replay_date)
    all_news = fetch_yesterday_news(replay_date)

    trend_trade: dict | None = None
    for hour in poll_hours:
        entry_time = datetime.combine(replay_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(
            hours=hour
        )
        prior_ohlc = [
            {"open": r["Open"], "high": r["High"], "low": r["Low"], "close": r["Close"]}
            for _, r in hourly[hourly["Datetime"] < entry_time].tail(24).iterrows()
        ]
        entry_row = hourly[hourly["Datetime"] >= entry_time].head(1)
        if entry_row.empty:
            continue
        entry_price = float(entry_row.iloc[0]["Open"])

        sig_info = signal_from_trend_only(prior_ohlc, cycle.phase)
        signal = sig_info["signal"]
        if signal not in ("LONG", "SHORT"):
            continue

        timeframe = normalize_timeframe(sig_info["timeframe"])
        setup = build_position_setup(
            signal, sig_info["confidence"], timeframe, entry_price, prior_ohlc,
            trade_style=sig_info.get("trade_style"),
        )
        trade_result = simulate_trade(setup, hourly, entry_time)
        trend_trade = {
            "entry_time_utc": entry_time.isoformat(),
            "poll_hour": hour,
            "signal": signal,
            "confidence": sig_info["confidence"],
            "timeframe": timeframe,
            "trade_style": sig_info["trade_style"],
            "subtrends": sig_info["subtrends"],
            "trade_result": trade_result,
        }
        break

    if trend_trade is None:
        trend_trade = {
            "signal": "FLAT",
            "trade_result": {"outcome": "NO_TRADE", "pnl_pct": 0.0},
            "subtrends": compute_price_trends([]),
        }

    ch = news_report["chart"]
    day_dir = "up" if ch["day_close"] > ch["day_open"] else "down" if ch["day_close"] < ch["day_open"] else "flat"

    news_pnl = news_report.get("trade_result", {}).get("pnl_pct", 0.0)
    trend_pnl = trend_trade.get("trade_result", {}).get("pnl_pct", 0.0)
    bh_pnl = news_report.get("buy_hold_pnl_pct", 0.0)

    news_sig = news_report.get("signal", "FLAT")
    trend_sig = trend_trade.get("signal", "FLAT")

    def _winner(a: float, b: float) -> str:
        if abs(a - b) < 0.01:
            return "tie"
        return "news" if a > b else "trend"

    return {
        "date": replay_date.isoformat(),
        "weekday": replay_date.strftime("%a"),
        "macro_phase": news_report.get("macro_phase"),
        "news_count": news_report.get("news_articles_total", 0),
        "chart": ch,
        "day_direction": day_dir,
        "buy_hold_pnl_pct": bh_pnl,
        "news": {
            "signal": news_sig,
            "confidence": news_report.get("confidence", 0),
            "timeframe": news_report.get("timeframe"),
            "trade_style": news_report.get("trade_style"),
            "subtrends": news_report.get("subtrends"),
            "poll_hour": news_report.get("poll_hour"),
            "method": news_report.get("signal_method", "lexicon"),
            "outcome": news_report.get("trade_result", {}).get("outcome"),
            "pnl_pct": news_pnl,
            "matched_day": (news_sig == "LONG" and day_dir == "up") or (news_sig == "SHORT" and day_dir == "down"),
        },
        "trend": {
            "signal": trend_sig,
            "confidence": trend_trade.get("confidence", 0),
            "timeframe": trend_trade.get("timeframe"),
            "trade_style": trend_trade.get("trade_style"),
            "subtrends": trend_trade.get("subtrends"),
            "poll_hour": trend_trade.get("poll_hour"),
            "method": "trend_only",
            "outcome": trend_trade.get("trade_result", {}).get("outcome"),
            "pnl_pct": trend_pnl,
            "matched_day": (trend_sig == "LONG" and day_dir == "up") or (trend_sig == "SHORT" and day_dir == "down"),
        },
        "vs_buy_hold": {
            "news_beats_bh": news_pnl > bh_pnl,
            "trend_beats_bh": trend_pnl > bh_pnl,
        },
        "news_vs_trend": _winner(news_pnl, trend_pnl),
        "poll_log": news_report.get("poll_log"),
    }


def run_week(
    date_from: date,
    date_to: date,
    *,
    poll_hours: list[int] | None = None,
    use_llm: bool = False,
) -> dict:
    days: list[dict] = []
    d = date_from
    while d <= date_to:
        print(f"  {d} ({d.strftime('%a')})...", flush=True)
        day = run_day_compare(d, poll_hours=poll_hours, use_llm=use_llm)
        days.append(day)
        d += timedelta(days=1)

    traded = [x for x in days if not x.get("error")]
    news_pnls = [x["news"]["pnl_pct"] for x in traded if x["news"]["signal"] in ("LONG", "SHORT")]
    trend_pnls = [x["trend"]["pnl_pct"] for x in traded if x["trend"]["signal"] in ("LONG", "SHORT")]
    news_outcomes = [x["news"]["outcome"] for x in traded if x["news"]["signal"] in ("LONG", "SHORT")]
    trend_outcomes = [x["trend"]["outcome"] for x in traded if x["trend"]["signal"] in ("LONG", "SHORT")]
    bh_pnls = [x["buy_hold_pnl_pct"] for x in traded]

    news_wins = sum(1 for x in traded if x["news_vs_trend"] == "news")
    trend_wins = sum(1 for x in traded if x["news_vs_trend"] == "trend")
    news_dir_hits = sum(1 for x in traded if x["news"].get("matched_day"))
    trend_dir_hits = sum(1 for x in traded if x["trend"].get("matched_day"))

    summary = {
        "backtest_type": "week_news_vs_trend",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days": len(traded),
        "errors": [x for x in days if x.get("error")],
        "totals": {
            "news_cumulative_pnl_pct": round(sum(news_pnls), 3) if news_pnls else 0,
            "trend_cumulative_pnl_pct": round(sum(trend_pnls), 3) if trend_pnls else 0,
            "buy_hold_cumulative_pnl_pct": round(sum(bh_pnls), 3) if bh_pnls else 0,
            "news_vs_trend_wins": news_wins,
            "trend_vs_news_wins": trend_wins,
            "news_direction_hits": news_dir_hits,
            "trend_direction_hits": trend_dir_hits,
            "news_profit": profit_stats(news_pnls, news_outcomes),
            "trend_profit": profit_stats(trend_pnls, trend_outcomes),
            "buy_hold_profit": profit_stats(bh_pnls),
        },
        "daily": days,
    }
    RESULT_JSON.write_text(json.dumps(summary, indent=2, default=str))
    return summary


def _print_summary(summary: dict) -> None:
    print()
    print(f"{'Date':<12} {'Day':<4} {'Chart':>8} {'News':>12} {'Trend':>12} {'B&H':>8} {'Winner':>8}")
    print("-" * 62)
    for d in summary["daily"]:
        if d.get("error"):
            print(f"{d.get('date','?'):<12} ERR  {d['error']}")
            continue
        ch = d["chart"]
        move = (ch["day_close"] - ch["day_open"]) / ch["day_open"] * 100 if ch["day_open"] else 0
        ns = d["news"]["signal"]
        ts = d["trend"]["signal"]
        np_ = d["news"]["pnl_pct"]
        tp_ = d["trend"]["pnl_pct"]
        bh = d["buy_hold_pnl_pct"]
        w = d["news_vs_trend"]
        print(
            f"{d['date']:<12} {d['weekday']:<4} {move:+7.2f}% "
            f"{ns:>4} {np_:+6.2f}% {ts:>4} {tp_:+6.2f}% {bh:+7.2f}% {w:>8}"
        )

    t = summary["totals"]
    print("-" * 62)
    print(f"Cumulative PnL — news {t['news_cumulative_pnl_pct']:+.2f}% · "
          f"trend {t['trend_cumulative_pnl_pct']:+.2f}% · "
          f"buy&hold {t['buy_hold_cumulative_pnl_pct']:+.2f}%")
    print(f"Head-to-head — news {t['news_vs_trend_wins']} wins · trend {t['trend_vs_news_wins']} wins")
    print(f"Called day direction — news {t['news_direction_hits']}/{summary['days']} · "
          f"trend {t['trend_direction_hits']}/{summary['days']}")

    for label, key in (("News", "news_profit"), ("Trend", "trend_profit"), ("Buy & hold", "buy_hold_profit")):
        p = t.get(key, {})
        if not p.get("trades"):
            continue
        pf = p.get("profit_factor")
        pf_s = "∞" if pf is None else f"{pf:.2f}"
        pr = p.get("profit_ratio")
        pr_s = "∞" if pr is None else f"{pr:.2f}"
        print()
        print(f"{label} profit ratio")
        print(f"  Win rate {p['win_rate']:.0%} ({p['wins']}W / {p['losses']}L) · "
              f"Profit factor {pf_s} · Avg win/loss {pr_s}")
        print(f"  Avg win {p['avg_win_pct']:+.2f}% · avg loss −{p['avg_loss_pct']:.2f}% · "
              f"expectancy {p['expectancy_pct']:+.2f}%/trade")
        if p.get("outcomes"):
            print(f"  Outcomes: {p['outcomes']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Week backtest: news vs trend")
    parser.add_argument("--from", dest="date_from", help="Start YYYY-MM-DD (default: Monday this week)")
    parser.add_argument("--to", dest="date_to", help="End YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--poll-hours", default="8,12,16,20")
    parser.add_argument("--llm", action="store_true")
    args = parser.parse_args()

    if args.date_from and args.date_to:
        d_from = date.fromisoformat(args.date_from)
        d_to = date.fromisoformat(args.date_to)
    else:
        d_from, d_to = _week_range()

    poll_hours = [int(h.strip()) for h in args.poll_hours.split(",") if h.strip()]

    print(f"Week backtest {d_from} → {d_to} (news vs trend-only)")
    print(f"Poll hours UTC: {poll_hours}\n")

    summary = run_week(d_from, d_to, poll_hours=poll_hours, use_llm=args.llm)
    _print_summary(summary)
    print(f"\nWrote {RESULT_JSON.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
