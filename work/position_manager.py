"""Open position tracking, SL/TP levels, exit checks (24h window)."""

from __future__ import annotations

import json
import re
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

Signal = Literal["LONG", "SHORT", "FLAT"]
Timeframe = Literal["5m", "15m", "1h", "4h", "1D"]

POSITIONS_FILE = Path(__file__).resolve().parent / "open_positions.json"
ALERTS_FILE = Path(__file__).resolve().parent / "alerts.log"
POSITION_TTL_HOURS = 24

# Max hold by timeframe label (hours)
TF_HOLD_HOURS = {"5m": 0.25, "15m": 1.0, "1h": 4.0, "4h": 12.0, "1D": 24.0}


def normalize_timeframe(raw: str | None) -> Timeframe:
    """Map LLM text → alert bucket: 5m | 15m | 1h | 4h | 1D."""
    text = (raw or "4h").lower()
    if re.search(r"\b1d\b|daily|24\s*h|day trade", text):
        return "1D"
    if re.search(r"4\s*[-–]?\s*8\s*h|4\s*h|3\s*[-–]?\s*6\s*h", text):
        return "4h"
    if re.search(r"1\s*[-–]?\s*2\s*h|\b1h\b|60\s*min", text):
        return "1h"
    if re.search(r"15\s*min|\b15m\b", text):
        return "15m"
    if re.search(r"5\s*min|\b5m\b|scalp", text):
        return "5m"
    if re.search(r"8\s*h|12\s*h|48\s*h", text):
        return "4h"
    return "4h"


def _volatility_pct(ohlc: list[dict] | None) -> float:
    if not ohlc:
        return 0.012
    ranges = []
    for c in ohlc[-24:]:
        close = c.get("close") or 0
        if close <= 0:
            continue
        ranges.append(abs(c["high"] - c["low"]) / close)
    if not ranges:
        return 0.012
    return max(statistics.median(ranges), 0.005)


def build_position_setup(
    signal: Signal,
    confidence: int,
    timeframe: Timeframe,
    entry_price: float,
    ohlc: list[dict] | None = None,
    trade_style: str | None = None,
    account_equity: float | None = None,
) -> dict[str, Any] | None:
    """Entry + SL/TP from price, timeframe, confidence-based R:R and risk tier."""
    from risk_sizing import compute_trade_risk, DEFAULT_ACCOUNT

    if signal == "FLAT" or entry_price <= 0:
        return None

    equity = account_equity or DEFAULT_ACCOUNT
    risk_info = compute_trade_risk(confidence, trade_style, account_equity=equity)
    rr = risk_info["risk_reward"]

    vol = _volatility_pct(ohlc)
    tf_scale = {"5m": 0.7, "15m": 0.85, "1h": 1.0, "4h": 1.15, "1D": 1.35}[timeframe]
    conf_scale = 0.85 + (min(confidence, 95) / 100) * 0.3

    sl_pct = min(0.035, max(0.004, vol * 1.4 * tf_scale / conf_scale))

    if trade_style == "counter_trend_scalp":
        sl_pct *= 0.88
    elif trade_style in ("with_macro_and_subtrend", "with_subtrend"):
        sl_pct *= 1.05

    if signal == "LONG":
        stop_loss = round(entry_price * (1 - sl_pct), 2)
        take_profit = round(entry_price * (1 + sl_pct * rr), 2)
    else:
        stop_loss = round(entry_price * (1 + sl_pct), 2)
        take_profit = round(entry_price * (1 - sl_pct * rr), 2)

    sl_dist_pct = round(abs(entry_price - stop_loss) / entry_price * 100, 2)
    tp_dist_pct = round(abs(take_profit - entry_price) / entry_price * 100, 2)

    return {
        "signal": signal,
        "confidence": confidence,
        "timeframe": timeframe,
        "entry_price": round(entry_price, 2),
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "stop_loss_pct": sl_dist_pct,
        "take_profit_pct": tp_dist_pct,
        "risk_reward": round(rr, 2),
        "max_hold_hours": TF_HOLD_HOURS[timeframe],
        "trade_style": trade_style,
        "risk_pct": risk_info["risk_pct"],
        "risk_dollars": risk_info["risk_dollars"],
        "reward_at_tp_dollars": risk_info["reward_at_tp_dollars"],
        "risk_tier": risk_info["tier"],
        "account_equity": risk_info["account_equity"],
    }


def format_trade_alert(setup: dict[str, Any]) -> str:
    sig = setup["signal"]
    icon = {"LONG": "🟢", "SHORT": "🔴", "FLAT": "⚪"}.get(sig, "⚪")
    if sig == "LONG":
        sl_tag = f"-{setup['stop_loss_pct']:.2f}%"
        tp_tag = f"+{setup['take_profit_pct']:.2f}%"
    else:
        sl_tag = f"+{setup['stop_loss_pct']:.2f}%"
        tp_tag = f"-{setup['take_profit_pct']:.2f}%"
    return (
        f"{icon} {sig} — {setup['confidence']}% · {setup['timeframe']}\n"
        f"Entry:  ${setup['entry_price']:,.2f}\n"
        f"Stop:   ${setup['stop_loss']:,.2f} ({sl_tag})\n"
        f"TP:     ${setup['take_profit']:,.2f} ({tp_tag})\n"
        f"Risk {setup.get('risk_pct', '?')}% (${setup.get('risk_dollars', 0):,.0f}) · "
        f"R:R {setup['risk_reward']} → ${setup.get('reward_at_tp_dollars', 0):,.0f} · "
        f"max hold {setup['max_hold_hours']}h"
    )


def _load_positions() -> list[dict]:
    if not POSITIONS_FILE.exists():
        return []
    return json.loads(POSITIONS_FILE.read_text())


def _save_positions(positions: list[dict]) -> None:
    POSITIONS_FILE.write_text(json.dumps(positions, indent=2))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def open_position(setup: dict[str, Any]) -> dict:
    pos = {
        "id": str(uuid.uuid4())[:8],
        "opened_at": _utcnow().isoformat(),
        "status": "open",
        **setup,
    }
    positions = [p for p in _load_positions() if p.get("status") == "open"]
    positions.append(pos)
    _save_positions(positions)
    return pos


def prune_old_positions() -> None:
    cutoff = _utcnow() - timedelta(hours=POSITION_TTL_HOURS)
    positions = _load_positions()
    changed = False
    for p in positions:
        if p.get("status") != "open":
            continue
        opened = datetime.fromisoformat(p["opened_at"])
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        if opened < cutoff:
            p["status"] = "expired"
            p["closed_at"] = _utcnow().isoformat()
            p["close_reason"] = "24h position window expired"
            changed = True
    if changed:
        _save_positions(positions)


def _close_position(p: dict, reason: str, price: float) -> dict:
    p["status"] = "closed"
    p["closed_at"] = _utcnow().isoformat()
    p["close_reason"] = reason
    p["close_price"] = price
    return p


def check_exits(
    current_price: float,
    new_signal: Signal,
    new_confidence: int,
    new_timeframe: Timeframe,
) -> list[dict]:
    """
    Alert if open positions (last 24h) should exit:
    - SL / TP hit
    - max hold for timeframe exceeded
    - new signal FLAT or reversed
    """
    if current_price <= 0:
        return []

    prune_old_positions()
    positions = _load_positions()
    alerts: list[dict] = []
    now = _utcnow()

    for p in positions:
        if p.get("status") != "open":
            continue

        opened = datetime.fromisoformat(p["opened_at"])
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        age_h = (now - opened).total_seconds() / 3600
        sig = p["signal"]
        reason = None

        if sig == "LONG":
            if current_price <= p["stop_loss"]:
                reason = f"Stop loss hit (${p['stop_loss']:,.0f})"
            elif current_price >= p["take_profit"]:
                reason = f"Take profit hit (${p['take_profit']:,.0f})"
        elif sig == "SHORT":
            if current_price >= p["stop_loss"]:
                reason = f"Stop loss hit (${p['stop_loss']:,.0f})"
            elif current_price <= p["take_profit"]:
                reason = f"Take profit hit (${p['take_profit']:,.0f})"

        if reason is None and age_h >= p.get("max_hold_hours", TF_HOLD_HOURS["4h"]):
            reason = f"Max hold {p.get('timeframe', '?')} exceeded ({age_h:.1f}h)"

        if reason is None:
            if new_signal == "FLAT" and new_confidence >= 50:
                reason = f"New signal FLAT ({new_confidence}%) — exit recommended"
            elif new_signal != "FLAT" and new_signal != sig:
                reason = f"Signal reversed → {new_signal} {new_confidence}% · {new_timeframe}"

        if reason:
            _close_position(p, reason, current_price)
            alerts.append(
                {
                    "type": "EXIT",
                    "position": p,
                    "reason": reason,
                    "current_price": current_price,
                    "age_hours": round(age_h, 2),
                }
            )

    _save_positions(positions)
    return alerts


def format_exit_alert(exit_info: dict) -> str:
    p = exit_info["position"]
    age = exit_info["age_hours"]
    return (
        f"🚨 EXIT {p['signal']} (opened {age:.1f}h ago · {p['timeframe']} frame)\n"
        f"Reason: {exit_info['reason']}\n"
        f"Price: ${exit_info['current_price']:,.2f}\n"
        f"Was: Entry ${p['entry_price']:,.2f} · SL ${p['stop_loss']:,.2f} · "
        f"TP ${p['take_profit']:,.2f}"
    )


def log_alert(message: str) -> None:
    line = f"[{_utcnow().isoformat()}] {message}\n"
    with ALERTS_FILE.open("a") as f:
        f.write(line.replace("\n\n", "\n") + "\n---\n")
    print(message)


def send_telegram(message: str) -> None:
    token = __import__("os").environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = __import__("os").environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        import requests

        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=15,
        )
    except Exception as exc:
        print(f"Telegram alert failed: {exc}")
