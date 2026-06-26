"""One full poll cycle: exits → analysis → position setup → alerts."""

from __future__ import annotations

from datetime import datetime, timezone

from btc_superduper_predictor import (
    LATEST_FILE,
    analyze_with_llm,
    extract_day_trade_signal,
    fetch_btc_market,
    fetch_fast_news,
)
from market_cycle import get_cycle_state as gcs
from position_manager import (
    build_position_setup,
    check_exits,
    format_exit_alert,
    format_trade_alert,
    log_alert,
    normalize_timeframe,
    open_position,
    send_telegram,
)
from trend_context import compute_price_trends


def run_trade_cycle(*, open_new_positions: bool = True) -> dict:
    cycle = gcs()
    market = fetch_btc_market()
    price = float(market.get("price_usd") or 0)
    ohlc = market.get("ohlc")

    exit_alerts = check_exits(price, "FLAT", 0, "4h")

    articles = fetch_fast_news()
    if not articles:
        return {"error": "No news", "exit_alerts": exit_alerts}

    analysis = analyze_with_llm(articles, market)
    if "error" in analysis:
        return {"error": analysis["error"], "exit_alerts": exit_alerts}

    day_trade = analysis.get("day_trade") or extract_day_trade_signal(analysis)
    signal = day_trade["signal"]
    confidence = int(day_trade.get("confidence") or 0)
    timeframe = normalize_timeframe(day_trade.get("timeframe"))
    subtrends = analysis.get("subtrends") or compute_price_trends(ohlc)

    # Walk-forward learned rules (from past real losses only)
    if signal in ("LONG", "SHORT"):
        from trade_learning import LearningState, apply_learned_rules

        ls = LearningState.load()
        if ls.instincts:
            signal, lr = apply_learned_rules(
                signal,
                macro_phase=cycle.phase,
                trade_style=day_trade.get("trade_style") or "flat",
                news_score=float(analysis.get("news_score") or day_trade.get("news_score") or 0),
                confidence=confidence,
                subtrends=subtrends,
                poll_hour=datetime.now(timezone.utc).hour,
                instincts=ls.instincts,
            )
            if lr:
                day_trade["signal"] = signal
                day_trade["reject_reason"] = lr
                day_trade["confidence"] = 40
                confidence = 40

    exit_alerts = check_exits(price, signal, confidence, timeframe)
    for ex in exit_alerts:
        msg = format_exit_alert(ex)
        log_alert(msg)
        send_telegram(msg)

    setup = build_position_setup(
        signal, confidence, timeframe, price, ohlc,
        trade_style=day_trade.get("trade_style"),
    )
    new_position = None
    entry_alert = None

    if setup and open_new_positions:
        entry_alert = format_trade_alert(setup)
        if signal in ("LONG", "SHORT"):
            new_position = open_position(setup)
            log_alert(entry_alert)
            send_telegram(entry_alert)

    if LATEST_FILE.exists():
        import json

        payload = json.loads(LATEST_FILE.read_text())
        payload["subtrends"] = subtrends
        payload["analysis"]["day_trade"] = day_trade
        payload["position_setup"] = setup
        payload["entry_alert"] = entry_alert
        payload["exit_alerts"] = [
            {"reason": e["reason"], "position_id": e["position"].get("id")} for e in exit_alerts
        ]
        payload["market_price"] = price
        LATEST_FILE.write_text(json.dumps(payload, indent=2))

    return {
        "signal": signal,
        "confidence": confidence,
        "timeframe": timeframe,
        "trade_style": day_trade.get("trade_style"),
        "subtrends": subtrends,
        "setup": setup,
        "entry_alert": entry_alert,
        "exit_alerts": exit_alerts,
        "price": price,
        "articles": len(articles),
        "new_position": new_position,
    }
