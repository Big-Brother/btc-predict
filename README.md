# BTC Predict

Local Bitcoin day-trading intelligence: fast news aggregation, macro-aware sentiment, confluence signals, backtests, walk-forward learning, and optional LLM analysis via **Ollama** (local or remote).

**Macro conviction (baked in):** structural bear until **October 5, 2026** — day trades still follow 1h/4h/24h sub-trends and news, not blind always-short.

> **Disclaimer:** Research and backtesting tool only. Not financial advice. Past backtest results do not guarantee future performance. Trade at your own risk.

---

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| **Python 3.11+** | 3.12 / 3.14 tested in dev |
| **Internet** | Live news + price data (CoinGecko, RSS feeds) |
| **Ollama** (optional) | For LLM dashboard, `--llm` backtests, or `SIGNAL_MODE=hybrid` |
| **Telegram** (optional) | Trade alerts via bot token |

Core backtests and the confluence signal engine **do not require an LLM**.

---

## Installation

```bash
git clone https://github.com/Big-Brother/btc-predict.git
cd btc-predict/work

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp ../.env.example ../.env         # edit as needed
python smoke_test.py               # verify news (+ Ollama if running)
```

### Optional: Ollama (local LLM)

```bash
# Install from https://ollama.com — then:
ollama pull qwen3:14b              # or llama3, deepseek-r1, etc.
ollama serve                       # default http://localhost:11434
```

---

## Usage

All commands run from **`work/`** with the venv activated.

### 1. Streamlit dashboard (live news + analysis)

```bash
streamlit run btc_superduper_predictor.py
```

Auto-refreshes on a schedule (`SCHEDULE_MINUTES`, default 5). Writes `latest_analysis.json` and appends to `btc_predictions_history.csv`.

### 2. Scheduled trade cycle (signals + learning + alerts)

```bash
python trade_cycle.py
```

Polls news, runs confluence + learned rules, outputs SL/TP via `position_manager`. Optional Telegram alerts if configured.

### 3. Backtests (historical replay)

Polls at **08:00 / 12:00 / 16:00 / 20:00 UTC** — first actionable signal per day.

```bash
# Single day
python backtest_yesterday.py --date 2026-06-24

# Week range
python backtest_week.py --from 2026-06-22 --to 2026-06-26

# Full month + walk-forward learning + markdown notes
python backtest_month.py --from 2026-06-01 --to 2026-06-26 --learn

# Prop account sim ($100k, confidence-flex risk sizing)
python prop_account.py --from 2026-06-01 --to 2026-06-26 --equity 100000

# A/B upgrade vs baseline (auto-revert if worse)
python upgrade_eval.py
```

### 4. Signal modes

| Mode | How to enable | Behavior |
|------|---------------|----------|
| **Confluence** (default) | `SIGNAL_MODE=best` or unset | Macro-weighted lexicon + trend filters |
| **Hybrid** | `SIGNAL_MODE=hybrid` | Confluence + Ollama on borderline days |
| **Full LLM backtest** | `python backtest_yesterday.py --llm` | Ollama for every replay day (slow) |

```bash
# Hybrid example
SIGNAL_MODE=hybrid python backtest_yesterday.py --date 2026-06-24
```

### Sample backtest results (Jun 1–26, 2026)

| Metric | Value |
|--------|--------|
| Trades | 19 (7 flat days) |
| Win rate | **73.7%** (14W / 5L) |
| Chart PnL (sum of trade %) | **+15.7%** |
| $100k prop sim (flex sizing) | **+$33,899 (+33.9%)** |

Details: `work/backtest_june_notes.md` · `work/backtest_june_report.json`

---

## Linking your own LLM (Ollama)

The stack uses the **Ollama HTTP API** (`/api/chat` with JSON output). Any server that speaks this protocol works — local Ollama, a remote machine on your LAN, or a cloud VM running Ollama.

### Environment variables

Set in `.env` (repo root) or export before running:

```bash
OLLAMA_BASE_URL=http://localhost:11434   # change to your endpoint
OLLAMA_MODEL=qwen3:14b                   # must match a model on that server
```

### Examples

**Local (default)**

```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:14b
```

**Remote Ollama on another machine**

```bash
OLLAMA_BASE_URL=http://192.168.1.121:11434
OLLAMA_MODEL=qwen3:14b
```

**Remote with auth / reverse proxy**

Point `OLLAMA_BASE_URL` at your proxy base URL (no trailing slash). The app calls `{OLLAMA_BASE_URL}/api/chat` and `{OLLAMA_BASE_URL}/api/tags`.

```bash
OLLAMA_BASE_URL=https://ollama.myserver.example
OLLAMA_MODEL=llama3:latest
```

If your proxy adds headers (API keys), set them in `work/btc_superduper_predictor.py` in the `analyze_with_llm` request — or put Ollama behind a local tunnel.

### Verify connectivity

```bash
curl "$OLLAMA_BASE_URL/api/tags"
python smoke_test.py
```

### Model recommendations

| Model | Use case |
|-------|----------|
| `qwen3:14b` | Default — good JSON + reasoning |
| `llama3:latest` | Faster, lighter |
| `deepseek-r1:8b` | Alternative reasoning model |

Smaller models work for experiments; confluence mode (`SIGNAL_MODE=best`) is recommended for backtests without LLM latency.

### Where LLM is used

| Component | LLM role |
|-----------|----------|
| `btc_superduper_predictor.py` | Dashboard + `analyze_with_llm()` |
| `trade_cycle.py` | Live cycle (full LLM path) |
| `signal_hybrid.py` | Second opinion on borderline confluence signals |
| `backtest_yesterday.py --llm` | Full LLM replay (slow) |

---

## Environment variables

Copy `.env.example` → `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen3:14b` | Model name on that server |
| `SIGNAL_MODE` | `best` | `best` (confluence) or `hybrid` |
| `SCHEDULE_MINUTES` | `5` | Dashboard / cycle poll interval |
| `PROP_ACCOUNT_SIZE` | `100000` | Prop sim starting equity |
| `COINGECKO_API_KEY` | — | Optional pro API for prices |
| `TELEGRAM_BOT_TOKEN` | — | Optional alert bot |
| `TELEGRAM_CHAT_ID` | — | Optional alert chat |
| `MIN_RISK_PCT` / `MAX_RISK_PCT` | `0.004` / `0.025` | Flex risk bounds |
| `NEWS_USER_AGENT` | Chrome UA | Some feeds require a browser UA |

---

## Project layout

```
btc-predict/
├── README.md
├── .env.example
├── work/                          ← run everything from here
│   ├── btc_superduper_predictor.py   # Streamlit + LLM analysis
│   ├── trade_cycle.py                # Scheduled live pipeline
│   ├── signal_engine.py              # Confluence rules
│   ├── signal_hybrid.py              # Optional LLM upgrade
│   ├── news_sentiment.py             # Macro-weighted lexicon
│   ├── news_fetcher.py               # Live news sources
│   ├── trend_context.py              # 1h/4h/24h sub-trends
│   ├── market_cycle.py               # Macro bear until Oct 2026
│   ├── risk_sizing.py                # Confidence → risk & R:R
│   ├── position_manager.py           # SL/TP + Telegram
│   ├── trade_learning.py             # Walk-forward instincts
│   ├── backtest_*.py                 # Replay & prop sim
│   ├── version_manager.py            # Save / restore signal stack
│   ├── upgrade_eval.py               # Upgrade A/B + auto-revert
│   ├── smoke_test.py                 # Install verification
│   ├── data/trade_learning.json      # Learned patterns
│   └── requirements.txt
└── scripts/publish.sh
```

---

## Version snapshots

Saved signal stacks under `work/versions/` (`best`, `upgrade`, `upgraded`):

```bash
python version_manager.py list
python version_manager.py restore best
python version_manager.py save my-tweak
```

---

## Self-improvement loop

1. Losses append pattern signatures to `data/trade_learning.json`
2. `backtest_month.py --learn` applies instincts **only to future days** (no peeking)
3. Live: `trade_learning.apply_learned_rules()` runs after the signal engine
4. Promote repeated instincts into `signal_engine.py` filters

---

## Tech stack

Python · Streamlit · pandas · requests · feedparser · yfinance · Ollama (optional)

---

## License

MIT — use at your own risk. No warranty.
