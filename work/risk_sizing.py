"""
Confidence + trade-style → flexible account risk % and R:R target.
Higher conviction = size up (1%++) and stretch TP toward 4:1.
"""

from __future__ import annotations

import os
from typing import Any

# Account risk at stop as fraction of equity (0.004 = 0.4%)
MIN_RISK_PCT = float(os.environ.get("MIN_RISK_PCT", "0.004"))
BASE_RISK_PCT = float(os.environ.get("BASE_RISK_PCT", "0.005"))  # floor ~0.5%
MAX_RISK_PCT = float(os.environ.get("MAX_RISK_PCT", "0.025"))  # cap 2.5% per trade
DEFAULT_ACCOUNT = float(os.environ.get("PROP_ACCOUNT_SIZE", "100000"))

MIN_RR = float(os.environ.get("MIN_RR", "1.8"))
BASE_RR = float(os.environ.get("BASE_RR", "2.5"))
MAX_RR = float(os.environ.get("MAX_RR", "4.0"))

STYLE_RISK_MULT: dict[str, float] = {
    "with_macro_and_subtrend": 1.18,
    "with_subtrend": 1.08,
    "macro_correction": 1.0,
    "macro_pullback": 0.72,
    "counter_trend_scalp": 0.58,
    "flat": 0.0,
}

STYLE_RR_BONUS: dict[str, float] = {
    "with_macro_and_subtrend": 0.45,
    "with_subtrend": 0.28,
    "macro_correction": 0.15,
    "macro_pullback": 0.0,
    "counter_trend_scalp": -0.25,
    "flat": 0.0,
}


def compute_trade_risk(
    confidence: int,
    trade_style: str | None = None,
    *,
    account_equity: float | None = None,
) -> dict[str, Any]:
    """
    Map signal confidence + style → risk_pct (account), R:R, dollar risk/reward.

    50% conf ≈ 0.7% risk · 2.2 R:R
    75% conf ≈ 1.4% risk · 2.9 R:R
    95% aligned ≈ 2.3% risk · 3.6+ R:R
    """
    conf = max(0, min(100, int(confidence or 0)))
    style = trade_style or "flat"
    equity = account_equity if account_equity and account_equity > 0 else DEFAULT_ACCOUNT

    # Risk scales super-linearly with confidence above 50
    conf_norm = conf / 100.0
    if conf < 50:
        core = BASE_RISK_PCT * (conf / 50.0) ** 1.1
    else:
        span = MAX_RISK_PCT - BASE_RISK_PCT
        core = BASE_RISK_PCT + ((conf - 50) / 50.0) ** 1.15 * span

    risk_mult = STYLE_RISK_MULT.get(style, 0.85 if style else 1.0)
    risk_pct = max(MIN_RISK_PCT, min(MAX_RISK_PCT, core * risk_mult))

    # R:R: base ramps with confidence + style bonus
    rr_core = BASE_RR + (conf_norm * (MAX_RR - BASE_RR) * 0.85)
    rr = rr_core + STYLE_RR_BONUS.get(style, 0.0)
    if style == "counter_trend_scalp":
        rr = max(MIN_RR, min(2.8, rr))
    else:
        rr = max(MIN_RR, min(MAX_RR, rr))

    risk_dollars = round(equity * risk_pct, 2)
    reward_at_tp = round(risk_dollars * rr, 2)

    tier = "light"
    if risk_pct >= 0.018:
        tier = "max"
    elif risk_pct >= 0.012:
        tier = "heavy"
    elif risk_pct >= 0.008:
        tier = "standard"

    return {
        "confidence": conf,
        "trade_style": style,
        "risk_pct": round(risk_pct * 100, 2),  # display as %
        "risk_fraction": round(risk_pct, 5),
        "risk_reward": round(rr, 2),
        "account_equity": round(equity, 2),
        "risk_dollars": risk_dollars,
        "reward_at_tp_dollars": reward_at_tp,
        "tier": tier,
    }


def r_multiple_from_trade(pnl_pct: float, stop_loss_pct: float) -> float:
    """Chart PnL % vs stop distance % → R-multiple."""
    if not stop_loss_pct or stop_loss_pct <= 0:
        return 0.0
    return pnl_pct / stop_loss_pct


def prop_pnl_dollars(
    equity: float,
    pnl_pct: float,
    stop_loss_pct: float,
    risk_info: dict[str, Any],
    *,
    outcome: str | None = None,
) -> float:
    """
    Dollar P&L: risk_dollars × R-multiple.
    TAKE_PROFIT caps at configured R:R; STOP_LOSS = -1R.
    """
    risk_frac = risk_info["risk_fraction"]
    rr = risk_info["risk_reward"]
    risk_d = equity * risk_frac

    if outcome == "STOP_LOSS":
        R = -1.0
    elif outcome == "TAKE_PROFIT":
        R = rr
    else:
        R = r_multiple_from_trade(pnl_pct, stop_loss_pct)
        if outcome == "TAKE_PROFIT":
            R = min(R, rr)
        R = max(-1.0, min(rr, R))

    return round(risk_d * R, 2)
