#!/usr/bin/env python3
"""
Compare best snapshot vs upgrade; auto-revert if upgrade is worse.

Benchmarks:
  - Jun 22–26 (100% week target)
  - Jun 1–26 (full month, confluence-only for speed)

Usage:
  python upgrade_eval.py
  python upgrade_eval.py --keep-hybrid   # also test SIGNAL_MODE=hybrid on week (slow)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

WORK_DIR = Path(__file__).resolve().parent


def _run_bench(version: str, signal_mode: str, d_from: date, d_to: date) -> dict:
    """Fresh subprocess per bench to avoid stale imports after restore."""
    code = f"""
import json, os, subprocess, sys
from datetime import date, timedelta
from pathlib import Path

WORK = Path({str(WORK_DIR)!r})
subprocess.run([sys.executable, str(WORK / "version_manager.py"), "restore", {version!r}], check=True)
os.environ["SIGNAL_MODE"] = {signal_mode!r}
sys.path.insert(0, str(WORK))

from backtest_yesterday import run_replay

d_from = date.fromisoformat({d_from.isoformat()!r})
d_to = date.fromisoformat({d_to.isoformat()!r})
taken, wins, losses, pnl_sum = 0, 0, 0, 0.0
d = d_from
while d <= d_to:
    try:
        r = run_replay(d)
    except Exception:
        d += timedelta(days=1)
        continue
    sig = r.get("signal", "FLAT")
    tr = r.get("trade_result") or {{}}
    out = tr.get("outcome")
    pnl = float(tr.get("pnl_pct") or 0)
    if sig in ("LONG", "SHORT") and out not in ("NO_TRADE", "NO_DATA"):
        taken += 1
        pnl_sum += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    d += timedelta(days=1)
wr = wins / taken if taken else 0
print(json.dumps({{
    "version": {version!r},
    "signal_mode": {signal_mode!r},
    "trades": taken,
    "wins": wins,
    "losses": losses,
    "win_rate": round(wr, 4),
    "pnl_sum": round(pnl_sum, 3),
}}))
"""
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=WORK_DIR,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if out.returncode != 0:
        raise RuntimeError(f"bench failed: {out.stderr or out.stdout}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def _score(m: dict) -> tuple:
    return (m["win_rate"], m["pnl_sum"])


def _fmt(m: dict) -> str:
    return f"{m['win_rate']:.1%} WR  {m['wins']}W/{m['losses']}L  PnL {m['pnl_sum']:+.2f}%"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-hybrid", action="store_true", help="Test hybrid LLM on week (slow)")
    args = parser.parse_args()

    week_from = date(2026, 6, 22)
    week_to = date(2026, 6, 26)
    month_from = date(2026, 6, 1)
    month_to = date(2026, 6, 26)

    print("Saving upgrade snapshot…")
    subprocess.run([sys.executable, str(WORK_DIR / "version_manager.py"), "save", "upgrade"], check=True)

    print("\n=== Benchmark: BEST (saved snapshot) ===\n")
    best_week = _run_bench("best", "best", week_from, week_to)
    best_month = _run_bench("best", "best", month_from, month_to)
    print(f"  Week:  {_fmt(best_week)}")
    print(f"  Month: {_fmt(best_month)}")

    print("\n=== Benchmark: UPGRADE (macro lexicon, confluence) ===\n")
    up_week = _run_bench("upgrade", "best", week_from, week_to)
    up_month = _run_bench("upgrade", "best", month_from, month_to)
    print(f"  Week:  {_fmt(up_week)}")
    print(f"  Month: {_fmt(up_month)}")

    hybrid_week = None
    if args.keep_hybrid:
        print("\n=== Benchmark: UPGRADE + hybrid LLM (week only) ===\n")
        hybrid_week = _run_bench("upgrade", "hybrid", week_from, week_to)
        print(f"  Week:  {_fmt(hybrid_week)}")

    best_score = (_score(best_week), _score(best_month))
    up_score = (_score(up_week), _score(up_month))

    # Upgrade wins if week AND month not worse (lexicographic on week first, then month)
    week_ok = _score(up_week) >= _score(best_week)
    month_ok = _score(up_month) >= _score(best_month)
    upgrade_wins = week_ok and month_ok

    winner = "upgrade"
    restore_to = "upgrade"
    reason = "macro lexicon matches or beats best on week and month"

    if not upgrade_wins:
        winner = "best"
        restore_to = "best"
        parts = []
        if not week_ok:
            parts.append(f"week {_fmt(up_week)} vs {_fmt(best_week)}")
        if not month_ok:
            parts.append(f"month {_fmt(up_month)} vs {_fmt(best_month)}")
        reason = "upgrade worse: " + "; ".join(parts)

    if hybrid_week and _score(hybrid_week) > _score(up_week) and week_ok and month_ok:
        print(f"\n  Hybrid week beats confluence-only: {_fmt(hybrid_week)}")
        print("  → Keep SIGNAL_MODE=hybrid for live (set in env)")
        winner_note = "hybrid"
    else:
        winner_note = "best (confluence)" if winner == "upgrade" else "best (reverted)"

    print(f"\n=== Verdict: {winner.upper()} ===")
    print(f"  {reason}")
    print(f"  Active profile: {winner_note}")

    subprocess.run(
        [sys.executable, str(WORK_DIR / "version_manager.py"), "restore", restore_to],
        check=True,
    )

    report = {
        "best": {"week": best_week, "month": best_month},
        "upgrade": {"week": up_week, "month": up_month},
        "hybrid_week": hybrid_week,
        "winner": winner,
        "active": restore_to,
        "week_ok": week_ok,
        "month_ok": month_ok,
    }
    out_path = WORK_DIR / "upgrade_eval_report.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nReport → {out_path}")

    if winner == "best":
        print("\nReverted to best — upgrade did not beat baseline.")
        return 1
    print("\nUpgrade kept — macro lexicon is same or better.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
