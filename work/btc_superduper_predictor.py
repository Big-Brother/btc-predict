import os
import json
import requests
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import schedule
import time
import threading

from news_fetcher import NewsFetchError, fetch_live_news
from market_cycle import build_day_trade_schema, build_macro_context, get_cycle_state
from position_manager import (
    build_position_setup,
    format_trade_alert,
    normalize_timeframe,
)
from trend_context import compute_price_trends, describe_trade_style
from signal_engine import build_day_trade_signal

# ====================== CONFIG ======================
WORK_DIR = Path(__file__).resolve().parent
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:14b")
NEWS_USER_AGENT = os.environ.get(
    "NEWS_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)
HISTORY_FILE = WORK_DIR / "btc_predictions_history.csv"
LATEST_FILE = WORK_DIR / "latest_analysis.json"
OPEN_POSITIONS_FILE = WORK_DIR / "open_positions.json"
ALERTS_LOG = WORK_DIR / "alerts.log"
COINGECKO_OHLC_URL = "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc"
COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
SCHEDULE_MINUTES = int(os.environ.get("SCHEDULE_MINUTES", "5"))
# ===================================================

def extract_day_trade_signal(analysis: dict) -> dict:
    """Day-trade LONG/SHORT/FLAT from LLM news analysis — separate from fixed macro belief."""
    signal = (analysis.get("day_trade_signal") or analysis.get("trade_signal") or "FLAT").upper()
    if signal not in ("LONG", "SHORT", "FLAT"):
        short = analysis.get("short_term_0_48h") or {}
        direction = (short.get("direction") or "sideways").lower()
        signal = {"up": "LONG", "down": "SHORT"}.get(direction, "FLAT")

    confidence = analysis.get("day_trade_confidence") or analysis.get("signal_confidence") or 0
    try:
        confidence = int(confidence)
    except (TypeError, ValueError):
        confidence = 0

    return {
        "signal": signal,
        "confidence": confidence,
        "timeframe": analysis.get("day_trade_timeframe") or "4h",
        "timeframe_bucket": normalize_timeframe(analysis.get("day_trade_timeframe")),
        "rationale": analysis.get("day_trade_rationale") or analysis.get("immediate_trading_implication") or "",
        "dominant_subtrend": analysis.get("dominant_subtrend"),
        "trade_style": analysis.get("trade_style"),
        "news_sentiment": analysis.get("news_sentiment") or analysis.get("overall_sentiment"),
    }


def fetch_btc_market() -> dict:
    """Live BTC price + recent OHLC candles for the dashboard chart."""
    headers = {"User-Agent": NEWS_USER_AGENT}
    api_key = os.environ.get("COINGECKO_API_KEY")
    if api_key:
        headers["x-cg-pro-api-key"] = api_key

    price_resp = requests.get(
        COINGECKO_PRICE_URL,
        params={"ids": "bitcoin", "vs_currencies": "usd", "include_24hr_change": "true"},
        headers=headers,
        timeout=15,
    )
    price_resp.raise_for_status()
    price_block = price_resp.json().get("bitcoin", {})

    ohlc_resp = requests.get(
        COINGECKO_OHLC_URL,
        params={"vs_currency": "usd", "days": 1},
        headers=headers,
        timeout=15,
    )
    ohlc_resp.raise_for_status()
    ohlc = ohlc_resp.json()

    rows = []
    for candle in ohlc:
        ts_ms, open_p, high_p, low_p, close_p = candle
        rows.append(
            {
                "time": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
            }
        )

    return {
        "price_usd": price_block.get("usd"),
        "change_24h_pct": price_block.get("usd_24h_change"),
        "ohlc": rows,
    }

def fetch_fast_news():
    try:
        return fetch_live_news(user_agent=NEWS_USER_AGENT)
    except NewsFetchError as e:
        print(f"News error: {e}")
        return []
    except Exception as e:
        print(f"News error: {e}")
        return []

def analyze_with_llm(articles, market: dict | None = None):
    if not articles:
        return {"error": "No news"}

    if market is None:
        market = fetch_btc_market()

    cycle = get_cycle_state()
    macro_context = build_macro_context(cycle)
    schema = build_day_trade_schema()
    subtrends = compute_price_trends(market.get("ohlc"))

    news_text = "\n\n".join([f"[{a['source']}] {a['title']}\n{a['summary']}" for a in articles])

    prompt = f"""{macro_context}

Current UTC: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")}
BTC price: ${market.get('price_usd', 0):,.0f} (24h {market.get('change_24h_pct', 0):+.2f}%)

=== ACTIVE SUB-TRENDS (trade WITH these swings) ===
{subtrends.get('summary', 'n/a')}
Use the sub-trend matching your `day_trade_timeframe`. Macro is background; **this trade follows the active swing**.

=== YOUR JOB: DAY TRADE FOR THE CURRENT SWING ===
Read news + sub-trends. Output LONG / SHORT / FLAT for the **next hold window only**.
Prefer with_subtrend / with_macro_and_subtrend. Counter-trend only if news is very strong.
Use FLAT when news conflicts with 4h trend and macro without clear edge.

TODAY'S NEWS ({len(articles)} articles):
{news_text}

Return **valid JSON only**:
{schema}
"""

    try:
        resp = requests.post("http://localhost:11434/api/chat", json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0.25,
            "format": "json"
        }, timeout=100)

        content = resp.json()["message"]["content"]
        analysis = json.loads(content)

        # Inject fixed 100% macro belief — never LLM-estimated
        analysis.update(cycle.fixed_macro_belief())
        analysis["cycle"] = cycle.to_dict()
        analysis["subtrends"] = subtrends
        day_trade = extract_day_trade_signal(analysis)
        # Confluence filter — veto weak counter-trend entries (same rules as backtest)
        ref = build_day_trade_signal(
            [f"{a.get('title','')} {a.get('summary','')}" for a in articles],
            macro_phase=cycle.phase,
            subtrends=subtrends,
        )
        if day_trade["signal"] in ("LONG", "SHORT") and ref["signal"] == "FLAT":
            day_trade["signal"] = "FLAT"
            day_trade["confidence"] = 40
            day_trade["reject_reason"] = ref.get("reject_reason")
        elif ref["signal"] in ("LONG", "SHORT"):
            day_trade["confidence"] = max(int(day_trade.get("confidence") or 0), ref["confidence"])
            if not day_trade.get("trade_style"):
                day_trade["trade_style"] = ref["trade_style"]
        if not day_trade.get("trade_style"):
            day_trade["trade_style"] = describe_trade_style(
                day_trade["signal"], cycle.phase, subtrends
            )
        analysis["day_trade"] = day_trade

        payload = {
            "timestamp": datetime.now().isoformat(),
            "articles_count": len(articles),
            "cycle": cycle.to_dict(),
            "analysis": analysis,
        }
        LATEST_FILE.write_text(json.dumps(payload, indent=2))

        row = {
            "timestamp": datetime.now().isoformat(),
            "cycle_phase": cycle.phase,
            "macro_belief": "100%",
            "day_trade_signal": analysis["day_trade"]["signal"],
            "day_trade_confidence": analysis["day_trade"]["confidence"],
            "timeframe": analysis["day_trade"].get("timeframe_bucket"),
            "trade_style": analysis["day_trade"].get("trade_style"),
            "subtrend": analysis["day_trade"].get("dominant_subtrend"),
            "news_sentiment": analysis["day_trade"].get("news_sentiment"),
            "rationale": analysis["day_trade"].get("rationale"),
        }
        if HISTORY_FILE.exists():
            df = pd.read_csv(HISTORY_FILE)
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        else:
            df = pd.DataFrame([row])
        df.to_csv(HISTORY_FILE, index=False)

        return analysis
    except Exception as e:
        return {"error": str(e)}

# ====================== DASHBOARD ======================
import streamlit as st

def run_dashboard():
    st.set_page_config(page_title="BTC Superduper Predictor", layout="wide")
    cycle = get_cycle_state()

    st.title("🚀 BTC Superduper Predictor — Day Trade Signals")
    st.caption(
        f"Structural macro: **{cycle.macro_belief_label}** (100% background bias) · "
        f"Day trades follow **active sub-trends** · flip {cycle.next_flip:%b %d, %Y}"
    )

    try:
        market = fetch_btc_market()
    except Exception as exc:
        market = None
        st.warning(f"Could not load BTC market data: {exc}")

    if LATEST_FILE.exists():
        data = json.loads(LATEST_FILE.read_text())
        analysis = data["analysis"]
        day_trade = analysis.get("day_trade") or extract_day_trade_signal(analysis)
        setup = data.get("position_setup") or analysis.get("position_setup")
        signal = day_trade["signal"]
        confidence = day_trade["confidence"]
        tf = day_trade.get("timeframe_bucket") or normalize_timeframe(day_trade.get("timeframe"))
        trade_style = day_trade.get("trade_style")
        subtrends = data.get("subtrends") or analysis.get("subtrends") or {}

        macro_prob = analysis.get(cycle.macro_belief_field, "100%")

        if market and market.get("ohlc") and not setup and signal != "FLAT" and market.get("price_usd"):
            setup = build_position_setup(
                signal, confidence, tf, float(market["price_usd"]), market.get("ohlc"),
                trade_style=trade_style,
            )

        if market and market.get("ohlc"):
            chart_df = pd.DataFrame(market["ohlc"]).set_index("time")
            st.subheader("BTC Chart (24h) — Day Trade Signal")
            st.line_chart(chart_df["close"], color="#f7931a")

        signal_cols = st.columns([2, 1, 1, 1])
        with signal_cols[0]:
            if signal == "SHORT":
                st.error(f"### 🔴 {signal} — {confidence}% · {tf}")
            elif signal == "LONG":
                st.success(f"### 🟢 {signal} — {confidence}% · {tf}")
            else:
                st.warning(f"### ⚪ {signal} — {confidence}% · {tf}")
            if trade_style:
                style_labels = {
                    "with_subtrend": "↗ with active sub-trend",
                    "with_macro_and_subtrend": "✓ macro + sub-trend aligned",
                    "counter_trend_scalp": "⚡ counter-trend scalp",
                    "macro_pullback": "↩ macro pullback / bounce",
                    "macro_correction": "↩ macro correction",
                }
                st.caption(style_labels.get(trade_style, trade_style))
            if subtrends.get("summary"):
                st.caption(f"Sub-trends: {subtrends['summary']}")

        if setup:
            st.subheader("Position setup")
            pc1, pc2, pc3, pc4, pc5 = st.columns(5)
            with pc1:
                st.metric("Entry", f"${setup['entry_price']:,.2f}")
            with pc2:
                st.metric("Stop loss", f"${setup['stop_loss']:,.2f}", delta=f"-{setup['stop_loss_pct']}%")
            with pc3:
                st.metric("Take profit", f"${setup['take_profit']:,.2f}", delta=f"+{setup['take_profit_pct']}%")
            with pc4:
                st.metric("R:R", setup.get("risk_reward"), delta=f"max {setup.get('max_hold_hours')}h")
            with pc5:
                st.metric(
                    "Account risk",
                    f"{setup.get('risk_pct', 0)}%",
                    delta=f"${setup.get('risk_dollars', 0):,.0f} → ${setup.get('reward_at_tp_dollars', 0):,.0f} TP",
                )
            if setup.get("risk_tier"):
                st.caption(f"Size tier: **{setup['risk_tier']}** · equity ${setup.get('account_equity', 0):,.0f}")

        exit_alerts = data.get("exit_alerts") or []
        if exit_alerts:
            st.subheader("Exit alerts (this cycle)")
            for ex in exit_alerts:
                st.warning(ex.get("reason", ex))

        alerts_path = ALERTS_LOG
        if alerts_path.exists():
            with st.expander("Recent alerts log"):
                st.text(alerts_path.read_text()[-4000:])

        with signal_cols[1]:
            st.metric("Day Trade", signal)
        with signal_cols[2]:
            st.metric("Macro Belief", macro_prob)
        with signal_cols[3]:
            if market:
                st.metric(
                    "BTC Price",
                    f"${market['price_usd']:,.0f}" if market.get("price_usd") else "N/A",
                    delta=f"{market.get('change_24h_pct', 0):.2f}%" if market.get("change_24h_pct") is not None else None,
                )

        st.subheader("Day Trade Rationale")
        st.info(day_trade.get("rationale") or "No rationale yet.")

        st.subheader("News Drivers")
        for d in analysis.get("key_drivers", []):
            st.markdown(f"- {d}")

        with st.expander("Fixed macro cycle (background)"):
            st.write(f"**Phase:** {cycle.phase_label}")
            st.write(f"**Day:** {cycle.day_in_phase} / {cycle.days_in_phase}")
            st.write(f"**Ends:** {cycle.phase_end}")
            st.write(f"**Next flip:** {cycle.next_flip} → {cycle.next_phase}")
            st.write(f"**Belief:** {cycle.macro_belief_label} = **100%** (operator conviction, not LLM output)")

        summary_path = WORK_DIR / "backtest_summary.json"
        equity_path = WORK_DIR / "backtest_equity.csv"
        if summary_path.exists() and equity_path.exists():
            with st.expander("Macro backtest (BTC genesis → today)"):
                bt = json.loads(summary_path.read_text())
                st.caption(bt.get("note", ""))
                b1, b2, b3 = st.columns(3)
                with b1:
                    st.metric("Macro strategy CAGR", f"{bt.get('macro_strategy_cagr_pct', 0):+.1f}%")
                with b2:
                    st.metric("Buy & hold CAGR", f"{bt.get('buy_hold_cagr_pct', 0):+.1f}%")
                with b3:
                    st.metric("Backtest period", f"{bt.get('years', 0)}y")
                eq = pd.read_csv(equity_path)
                eq["date"] = pd.to_datetime(eq["date"])
                chart = eq.set_index("date")[["strategy_equity", "buy_hold_equity"]]
                chart.columns = ["Macro LONG/SHORT", "Buy & Hold"]
                st.line_chart(chart)
                st.caption(
                    f"From ${bt.get('start_price_usd')} ({bt.get('data_from')}) · "
                    f"Run `python backtest.py` to refresh"
                )

    else:
        st.warning("No analysis yet. Run the predictor first.")
        if market and market.get("ohlc"):
            chart_df = pd.DataFrame(market["ohlc"]).set_index("time")
            st.subheader("BTC Chart (24h)")
            st.line_chart(chart_df["close"], color="#f7931a")

    if st.button("🔄 Refresh Analysis Now"):
        with st.spinner("Fetching news + running LLM..."):
            from trade_cycle import run_trade_cycle

            run_trade_cycle()
            st.success("Updated!")
            st.rerun()

# ====================== SCHEDULER ======================
def run_analysis_job():
    from trade_cycle import run_trade_cycle

    cycle = get_cycle_state()
    print(f"[{datetime.now()}] Trade cycle ({cycle.phase_label}, poll every {SCHEDULE_MINUTES}m)...")
    result = run_trade_cycle()
    if result.get("entry_alert"):
        print(result["entry_alert"])
    for ex in result.get("exit_alerts") or []:
        from position_manager import format_exit_alert

        print(format_exit_alert(ex))


def start_scheduler():
    schedule.every(SCHEDULE_MINUTES).minutes.do(run_analysis_job)
    print(f"Scheduler: every {SCHEDULE_MINUTES} minutes (set SCHEDULE_MINUTES=1|5|10)")
    while True:
        schedule.run_pending()
        time.sleep(30)

# ====================== MAIN ======================
if __name__ == "__main__":
    import sys

    if "--dashboard" in sys.argv:
        run_dashboard()
    else:
        print(f"Starting BTC Superduper Predictor ({get_cycle_state().phase_label})...")

        from trade_cycle import run_trade_cycle

        result = run_trade_cycle()
        if result.get("entry_alert"):
            print(result["entry_alert"])

        threading.Thread(target=start_scheduler, daemon=True).start()

        print("Launching dashboard at http://localhost:8501")
        import subprocess
        subprocess.run(["streamlit", "run", __file__, "--", "--dashboard"])