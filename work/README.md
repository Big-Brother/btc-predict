# BTC Superduper Predictor

**A fast, private, bias-driven Bitcoin news aggregator and predictive analysis platform.**

### Vision
Build the **strongest local trading intelligence system** for Bitcoin with a hard-coded conviction:  
**Bitcoin remains in an overall downtrend until October 5th, 2026.**

Speed of information + structured LLM reasoning + strong bias = **real trading edge**.

### Core Philosophy
- **Speed is edge** — Fast news delivery is prioritized.
- **Conviction matters** — Strong downtrend bias is baked into every analysis.
- **Privacy first** — Fully local (runs on your machine).
- **Actionable output** — Designed for traders, not just analysts.

### Features
- Real-time BTC news from 200+ sources (one fast API call)
- Local LLM analysis via Ollama (Qwen3 14B or better)
- Strong user bias enforcement
- Auto-refreshing Streamlit dashboard
- Full prediction history + CSV logging
- Background scheduling (every 5–15 minutes)
- Structured JSON output for further automation
- Telegram alerts ready (optional)

### Tech Stack
- **Python 3**
- **Ollama** (Local LLM)
- **Streamlit** (Dashboard)
- **Requests + Schedule** (News + Orchestration)
- **Pandas** (History tracking)

### Project Structure
btc-superduper-predictor/
├── btc_superduper_predictor.py     # Main script + dashboard
├── latest_analysis.json
├── btc_predictions_history.csv
├── README.md
└── (optional) telegram_bot.py

