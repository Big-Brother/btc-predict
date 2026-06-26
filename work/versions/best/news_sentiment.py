"""Fast headline sentiment → day-trade signal (backtest proxy for LLM)."""

from __future__ import annotations

import re

BEAR_WORDS = re.compile(
    r"\b(crash|collapse|ban|hack|stolen|sec\b|lawsuit|fraud|dump|selloff|"
    r"bear|down|fall|drop|plunge|outflow|fear|crackdown|restrict|illegal|"
    r"warning|risk|bubble burst|mt\.? gox|cypriot|shutdown)\b",
    re.I,
)
BULL_WORDS = re.compile(
    r"\b(rally|surge|soar|record high|all.?time high|ath|adoption|approve|"
    r"etf|inflow|institutional|bull|up\b|rise|gain|breakout|halving|"
    r"milestone|partnership|accept|legal|growth|demand)\b",
    re.I,
)


def score_headlines(texts: list[str]) -> float:
    """Negative = bearish, positive = bullish."""
    score = 0.0
    for text in texts:
        if not text:
            continue
        score += len(BULL_WORDS.findall(text)) * 1.0
        score -= len(BEAR_WORDS.findall(text)) * 1.0
    return score


def day_trade_from_news(
    texts: list[str],
    *,
    macro_phase: str,
    min_score: float = 1.0,
    subtrends: dict | None = None,
) -> tuple[str, int, str]:
    """Legacy wrapper — delegates to confluence signal engine."""
    from signal_engine import build_day_trade_signal

    out = build_day_trade_signal(texts, macro_phase=macro_phase, subtrends=subtrends)
    if out["signal"] == "FLAT" and min_score > 0.75:
        return "FLAT", 40, "flat"
    return out["signal"], out["confidence"], out["trade_style"]
