#!/usr/bin/env python3
"""
Full-month daily backtest vs news + pattern analysis + walk-forward learning.

Usage:
  python backtest_month.py --year 2026 --month 6
  python backtest_month.py --from 2026-06-01 --to 2026-06-30
  python backtest_month.py --learn          # walk-forward adaptive replay
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WORK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK_DIR))

from backtest_yesterday import run_replay
from backtest_week import profit_stats
from market_cycle import get_cycle_state
from trade_learning import (
    LearningState,
    TradeRecord,
    apply_learned_rules,
    ingest_outcome,
    pattern_report,
)

REPORT_JSON = WORK_DIR / "backtest_june_report.json"
NOTES_MD = WORK_DIR / "backtest_june_notes.md"


def _day_direction(open_p: float, close_p: float) -> str:
    if close_p > open_p * 1.001:
        return "up"
    if close_p < open_p * 0.999:
        return "down"
    return "flat"


def replay_to_record(rep: dict, replay_date: date) -> TradeRecord:
    ch = rep.get("chart") or {}
    tr = rep.get("trade_result") or {}
    sub = rep.get("subtrends") or {}
    return TradeRecord(
        date=replay_date.isoformat(),
        signal=rep.get("signal", "FLAT"),
        outcome=tr.get("outcome", "NO_TRADE"),
        pnl_pct=float(tr.get("pnl_pct") or 0),
        macro_phase=rep.get("macro_phase") or get_cycle_state(replay_date).phase,
        trade_style=rep.get("trade_style") or "flat",
        news_score=float(rep.get("news_score") or 0),
        confidence=int(rep.get("confidence") or 0),
        poll_hour=rep.get("poll_hour"),
        subtrends=sub,
        day_direction=_day_direction(float(ch.get("day_open") or 0), float(ch.get("day_close") or 0)),
    )


def run_baseline(d_from: date, d_to: date) -> tuple[list[TradeRecord], list[dict]]:
    """Standard confluence replay each day."""
    records: list[TradeRecord] = []
    daily: list[dict] = []
    d = d_from
    while d <= d_to:
        print(f"  {d}...", flush=True)
        try:
            rep = run_replay(d)
        except Exception as exc:
            rep = {"error": str(exc), "replay_date": d.isoformat(), "signal": "FLAT"}
        if rep.get("error") and not rep.get("chart"):
            daily.append({"date": d.isoformat(), "error": rep["error"]})
            d += timedelta(days=1)
            continue
        rec = replay_to_record(rep, d)
        records.append(rec)
        ch = rep.get("chart") or {}
        daily.append(
            {
                "date": d.isoformat(),
                "weekday": d.strftime("%a"),
                "signal": rec.signal,
                "confidence": rec.confidence,
                "trade_style": rec.trade_style,
                "news_score": rec.news_score,
                "poll_hour": rec.poll_hour,
                "outcome": rec.outcome,
                "pnl_pct": rec.pnl_pct,
                "win": rec.win,
                "macro_phase": rec.macro_phase,
                "day_direction": rec.day_direction,
                "day_move_pct": round(
                    (float(ch.get("day_close", 0)) - float(ch.get("day_open", 0)))
                    / float(ch.get("day_open") or 1)
                    * 100,
                    2,
                )
                if ch.get("day_open")
                else 0,
                "buy_hold_pct": rep.get("buy_hold_pnl_pct"),
                "subtrends": rec.subtrends.get("summary") if rec.subtrends else None,
                "poll_log": rep.get("poll_log"),
            }
        )
        d += timedelta(days=1)
    return records, daily


def run_walk_forward(d_from: date, d_to: date) -> tuple[list[TradeRecord], LearningState, list[dict]]:
    """
    Walk-forward: apply instincts learned from past losses only to future days.
    Re-simulates each day with learned rules layered on poll loop.
    """
    from backtest_yesterday import (
        fetch_hourly_bars,
        fetch_yesterday_news,
        news_before_cutoff,
        signal_from_lexicon,
        simulate_trade,
    )
    from position_manager import build_position_setup, normalize_timeframe
    from signal_engine import is_late_chase

    state = LearningState()
    records: list[TradeRecord] = []
    daily: list[dict] = []
    poll_hours = [8, 12, 16, 20]

    d = d_from
    while d <= d_to:
        print(f"  {d} (learn)...", flush=True)
        cycle = get_cycle_state(d)
        hourly = fetch_hourly_bars(d)
        if hourly.empty:
            d += timedelta(days=1)
            continue

        all_news = fetch_yesterday_news(d)
        day_open = float(hourly.iloc[0]["Open"])
        day_close = float(hourly.iloc[-1]["Close"])

        best = None
        poll_log: list[dict] = []

        for hour in poll_hours:
            entry_time = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=hour)
            news = news_before_cutoff(all_news, entry_time)
            texts = [f"{a['title']} {a.get('summary','')}" for a in news]
            prior_ohlc = [
                {"open": r["Open"], "high": r["High"], "low": r["Low"], "close": r["Close"]}
                for _, r in hourly[hourly["Datetime"] < entry_time].tail(24).iterrows()
            ]
            entry_row = hourly[hourly["Datetime"] >= entry_time].head(1)
            entry_price = float(entry_row.iloc[0]["Open"]) if not entry_row.empty else day_open

            sig_info = signal_from_lexicon(texts, cycle.phase, prior_ohlc)
            signal = sig_info["signal"]
            confidence = int(sig_info["confidence"])

            if signal in ("LONG", "SHORT") and is_late_chase(hour, signal, day_open, entry_price):
                signal = "FLAT"
                sig_info = {**sig_info, "signal": "FLAT", "reject_reason": "late_chase"}

            if signal in ("LONG", "SHORT"):
                signal, lr = apply_learned_rules(
                    signal,
                    macro_phase=cycle.phase,
                    trade_style=sig_info.get("trade_style") or "flat",
                    news_score=float(sig_info.get("news_score") or 0),
                    confidence=confidence,
                    subtrends=sig_info.get("subtrends") or {},
                    poll_hour=hour,
                    instincts=state.instincts,
                )
                if lr:
                    sig_info = {**sig_info, "signal": "FLAT", "reject_reason": lr}
                    confidence = 40

            poll_log.append(
                {
                    "hour": hour,
                    "signal": signal,
                    "confidence": confidence,
                    "news_score": sig_info.get("news_score"),
                    "reject": sig_info.get("reject_reason"),
                }
            )

            if signal in ("LONG", "SHORT") and best is None:
                tf = normalize_timeframe(sig_info.get("timeframe"))
                setup = build_position_setup(
                    signal, confidence, tf, entry_price, prior_ohlc,
                    trade_style=sig_info.get("trade_style"),
                )
                tr = simulate_trade(setup, hourly, entry_time)
                best = {
                    "signal": signal,
                    "confidence": confidence,
                    "trade_style": sig_info.get("trade_style"),
                    "news_score": sig_info.get("news_score"),
                    "poll_hour": hour,
                    "subtrends": sig_info.get("subtrends"),
                    "trade_result": tr,
                    "setup": setup,
                }

        if best is None:
            rec = TradeRecord(
                date=d.isoformat(),
                signal="FLAT",
                outcome="NO_TRADE",
                pnl_pct=0.0,
                macro_phase=cycle.phase,
                trade_style="flat",
                news_score=0.0,
                confidence=0,
                poll_hour=None,
                subtrends={},
                day_direction=_day_direction(day_open, day_close),
            )
        else:
            tr = best["trade_result"]
            rec = TradeRecord(
                date=d.isoformat(),
                signal=best["signal"],
                outcome=tr.get("outcome", "NO_TRADE"),
                pnl_pct=float(tr.get("pnl_pct") or 0),
                macro_phase=cycle.phase,
                trade_style=best.get("trade_style") or "flat",
                news_score=float(best.get("news_score") or 0),
                confidence=best["confidence"],
                poll_hour=best["poll_hour"],
                subtrends=best.get("subtrends") or {},
                day_direction=_day_direction(day_open, day_close),
            )

        new_inst = ingest_outcome(state, rec)
        records.append(rec)
        daily.append(
            {
                "date": d.isoformat(),
                "signal": rec.signal,
                "win": rec.win,
                "pnl_pct": rec.pnl_pct,
                "outcome": rec.outcome,
                "learned_applied": len(state.instincts),
                "new_instincts": new_inst,
                "poll_log": poll_log,
            }
        )
        d += timedelta(days=1)

    state.save()
    return records, state, daily


def write_notes_md(patterns: dict, baseline_wr: float, adaptive_wr: float | None, d_from: date, d_to: date) -> None:
    lines = [
        f"# June Backtest Notes ({d_from} → {d_to})",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Summary",
        f"- **Baseline win rate:** {baseline_wr:.1%}",
    ]
    if adaptive_wr is not None:
        lines.append(f"- **Walk-forward adaptive win rate:** {adaptive_wr:.1%}")
    lines.extend(
        [
            f"- Trades: {patterns['trades_taken']} · Flat days: {patterns['flat_days']}",
            f"- Wins/Losses: {patterns['wins']}/{patterns['losses']}",
            "",
            "## Loss patterns",
            "",
        ]
    )
    for loss in patterns.get("loss_details", []):
        lines.append(
            f"- **{loss['date']}** {loss['signal']} ({loss['style']}) "
            f"{loss['outcome']} {loss['pnl_pct']:+.2f}% · news {loss['news_score']} · "
            f"poll {loss.get('poll_hour')} · {loss.get('subtrends_summary', '')}"
        )
    lines.extend(["", "## What worked", ""])
    for w in patterns.get("win_details", []):
        lines.append(f"- **{w['date']}** {w['signal']} ({w['style']}) {w['pnl_pct']:+.2f}% · news {w['news_score']}")
    lines.extend(["", "## Recommendations", ""])
    for r in patterns.get("recommendations", []):
        lines.append(f"- {r}")
    lines.extend(
        [
            "",
            "## Self-improvement loop",
            "",
            "1. Each loss appends to `data/trade_learning.json` with pattern signatures.",
            "2. Walk-forward mode applies **learned instincts only to future days** (no peeking).",
            "3. Live pipeline: call `trade_learning.apply_learned_rules()` after `signal_engine`.",
            "4. Review instincts weekly; promote repeated patterns into `signal_engine.py` filters.",
            "",
        ]
    )
    NOTES_MD.write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="Monthly news backtest + learning")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--month", type=int, default=6)
    parser.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    parser.add_argument("--learn", action="store_true", help="Walk-forward adaptive replay")
    parser.add_argument("--reset-learning", action="store_true")
    args = parser.parse_args()

    if args.date_from and args.date_to:
        d_from = date.fromisoformat(args.date_from)
        d_to = date.fromisoformat(args.date_to)
    else:
        d_from = date(args.year, args.month, 1)
        last = calendar.monthrange(args.year, args.month)[1]
        d_to = date(args.year, args.month, last)

    if args.reset_learning:
        from trade_learning import LEARNING_FILE

        if LEARNING_FILE.exists():
            LEARNING_FILE.unlink()

    print(f"Baseline backtest {d_from} → {d_to}\n")
    baseline_records, baseline_daily = run_baseline(d_from, d_to)
    patterns = pattern_report(baseline_records)
    taken_pnls = [r.pnl_pct for r in baseline_records if r.signal in ("LONG", "SHORT")]
    outcomes = [r.outcome for r in baseline_records if r.signal in ("LONG", "SHORT")]
    profit = profit_stats(taken_pnls, outcomes)

    adaptive_records = None
    adaptive_wr = None
    learning_state = None
    if args.learn:
        print("\nWalk-forward learning replay...\n")
        adaptive_records, learning_state, _ = run_walk_forward(d_from, d_to)
        ap = pattern_report(adaptive_records)
        adaptive_wr = ap["win_rate"]
        print(f"Adaptive instincts loaded: {len(learning_state.instincts)}")

    baseline_wr = patterns["win_rate"]
    write_notes_md(patterns, baseline_wr, adaptive_wr, d_from, d_to)

    report = {
        "period": {"from": d_from.isoformat(), "to": d_to.isoformat()},
        "baseline": {
            "patterns": patterns,
            "profit_stats": profit,
            "daily": baseline_daily,
        },
        "adaptive": None,
    }
    if adaptive_records:
        apnls = [r.pnl_pct for r in adaptive_records if r.signal in ("LONG", "SHORT")]
        aout = [r.outcome for r in adaptive_records if r.signal in ("LONG", "SHORT")]
        report["adaptive"] = {
            "patterns": pattern_report(adaptive_records),
            "profit_stats": profit_stats(apnls, aout),
            "instincts": learning_state.instincts if learning_state else [],
        }

    REPORT_JSON.write_text(json.dumps(report, indent=2, default=str))

    print("\n" + "=" * 60)
    print(f"BASELINE  WR {baseline_wr:.1%}  trades {patterns['trades_taken']}  "
          f"PnL sum {profit['gross_profit_pct'] - profit['gross_loss_pct']:+.2f}%")
    if adaptive_wr is not None:
        ad = report["adaptive"]["patterns"]
        print(f"ADAPTIVE  WR {adaptive_wr:.1%}  trades {ad['trades_taken']}  "
              f"instincts {len(report['adaptive']['instincts'])}")
    print(f"\nWrote {REPORT_JSON.name}  {NOTES_MD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
