"""
Hybrid upgrade: macro-aware lexicon + Ollama on borderline / disagreement cases.
Falls back to confluence-only when Ollama unavailable.
"""

from __future__ import annotations

import os
from typing import Any

from signal_engine import build_day_trade_signal


def _ollama_base_url() -> str:
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


def _ollama_available() -> bool:
    try:
        import requests

        r = requests.get(f"{_ollama_base_url()}/api/tags", timeout=3)
        return r.status_code == 200 and bool(r.json().get("models"))
    except Exception:
        return False


def llm_day_trade_signal(
    articles: list[dict],
    *,
    macro_phase: str,
    prior_ohlc: list[dict] | None,
    market_ctx: dict | None = None,
) -> dict[str, Any]:
    """Single Ollama call — same confluence filters applied after."""
    from btc_superduper_predictor import analyze_with_llm, extract_day_trade_signal

    market_ctx = market_ctx or {"price_usd": 0, "change_24h_pct": 0, "ohlc": prior_ohlc or []}
    analysis = analyze_with_llm(articles, market_ctx)
    if "error" in analysis:
        raise RuntimeError(analysis["error"])
    dt = analysis.get("day_trade") or extract_day_trade_signal(analysis)
    return {
        "signal": dt["signal"],
        "confidence": int(dt.get("confidence") or 0),
        "timeframe": dt.get("timeframe") or "4h",
        "trade_style": dt.get("trade_style") or "flat",
        "subtrends": analysis.get("subtrends") or {},
        "news_score": None,
        "method": "llm",
    }


def build_hybrid_signal(
    texts: list[str],
    articles: list[dict] | None,
    *,
    macro_phase: str,
    prior_ohlc: list[dict] | None = None,
    subtrends: dict[str, Any] | None = None,
    entry_price: float | None = None,
) -> dict[str, Any]:
    """
    Upgrade path:
    1. Macro-aware lexicon confluence (fast)
    2. If borderline or lexicon FLAT with strong article count → LLM second opinion
    3. If LLM disagrees with high-conf lexicon → FLAT (safety)
    """
    base = build_day_trade_signal(texts, macro_phase=macro_phase, prior_ohlc=prior_ohlc, subtrends=subtrends)
    mode = os.environ.get("SIGNAL_MODE", "best")

    if mode != "hybrid" or not articles or not _ollama_available():
        base["method"] = "confluence"
        return base

    need_llm = (
        base["signal"] == "FLAT"
        and base.get("reject_reason") in ("weak_news", "low_confidence", None)
        and len(articles) >= 8
    ) or (
        base["signal"] != "FLAT"
        and 52 <= base["confidence"] < 72
    )

    if not need_llm:
        base["method"] = "confluence"
        return base

    try:
        market_ctx = {
            "price_usd": entry_price or 0,
            "ohlc": prior_ohlc or [],
            "change_24h_pct": 0,
        }
        llm = llm_day_trade_signal(articles, macro_phase=macro_phase, prior_ohlc=prior_ohlc, market_ctx=market_ctx)
    except Exception:
        base["method"] = "confluence"
        return base

    # LLM returned signal — re-validate through confluence on LLM direction using fresh score
    if llm["signal"] == "FLAT":
        base["method"] = "confluence"
        return base

    if base["signal"] != "FLAT" and base["signal"] != llm["signal"] and base["confidence"] >= 75:
        return {
            **base,
            "signal": "FLAT",
            "confidence": 40,
            "trade_style": "flat",
            "reject_reason": "hybrid_disagreement",
            "method": "hybrid",
            "llm_signal": llm["signal"],
        }

    if base["signal"] == "FLAT" or llm["confidence"] > base["confidence"]:
        merged = build_day_trade_signal(
            texts,
            macro_phase=macro_phase,
            prior_ohlc=prior_ohlc,
            subtrends=llm.get("subtrends") or base.get("subtrends"),
        )
        if merged["signal"] == "FLAT" and llm["confidence"] >= 65:
            merged = {**llm, "subtrends": llm.get("subtrends") or base.get("subtrends")}
            merged["method"] = "hybrid_llm"
            return merged
        merged["method"] = "hybrid"
        merged["llm_confidence"] = llm["confidence"]
        return merged

    base["method"] = "confluence"
    return base
