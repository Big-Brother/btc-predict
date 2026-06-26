import requests
import json
from datetime import datetime
from pathlib import Path
import pandas as pd
import schedule
import time
import threading

# ====================== CONFIG ======================
OLLAMA_MODEL = "qwen3:14b"
USER_BIAS = """
You are a elite Bitcoin trader-analyst with a very strong conviction:
Bitcoin is in an **overall downtrend that should persist until October 5th, 2026**.
You are highly skeptical of bullish narratives. Only overwhelming, multi-source, sustained evidence can temporarily challenge this thesis.
Focus on trading edge: immediate implications, risk management, and probability.
"""

NEWS_API_URL = "https://cryptocurrency.cv/api/news?limit=40&coins=BTC"  # Fastest free aggregator

HISTORY_FILE = Path("btc_predictions_history.csv")
LATEST_FILE = Path("latest_analysis.json")
# ===================================================

def fetch_fast_news():
    try:
        r = requests.get(NEWS_API_URL, timeout=12)
        r.raise_for_status()
        articles = r.json().get("articles", [])[:35]
        return [{
            "title": a.get("title"),
            "summary": a.get("description", a.get("summary", ""))[:700],
            "source": a.get("source", "Unknown"),
            "url": a.get("url"),
            "published": a.get("published_at")
        } for a in articles]
    except Exception as e:
        print(f"News error: {e}")
        return []

def analyze_with_llm(articles):
    if not articles:
        return {"error": "No news"}

    news_text = "\n\n".join([f"[{a['source']}] {a['title']}\n{a['summary']}" for a in articles])

    prompt = f"""{USER_BIAS}

Current UTC: {datetime.utcnow().strftime("%Y-%m-%d %H:%M")}

HIGH-VELOCITY BTC NEWS:
{news_text}

Deliver elite trading analysis. Respect the downtrend thesis strongly.

Return **valid JSON only**:
{{
  "timestamp": "YYYY-MM-DD HH:MM",
  "overall_sentiment": "bearish | bullish | neutral",
  "downtrend_until_oct5_probability": "XX%",
  "short_term_0_48h": {{"direction": "down/up/sideways", "confidence": XX}},
  "until_oct5_outlook": "Concise high-conviction reasoning",
  "key_drivers": ["top 4-6 points"],
  "immediate_trading_implication": "What a trader should do right now",
  "risk_level": "Low/Medium/High",
  "final_bias_score": -100 to +100
}}
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

        # Save latest
        payload = {"timestamp": datetime.now().isoformat(), "articles_count": len(articles), "analysis": analysis}
        LATEST_FILE.write_text(json.dumps(payload, indent=2))

        # Append to history
        row = {
            "timestamp": datetime.now().isoformat(),
            "sentiment": analysis.get("overall_sentiment"),
            "downtrend_prob": analysis.get("downtrend_until_oct5_probability"),
            "short_direction": analysis.get("short_term_0_48h", {}).get("direction"),
            "bias_score": analysis.get("final_bias_score"),
            "implication": analysis.get("immediate_trading_implication")
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
    st.title("🚀 BTC Superduper Predictor")
    st.caption("Local • Private • Downtrend-Biased until Oct 5th, 2026")

    if LATEST_FILE.exists():
        data = json.loads(LATEST_FILE.read_text())
        analysis = data["analysis"]

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Sentiment", analysis.get("overall_sentiment", "N/A").upper(), 
                      delta=analysis.get("downtrend_until_oct5_probability"))
        with col2:
            short = analysis.get("short_term_0_48h", {})
            st.metric("0-48h Direction", short.get("direction", "N/A"), delta=f"{short.get('confidence', 0)}%")
        with col3:
            st.metric("Bias Score", f"{analysis.get('final_bias_score', 0)}", delta="vs thesis")

        st.subheader("Immediate Trading Implication")
        st.info(analysis.get("immediate_trading_implication", "No data"))

        st.subheader("Key Drivers")
        for d in analysis.get("key_drivers", []):
            st.markdown(f"- {d}")

        st.subheader("Until Oct 5th Outlook")
        st.write(analysis.get("until_oct5_outlook", ""))

    else:
        st.warning("No analysis yet. Run the predictor first.")

    if st.button("🔄 Refresh Analysis Now"):
        with st.spinner("Fetching news + running LLM..."):
            articles = fetch_fast_news()
            result = analyze_with_llm(articles)
            st.success("Updated!")
            st.rerun()

# ====================== SCHEDULER ======================
def run_analysis_job():
    print(f"[{datetime.now()}] Running scheduled analysis...")
    articles = fetch_fast_news()
    analyze_with_llm(articles)

def start_scheduler():
    schedule.every(10).minutes.do(run_analysis_job)   # Change to 5 for more aggression
    while True:
        schedule.run_pending()
        time.sleep(30)

# ====================== MAIN ======================
if __name__ == "__main__":
    print("Starting BTC Superduper Predictor...")
    
    # Run first analysis
    articles = fetch_fast_news()
    analyze_with_llm(articles)
    
    # Start scheduler in background
    threading.Thread(target=start_scheduler, daemon=True).start()
    
    # Launch dashboard
    print("Launching dashboard at http://localhost:8501")
    import subprocess
    subprocess.run(["streamlit", "run", __file__, "--", "--dashboard"])