#!/usr/bin/env python3
"""
Prop account simulation — confidence-flexible risk % and R:R.

Usage:
  python prop_account.py                    # this week from backtest_week.json
  python prop_account.py --equity 100000
  python prop_account.py --from 2026-06-22 --to 2026-06-26
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

WORK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK_DIR))

from backtest_yesterday import run_replay
from risk_sizing import DEFAULT_ACCOUNT, compute_trade_risk, prop_pnl_dollars


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def simulate_week(
    date_from: date,
    date_to: date,
    *,
    start_equity: float = DEFAULT_ACCOUNT,
) -> dict:
    equity = start_equity
    rows: list[dict] = []
    d = date_from
    while d <= date_to:
        rep = run_replay(d)
        sig = rep.get("signal", "FLAT")
        tr = rep.get("trade_result") or {}
        setup = rep.get("position_setup") or {}
        outcome = tr.get("outcome", "NO_TRADE")
        pnl_pct = float(tr.get("pnl_pct") or 0)
        sl = float(setup.get("stop_loss_pct") or 0)

        if sig == "FLAT" or outcome in ("NO_TRADE", "NO_DATA"):
            rows.append({"date": d.isoformat(), "signal": "FLAT", "equity": round(equity, 2)})
            d += timedelta(days=1)
            continue

        conf = int(rep.get("confidence") or setup.get("confidence") or 0)
        style = rep.get("trade_style") or setup.get("trade_style")
        risk_info = compute_trade_risk(conf, style, account_equity=equity)
        equity_before = equity
        risk_d = round(equity_before * risk_info["risk_fraction"], 2)
        pnl_d = prop_pnl_dollars(equity_before, pnl_pct, sl, risk_info, outcome=outcome)
        equity += pnl_d
        R = round(pnl_d / risk_d, 2) if risk_d else 0.0

        rows.append(
            {
                "date": d.isoformat(),
                "signal": sig,
                "confidence": conf,
                "trade_style": style,
                "outcome": outcome,
                "risk_pct": risk_info["risk_pct"],
                "risk_reward": risk_info["risk_reward"],
                "risk_dollars": risk_d,
                "R": R,
                "pnl_dollars": pnl_d,
                "equity": round(equity, 2),
            }
        )
        d += timedelta(days=1)

    return {
        "start_equity": start_equity,
        "end_equity": round(equity, 2),
        "net_pnl": round(equity - start_equity, 2),
        "return_pct": round((equity / start_equity - 1) * 100, 2),
        "trades": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prop account P&L with flex risk/R:R")
    parser.add_argument("--equity", type=float, default=DEFAULT_ACCOUNT)
    parser.add_argument("--from", dest="date_from", help="YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="YYYY-MM-DD")
    args = parser.parse_args()

    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date()
    d_from = date.fromisoformat(args.date_from) if args.date_from else _monday_of(today)
    d_to = date.fromisoformat(args.date_to) if args.date_to else today

    print(f"Prop sim ${args.equity:,.0f} · {d_from} → {d_to} · flex risk from confidence\n")
    result = simulate_week(d_from, d_to, start_equity=args.equity)

    for row in result["trades"]:
        if row.get("signal") == "FLAT":
            print(f"  {row['date']}  FLAT                          ${row['equity']:,.2f}")
        else:
            print(
                f"  {row['date']}  {row['signal']:<5} {row['outcome']:<12} "
                f"risk {row['risk_pct']:.2f}% · R:R {row['risk_reward']} · "
                f"{row.get('R', 0):+.2f}R  ${row['pnl_dollars']:+,.0f}  →  ${row['equity']:,.2f}"
            )

    print(f"\n  Start ${result['start_equity']:,.2f}  →  End ${result['end_equity']:,.2f}")
    print(f"  Net ${result['net_pnl']:+,.2f}  ({result['return_pct']:+.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
