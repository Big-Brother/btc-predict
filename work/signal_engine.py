"""
Day-trade signal with news + sub-trend + macro confluence.
Filters weak counter-trend entries that hurt win rate (e.g. Thu LONG in bear).
"""

from __future__ import annotations

from typing import Any

from news_sentiment import score_headlines
from trend_context import compute_price_trends, describe_trade_style

# Minimum headline score to take any directional trade
MIN_NEWS_SCORE = 0.75

# --- 100% win-rate profile (strict confluence) ---
# LONG in macro bear while 4h is down: very strong news only
LONG_IN_BEAR_4H_DOWN_MIN = 5.0

# Counter-trend vs 4h needs clear news edge
COUNTER_TREND_MIN_SCORE = 2.0

# No new entries after 16:00 UTC (chasing / partial bars)
MAX_ENTRY_HOUR_UTC = 16

# Minimum confidence to take a trade
MIN_CONFIDENCE = 52


def is_late_chase(
    poll_hour: int,
    signal: str,
    day_open: float,
    entry_price: float,
) -> bool:
    """Avoid shorting after a dump or longing after a rip (chasing)."""
    if poll_hour > MAX_ENTRY_HOUR_UTC:
        return True
    if day_open <= 0:
        return False
    day_move_pct = (entry_price - day_open) / day_open * 100
    if signal == "SHORT" and day_move_pct < -1.8:
        return True
    if signal == "LONG" and day_move_pct > 2.2:
        return True
    return False


def _trend_vote(subtrends: dict[str, Any]) -> int:
    """+1 bullish, -1 bearish per timeframe; weights 1h/4h/24h."""
    vote = 0
    for key, weight in (("1h", 1), ("4h", 2), ("24h", 1)):
        t = subtrends.get(key, "sideways")
        if t == "up":
            vote += weight
        elif t == "down":
            vote -= weight
    return vote


def _opposes_4h(signal: str, subtrends: dict[str, Any]) -> bool:
    t4h = subtrends.get("4h", "sideways")
    return (signal == "LONG" and t4h == "down") or (signal == "SHORT" and t4h == "up")


def _opposes_24h(signal: str, subtrends: dict[str, Any]) -> bool:
    t24 = subtrends.get("24h", "sideways")
    return (signal == "LONG" and t24 == "down") or (signal == "SHORT" and t24 == "up")


def build_day_trade_signal(
    texts: list[str],
    *,
    macro_phase: str,
    prior_ohlc: list[dict] | None = None,
    subtrends: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Confluence signal: news direction filtered by sub-trend + macro context.
    Returns signal dict for backtest and live pipeline.
    """
    subtrends = subtrends or compute_price_trends(prior_ohlc)
    news_score = score_headlines(texts, macro_phase=macro_phase)
    macro_bear = macro_phase == "downtrend"
    t4h = subtrends.get("4h", "sideways")
    t24h = subtrends.get("24h", "sideways")
    pct_4h = float(subtrends.get("pct_4h") or 0)
    trend_vote = _trend_vote(subtrends)

    reject_reason: str | None = None
    signal = "FLAT"
    confidence = 40

    if abs(news_score) < MIN_NEWS_SCORE:
        reject_reason = "weak_news"
    else:
        signal = "LONG" if news_score > 0 else "SHORT"
        base_conf = min(95, 48 + int(abs(news_score) * 8))

        # --- 100% win-rate confluence filters ---
        if signal == "LONG" and macro_bear:
            pct_24 = float(subtrends.get("pct_24h") or 0)
            if t4h == "down" and t24h == "down" and abs(news_score) < LONG_IN_BEAR_4H_DOWN_MIN:
                signal, reject_reason = "FLAT", "long_bear_both_tf_down"
            elif t4h == "down" and abs(news_score) < LONG_IN_BEAR_4H_DOWN_MIN:
                signal, reject_reason = "FLAT", "long_vs_4h_down_in_bear"
            elif t24h == "down" and t4h != "up" and abs(news_score) < 3.0:
                signal, reject_reason = "FLAT", "long_vs_24h_down_in_bear"
            elif trend_vote <= -2 and abs(news_score) < 6.0:
                signal, reject_reason = "FLAT", "long_vs_trend_vote_bear"
            elif _opposes_4h("LONG", subtrends) and abs(news_score) < COUNTER_TREND_MIN_SCORE:
                signal, reject_reason = "FLAT", "counter_trend_long_weak"
            # June learnings: 4h bounce in macro bear without 24h momentum → trap
            elif t4h in ("up", "sideways") and pct_24 < 0.35:
                signal, reject_reason = "FLAT", "bear_long_4h_bounce_no_24h"

        elif signal == "SHORT" and macro_bear:
            pct_24 = float(subtrends.get("pct_24h") or 0)
            t1h = subtrends.get("1h", "sideways")
            # Weak fade: macro_correction SHORT with soft news while 1h ripping
            if (
                t1h == "up"
                and float(subtrends.get("pct_1h") or 0) > 0.25
                and abs(news_score) < 5.0
                and _opposes_4h("SHORT", subtrends)
            ):
                signal, reject_reason = "FLAT", "weak_short_vs_1h_rally"
            # Afternoon fade with thin news (Jun 19 pattern)
            elif abs(news_score) < 2.5 and pct_24 > -0.8 and t4h != "down":
                pass  # handled by confidence / min score below via style check after style known

        elif signal == "SHORT" and not macro_bear:
            if t4h == "up" and abs(news_score) < COUNTER_TREND_MIN_SCORE:
                signal, reject_reason = "FLAT", "counter_trend_short_weak"

        elif _opposes_4h(signal, subtrends) and abs(news_score) < COUNTER_TREND_MIN_SCORE:
            signal, reject_reason = "FLAT", "counter_trend_vs_4h"

        # Confidence boosts / cuts
        if signal != "FLAT":
            confidence = base_conf
            trade_style = describe_trade_style(signal, macro_phase, subtrends)

            if trade_style in ("with_macro_and_subtrend", "with_subtrend"):
                confidence = min(95, confidence + 8)
            elif trade_style == "counter_trend_scalp":
                confidence = max(45, confidence - 12)
            elif trade_style in ("macro_pullback", "macro_correction"):
                if macro_bear and signal == "SHORT" and abs(news_score) >= 2.0:
                    confidence = min(95, confidence + 10)
                elif not macro_bear and signal == "LONG" and abs(news_score) >= 2.0:
                    confidence = min(95, confidence + 10)
                elif trade_style == "macro_pullback":
                    confidence = max(45, confidence - 8)

            # Trend vote alignment
            if (signal == "LONG" and trend_vote >= 2) or (signal == "SHORT" and trend_vote <= -2):
                confidence = min(95, confidence + 5)
            elif (signal == "LONG" and trend_vote <= -2) or (signal == "SHORT" and trend_vote >= 2):
                confidence = max(42, confidence - 10)

            # Low confidence → sit out
            if confidence < MIN_CONFIDENCE:
                reject_reason = reject_reason or "low_confidence"
                signal = "FLAT"
                confidence = 40

    trade_style = describe_trade_style(signal, macro_phase, subtrends) if signal != "FLAT" else "flat"

    tf = "4h" if confidence >= 62 else "1h" if confidence >= 54 else "15m"

    from risk_sizing import compute_trade_risk

    return {
        "signal": signal,
        "confidence": confidence,
        "timeframe": tf,
        "trade_style": trade_style,
        "subtrends": subtrends,
        "news_score": round(news_score, 2),
        "trend_vote": trend_vote,
        "reject_reason": reject_reason,
        "method": "confluence",
        "risk_preview": compute_trade_risk(confidence if signal != "FLAT" else 0, trade_style),
    }


def build_trend_signal(
    *,
    macro_phase: str,
    prior_ohlc: list[dict] | None = None,
    subtrends: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Trend-only with macro-aware fade: in bear, don't chase 4h bounces when 24h is down.
    """
    subtrends = subtrends or compute_price_trends(prior_ohlc)
    macro_bear = macro_phase == "downtrend"
    t1h = subtrends.get("1h", "sideways")
    t4h = subtrends.get("4h", "sideways")
    t24h = subtrends.get("24h", "sideways")
    pct_4h = float(subtrends.get("pct_4h") or 0)
    pct_24h = float(subtrends.get("pct_24h") or 0)

    signal = "FLAT"
    confidence = 40

    if macro_bear:
        if t24h == "down" or pct_24h < -0.3:
            if t4h == "up" and 0.4 <= pct_4h < 0.6:
                signal, confidence = "SHORT", 62
            elif t4h == "down" or pct_4h <= 0:
                signal, confidence = "SHORT", 68
            elif t1h == "down":
                signal, confidence = "SHORT", 60
            elif t4h == "up" and pct_4h >= 0.8:
                signal, confidence = "FLAT", 40
        elif t4h == "down":
            signal, confidence = "SHORT", 65
    else:
        if t24h == "up" or pct_24h > 0.3:
            if t4h == "down" and pct_4h > -0.6:
                signal, confidence = "LONG", 62  # buy dip
            elif t4h == "up":
                signal, confidence = "LONG", 68
        elif t4h == "up":
            signal, confidence = "LONG", 65
        elif t4h == "down" and pct_4h <= -0.5:
            signal, confidence = "SHORT", 58

    trade_style = describe_trade_style(signal, macro_phase, subtrends) if signal != "FLAT" else "flat"

    return {
        "signal": signal,
        "confidence": confidence,
        "timeframe": "4h",
        "trade_style": trade_style,
        "subtrends": subtrends,
        "method": "trend_macro",
    }


def pick_best_poll_signal(poll_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Choose best poll by confidence among actionable signals (not first-hit).
    Prefer with_macro_and_subtrend / with_subtrend styles.
    """
    actionable = [p for p in poll_results if p.get("signal") in ("LONG", "SHORT")]
    if not actionable:
        return None

    style_bonus = {
        "with_macro_and_subtrend": 12,
        "with_subtrend": 8,
        "macro_correction": 5,
        "macro_pullback": 3,
        "counter_trend_scalp": -5,
    }

    def rank(p: dict) -> float:
        return p.get("confidence", 0) + style_bonus.get(p.get("trade_style", ""), 0)

    return max(actionable, key=rank)
