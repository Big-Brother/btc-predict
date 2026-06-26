# BTC Predict

Local Bitcoin day-trading intelligence: fast news aggregation, macro-aware sentiment, confluence signals, backtests, and optional Ollama LLM analysis.

**Macro conviction (baked in):** structural bear until **October 5, 2026** — day trades still follow 1h/4h/24h sub-trends and news, not blind always-short.

> **Disclaimer:** Research and backtesting tool only. Not financial advice. Past backtest results do not guarantee future performance. Trade at your own risk.

---

## What it does

| Layer | Role |
|-------|------|
| **News** | RSS + CryptoPanic-style feeds, headline lexicon (macro-weighted) |
| **Trends** | 1h / 4h / 24h sub-trends + macro cycle phase |
| **Signals** | Confluence engine: news score + trend filters + late-chase guard |
| **Risk** | Confidence → position size (0.4–2.5%) and R:R (1.8–4.0) |
| **Learning** | Walk-forward instincts from losses → `trade_learning.json` |
| **LLM (optional)** | Ollama on borderline cases when `SIGNAL_MODE=hybrid` |

---

## Quick start

All active development lives in **`work/`**.

```bash
cd work
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Optional: copy env template from repo root
cp ../.env.example ../.env

# Smoke test (news + optional Ollama)
python smoke_test.py

# Streamlit dashboard + scheduler
streamlit run btc_superduper_predictor.py
```

**Ollama (optional, for LLM / hybrid mode):**

```bash
ollama pull qwen3:14b
ollama serve   # default http://localhost:11434
```

---

## Backtests (June 2026 replay)

Real daily replay — polls at 08:00 / 12:00 / 16:00 / 20:00 UTC, first actionable signal only.

```bash
cd work && source .venv/bin/activate

# Single day
python backtest_yesterday.py --date 2026-06-24

# Week
python backtest_week.py --from 2026-06-22 --to 2026-06-26

# Full month + walk-forward learning + notes
python backtest_month.py --from 2026-06-01 --to 2026-06-26 --learn

# Prop account sim ($100k, flex risk)
python prop_account.py --from 2026-06-01 --to 2026-06-26 --equity 100000

# Compare upgrade vs saved baseline; auto-revert if worse
python upgrade_eval.py
```

### Sample results (Jun 1–26, 2026, upgraded stack)

| Metric | Value |
|--------|--------|
| Trades | 19 (7 flat days) |
| Win rate | **73.7%** (14W / 5L) |
| Chart PnL (sum of trade %) | **+15.7%** |
| $100k prop sim (flex sizing) | **+$33,899 (+33.9%)** |

Notes and loss patterns: `work/backtest_june_notes.md` · Full JSON: `work/backtest_june_report.json`

---

## Signal modes

| `SIGNAL_MODE` | Behavior |
|---------------|----------|
| `best` (default) | Macro-weighted lexicon + confluence rules |
| `hybrid` | Above + Ollama second opinion on borderline / FLAT-with-news days |

Version snapshots (`best`, `upgrade`, `upgraded`) under `work/versions/` — restore with:

```bash
python version_manager.py restore best
python version_manager.py list
```

---

## Project layout

```
btc-predict/
├── README.md                 ← you are here
├── .env.example
├── work/                     ← main application
│   ├── btc_superduper_predictor.py   # Streamlit + live cycle
│   ├── trade_cycle.py                # Scheduled live pipeline
│   ├── signal_engine.py              # Confluence rules
│   ├── signal_hybrid.py              # Optional LLM upgrade path
│   ├── news_sentiment.py             # Macro-weighted lexicon
│   ├── news_fetcher.py               # Live news sources
│   ├── trend_context.py              # 1h/4h/24h sub-trends
│   ├── market_cycle.py               # Macro bear until Oct 2026
│   ├── risk_sizing.py                # Confidence → risk & R:R
│   ├── position_manager.py           # SL/TP + Telegram alerts
│   ├── trade_learning.py             # Walk-forward instincts
│   ├── backtest_*.py                 # Replay & prop sim
│   ├── version_manager.py            # Save / restore signal stack
│   ├── upgrade_eval.py               # Upgrade A/B + auto-revert
│   ├── data/trade_learning.json      # Learned patterns
│   └── requirements.txt
└── (legacy root scripts — use work/ instead)
```

---

## Environment variables

See `.env.example`. Common settings:

- `PROP_ACCOUNT_SIZE` — prop sim starting equity (default `100000`)
- `OLLAMA_MODEL` — model tag for hybrid / LLM backtests
- `SIGNAL_MODE` — `best` or `hybrid`
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — optional trade alerts
- `COINGECKO_API_KEY` — optional pro API for price data

---

## Live trading cycle

```bash
cd work && source .venv/bin/activate
python trade_cycle.py
```

Applies learned rules after the signal engine. Use paper trading first.

---

## Tech stack

- Python 3.11+
- Streamlit, pandas, requests, feedparser, yfinance
- Ollama (local LLM, optional)
- No cloud dependency for core backtests

---

## License

MIT — use at your own risk. No warranty.
