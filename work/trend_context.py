"""Short-term price trends — trade WITH minute/hour/day swings inside macro cycle."""

from __future__ import annotations

from typing import Any, Literal

Trend = Literal["up", "down", "sideways"]


def _pct_change(from_p: float, to_p: float) -> float:
    if from_p <= 0:
        return 0.0
    return (to_p - from_p) / from_p * 100


def _classify(pct: float, threshold: float = 0.25) -> Trend:
    if pct > threshold:
        return "up"
    if pct < -threshold:
        return "down"
    return "sideways"


def compute_price_trends(ohlc: list[dict[str, Any]] | None) -> dict[str, Any]:
    """
    Derive sub-trends from recent candles (CoinGecko 1d feed ≈ hourly bars).
    Macro cycle is separate — these are the swings to trade with.
    """
    if not ohlc:
        return {
            "1h": "sideways",
            "4h": "sideways",
            "24h": "sideways",
            "summary": "No price data",
        }

    closes = [float(c["close"]) for c in ohlc if c.get("close")]
    if len(closes) < 2:
        return {"1h": "sideways", "4h": "sideways", "24h": "sideways", "summary": "Insufficient bars"}

    last = closes[-1]
    t1h = _classify(_pct_change(closes[-2], last)) if len(closes) >= 2 else "sideways"
    t4h = _classify(_pct_change(closes[-min(5, len(closes))], last))
    t24h = _classify(_pct_change(closes[0], last))

    parts = []
    for label, t, pct in (
        ("1h", t1h, _pct_change(closes[-2], last)),
        ("4h", t4h, _pct_change(closes[-min(5, len(closes))], last)),
        ("24h", t24h, _pct_change(closes[0], last)),
    ):
        arrow = {"up": "↑", "down": "↓", "sideways": "→"}[t]
        parts.append(f"{label} {arrow} ({pct:+.2f}%)")

    return {
        "1h": t1h,
        "4h": t4h,
        "24h": t24h,
        "pct_1h": round(_pct_change(closes[-2], last), 3),
        "pct_4h": round(_pct_change(closes[-min(5, len(closes))], last), 3),
        "pct_24h": round(_pct_change(closes[0], last), 3),
        "last_price": last,
        "summary": " · ".join(parts),
    }


def describe_trade_style(signal: str, macro_phase: str, subtrends: dict[str, Any]) -> str:
    """How this trade relates to macro vs active sub-trend."""
    if signal == "FLAT":
        return "flat"

    macro_bear = macro_phase == "downtrend"
    macro_dir = "down" if macro_bear else "up"
    active = subtrends.get("4h") or subtrends.get("1h") or "sideways"

    same_macro = (signal == "SHORT" and macro_bear) or (signal == "LONG" and not macro_bear)
    same_sub = (signal == "LONG" and active == "up") or (signal == "SHORT" and active == "down")

    if same_sub and same_macro:
        return "with_macro_and_subtrend"
    if same_sub:
        return "with_subtrend"
    if same_macro:
        return "macro_pullback" if signal == "LONG" and macro_bear else "macro_correction"
    return "counter_trend_scalp"
