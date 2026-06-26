#!/usr/bin/env python3
"""
Backtest the perpetual 364/1064 macro cycle from BTC genesis.

Macro strategy: LONG during bull phases, SHORT during bear phases.
Benchmark: buy-and-hold BTC.

Note: Live day-trade signals need historical news + LLM — not reproducible here.
This backtests the fixed macro cycle belief only.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

WORK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(WORK_DIR))

from market_cycle import BEAR_DAYS, BULL_DAYS, CYCLE_DAYS, CYCLE_EPOCH, get_cycle_state

RESULTS_CSV = WORK_DIR / "backtest_results.csv"
SUMMARY_JSON = WORK_DIR / "backtest_summary.json"
EQUITY_CSV = WORK_DIR / "backtest_equity.csv"

BLOCKCHAIN_CHART = "https://api.blockchain.info/charts/market-price"
GENESIS_DATE = datetime(2009, 1, 3, tzinfo=timezone.utc).date()
MIN_PRICE_USD = 1.0  # ignore sub-dollar noise before reliable USD market
MAX_DAILY_RETURN = 0.35  # clip wild early prints (shorts can't lose >100%/day)


def fetch_blockchain_prices() -> pd.DataFrame:
    resp = requests.get(
        BLOCKCHAIN_CHART,
        params={"timespan": "all", "format": "json", "rollingAverage": "1days"},
        timeout=60,
    )
    resp.raise_for_status()
    rows = []
    for point in resp.json().get("values", []):
        ts = datetime.fromtimestamp(point["x"], tz=timezone.utc).date()
        price = float(point["y"])
        if price > 0:
            rows.append({"date": ts, "close": price, "source": "blockchain.info"})
    return pd.DataFrame(rows)


def fetch_yfinance_daily() -> pd.DataFrame:
    raw = yf.download("BTC-USD", start="2014-01-01", progress=False, auto_adjust=True)
    if raw.empty:
        return pd.DataFrame(columns=["date", "close", "source"])
    close = raw["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    df = close.reset_index()
    df.columns = ["date", "close"]
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["source"] = "yfinance"
    return df


def load_btc_history() -> pd.DataFrame:
    """Daily BTC USD from genesis (blockchain.info) merged with yfinance daily."""
    chain = fetch_blockchain_prices()
    daily = fetch_yfinance_daily()

    merged = pd.concat([chain, daily], ignore_index=True)
    merged = merged.sort_values("date").drop_duplicates("date", keep="last")
    merged = merged[merged["date"] >= GENESIS_DATE].reset_index(drop=True)

    # Forward-fill sparse early blockchain samples to daily grid
    full = pd.DataFrame({"date": pd.date_range(merged["date"].min(), merged["date"].max(), freq="D")})
    full["date"] = full["date"].dt.date
    merged = full.merge(merged, on="date", how="left")
    merged["close"] = merged["close"].ffill()
    merged = merged.dropna(subset=["close"]).reset_index(drop=True)
    merged = merged[merged["close"] >= MIN_PRICE_USD].reset_index(drop=True)
    return merged


def run_macro_backtest(prices: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    df = prices.copy()
    df["phase"] = df["date"].apply(lambda d: get_cycle_state(d).phase)
    df["position"] = df["phase"].map({"bull": 1.0, "downtrend": -1.0})

    raw_return = df["close"].pct_change()
    df["daily_return"] = raw_return.clip(-MAX_DAILY_RETURN, MAX_DAILY_RETURN)
    df["return_clipped"] = (raw_return.abs() > MAX_DAILY_RETURN).fillna(False)
    df["strategy_return"] = df["position"].shift(1) * df["daily_return"]
    df["buy_hold_return"] = df["daily_return"]

    df["strategy_equity"] = (1 + df["strategy_return"].fillna(0)).cumprod()
    df["buy_hold_equity"] = (1 + df["buy_hold_return"].fillna(0)).cumprod()

    start_price = df["close"].iloc[0]
    end_price = df["close"].iloc[-1]
    years = max((df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25, 0.01)

    strat_total = df["strategy_equity"].iloc[-1] - 1
    bh_total = df["buy_hold_equity"].iloc[-1] - 1
    strat_cagr = (df["strategy_equity"].iloc[-1]) ** (1 / years) - 1
    bh_cagr = (df["buy_hold_equity"].iloc[-1]) ** (1 / years) - 1

    def max_drawdown(equity: pd.Series) -> float:
        peak = equity.cummax()
        dd = (equity - peak) / peak
        return float(dd.min())

    def sharpe(returns: pd.Series) -> float:
        r = returns.dropna()
        if r.std() == 0:
            return 0.0
        return float(r.mean() / r.std() * (365**0.5))

    bull_days = int((df["phase"] == "bull").sum())
    bear_days = int((df["phase"] == "downtrend").sum())
    clipped_days = int(df["return_clipped"].sum())

    phase_rows = []
    df["phase_id"] = (df["phase"] != df["phase"].shift(1)).cumsum()
    for _, grp in df.groupby("phase_id"):
        if len(grp) < 2:
            continue
        phase_rows.append(
            {
                "phase": grp["phase"].iloc[0],
                "start": str(grp["date"].iloc[0]),
                "end": str(grp["date"].iloc[-1]),
                "days": len(grp),
                "btc_return_pct": round((grp["close"].iloc[-1] / grp["close"].iloc[0] - 1) * 100, 1),
                "strategy_return_pct": round(((1 + grp["strategy_return"].fillna(0)).prod() - 1) * 100, 1),
            }
        )

    phase_stats = []
    flips = df[df["phase"] != df["phase"].shift(1)].copy()
    for _, row in flips.iterrows():
        phase_stats.append({"date": str(row["date"]), "phase": row["phase"], "price": row["close"]})

    summary = {
        "data_from": str(df["date"].iloc[0]),
        "data_to": str(df["date"].iloc[-1]),
        "btc_genesis": str(GENESIS_DATE),
        "cycle_epoch": str(CYCLE_EPOCH),
        "cycle_days": CYCLE_DAYS,
        "bear_days_per_cycle": BEAR_DAYS,
        "bull_days_per_cycle": BULL_DAYS,
        "start_price_usd": round(start_price, 2),
        "end_price_usd": round(end_price, 2),
        "years": round(years, 2),
        "bull_phase_days": bull_days,
        "bear_phase_days": bear_days,
        "clipped_return_days": clipped_days,
        "min_price_filter_usd": MIN_PRICE_USD,
        "phase_periods": phase_rows,
        "macro_strategy_total_return_pct": round(strat_total * 100, 2),
        "buy_hold_total_return_pct": round(bh_total * 100, 2),
        "macro_strategy_cagr_pct": round(strat_cagr * 100, 2),
        "buy_hold_cagr_pct": round(bh_cagr * 100, 2),
        "macro_max_drawdown_pct": round(max_drawdown(df["strategy_equity"]) * 100, 2),
        "buy_hold_max_drawdown_pct": round(max_drawdown(df["buy_hold_equity"]) * 100, 2),
        "macro_sharpe": round(sharpe(df["strategy_return"]), 2),
        "buy_hold_sharpe": round(sharpe(df["buy_hold_return"]), 2),
        "phase_transitions": len(phase_stats),
        "note": (
            "Macro cycle backtest only. Day-trade LLM signals require historical news "
            "and are not replayed here."
        ),
    }
    return df, summary


def main() -> int:
    print("BTC Macro Cycle Backtest — from genesis")
    print(f"Cycle epoch (inaugural bear start): {CYCLE_EPOCH}")
    print(f"Pattern: {BEAR_DAYS}d bear → {BULL_DAYS}d bull → repeat\n")

    prices = load_btc_history()
    print(f"Loaded {len(prices)} daily bars ({prices['date'].iloc[0]} → {prices['date'].iloc[-1]})")

    df, summary = run_macro_backtest(prices)

    df[["date", "close", "phase", "position", "daily_return", "strategy_return", "strategy_equity", "buy_hold_equity"]].to_csv(
        EQUITY_CSV, index=False
    )
    df[df["phase"] != df["phase"].shift(1)][["date", "phase", "close"]].to_csv(RESULTS_CSV, index=False)
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2))

    print("\n=== RESULTS ===")
    print(f"Period:           {summary['data_from']} → {summary['data_to']} ({summary['years']}y)")
    print(f"BTC price:        ${summary['start_price_usd']:,.2f} → ${summary['end_price_usd']:,.2f}")
    print(f"Bull phase days:  {summary['bull_phase_days']:,}")
    print(f"Bear phase days:  {summary['bear_phase_days']:,}")
    print(f"Phase flips:      {summary['phase_transitions']}")
    print(f"Clipped days:     {summary['clipped_return_days']} (>{MAX_DAILY_RETURN:.0%} daily move)")
    print()
    print("Per-phase breakdown (last 6):")
    for p in summary["phase_periods"][-6:]:
        print(f"  {p['start']} → {p['end']} | {p['phase']:9} | "
              f"BTC {p['btc_return_pct']:+.1f}% | strategy {p['strategy_return_pct']:+.1f}%")
    print()
    print(f"Macro LONG/SHORT: {summary['macro_strategy_total_return_pct']:+,.1f}% total | "
          f"{summary['macro_strategy_cagr_pct']:+.1f}% CAGR | "
          f"{summary['macro_max_drawdown_pct']:.1f}% max DD | "
          f"Sharpe {summary['macro_sharpe']:.2f}")
    print(f"Buy & hold:       {summary['buy_hold_total_return_pct']:+,.1f}% total | "
          f"{summary['buy_hold_cagr_pct']:+.1f}% CAGR | "
          f"{summary['buy_hold_max_drawdown_pct']:.1f}% max DD | "
          f"Sharpe {summary['buy_hold_sharpe']:.2f}")
    print(f"\nWrote {EQUITY_CSV.name}, {RESULTS_CSV.name}, {SUMMARY_JSON.name}")
    print(f"\n{summary['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
