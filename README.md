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

## Getting signals

Signals are **LONG**, **SHORT**, or **FLAT**. Each tradeable signal includes **confidence (0–100)**, **timeframe** (5m–1D), **entry**, **stop-loss**, **take-profit**, **risk %**, and **max hold** hours.

### How the pipeline decides

```
News headlines  →  sentiment score (macro-weighted lexicon, or LLM)
       +
1h / 4h / 24h sub-trends  (from recent OHLC)
       +
Macro cycle phase  (bear until Oct 2026 — background filter, not always SHORT)
       ↓
Confluence rules  (min confidence 52, late-chase block after 16:00 UTC, bear-LONG guards, etc.)
       ↓
Learned instincts  (live + walk-forward backtest — blocks repeat loss patterns)
       ↓
Position setup  →  SL / TP / risk $ from confidence + trade style
```

**Live** uses full LLM (`analyze_with_llm`). **Default backtests** use the lexicon proxy (same filters). See [Backtests & LLM (notes)](#backtests--llm-notes) at the bottom.

### Option A — Live signal (one poll)

```bash
cd work && source .venv/bin/activate
python trade_cycle.py
```

Prints an alert like:

```
🔴 SHORT — 85% · 4h
Entry:  $62,823.03
Stop:   $63,280.70 (+0.73%)
TP:     $61,115.92 (-2.72%)
Risk 1.83% ($1,827) · R:R 3.73 → $6,822 · max hold 12.0h
```

Also writes `latest_analysis.json` and logs to `alerts.log`. With Telegram env vars set, the same text is sent to your chat.

### Option B — Dashboard (continuous)

```bash
streamlit run btc_superduper_predictor.py
```

Refreshes every `SCHEDULE_MINUTES` (default 5). Shows news, LLM analysis, sub-trends, and day-trade signal on the UI. History in `btc_predictions_history.csv`.

### Option C — Backtest one day (replay)

Simulates scheduler polls at **08:00 / 12:00 / 16:00 / 20:00 UTC**; **first actionable signal** of the day wins.

```bash
python backtest_yesterday.py --date 2026-06-24
python backtest_yesterday.py --date 2026-06-24 --llm   # Ollama instead of lexicon
```

Output includes a **poll log** (what fired each hour) and full **entry / SL / TP / outcome** if a trade was taken. JSON saved to `backtest_yesterday.json`.

### Option D — Backtest a range

```bash
python backtest_week.py --from 2026-06-22 --to 2026-06-26
python prop_account.py --from 2026-06-01 --to 2026-06-26 --equity 100000
```

### Reading a signal

| Field | Meaning |
|-------|---------|
| **LONG / SHORT / FLAT** | Direction for the next hold window, or no trade |
| **Confidence** | 52+ required to trade; higher → larger risk % and wider R:R |
| **trade_style** | e.g. `macro_correction`, `with_subtrend`, `with_macro_and_subtrend` |
| **news_score** | Negative = bearish headlines, positive = bullish (backtests) |
| **Stop / TP** | Volatility-scaled from entry; checked on hourly bars in backtest |
| **Outcome** | `TAKE_PROFIT`, `STOP_LOSS`, `MAX_HOLD`, `SESSION_END`, `NO_TRADE` |

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

Polls news, runs LLM + learned rules, outputs SL/TP via `position_manager`. Optional Telegram alerts if configured.

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

## Example signals & positions (June 2026 replay)

Real output from `python backtest_yesterday.py --date …` (confluence mode). Prices are BTC-USD at replay entry.

### Win — SHORT · take profit (Jun 24)

**Poll log:** 08:00 FLAT → **12:00 SHORT 85%** → 16:00 FLAT → 20:00 FLAT

```
🔴 SHORT — 85% · 4h · macro_correction
Entry:  $62,823   Stop: $63,281 (+0.73%)   TP: $61,116 (-2.72%)
News score: -5.56 · Macro: downtrend · 12 headlines at entry
Outcome: TAKE_PROFIT · exit $61,116 @ 14:00 UTC · PnL +2.72%
Day: open $62,645 → close $60,983 (buy & hold −2.65%)
```

Prop sim that day: **+$8,367** on ~$131k equity (2.5% risk tier, 3.73R).

### Win — SHORT · macro + sub-trend (Jun 3)

```
🔴 SHORT · macro_correction · news -16.8
Outcome: MAX_HOLD · PnL +3.27%
```

Strong bear news aligned with 4h down — one of the best days of the month.

### Loss — SHORT · stop hit (Jun 10)

**Poll log:** 08:00 SHORT 95% → 12:00 SHORT 95% → 16:00 SHORT 85% → 20:00 FLAT

```
🔴 SHORT — 95% · 4h · macro_correction
Entry:  $61,632   Stop: $62,171 (+0.87%)   TP: $59,555 (-3.37%)
News score: -8.12 · 1h ↑ · 4h flat · 24h ↓  (chop — price bounced into stop)
Outcome: STOP_LOSS · exit $62,171 @ 13:00 UTC · PnL −0.87%
Day: open $61,698 → close $61,451
```

Pattern: SHORT into **1h/4h not clearly down** — same failure mode as several June losses.

### Loss — weak news, late poll (Jun 7)

```
🔴 SHORT · with_macro_and_subtrend · news -1.0 · poll 16:00 UTC
Outcome: STOP_LOSS · PnL −1.17%
Sub-trends: 24h ↑ (+2.15%) — fading a rally without strong bear news
```

### Flat — no trade (Jun 25)

**Poll log:** all four polls FLAT (confidence below threshold or filters blocked entry)

```
⚪ FLAT all day · Macro: downtrend · 100 headlines but no confluence edge
Outcome: NO_TRADE · PnL 0%
Day: open $60,988 → close $59,704 (buy & hold −2.11% — system sat out)
```

### June scorecard (confluence backtest)

| Result | Count | Examples |
|--------|-------|----------|
| **Wins** | 14 | Jun 1, 3, 5, 9, 24 (+2.72%), 26… |
| **Losses** | 5 | Jun 4 (−0.02%), 7 (−1.17%), 10 (−0.87%), 17, 19 |
| **Flat** | 7 | Jun 2, 8, 11, 12, 16, 18, 25 |

Run any date yourself:

```bash
python backtest_yesterday.py --date 2026-06-24
```


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

## Backtests & LLM (notes)

Backtests **do** use **news + charts + macro cycle**. By default they use a **macro-weighted lexicon + confluence rules** instead of calling Ollama on every poll. Live `trade_cycle.py` uses full LLM; backtests default to the fast path so you can iterate rules and replay months in seconds.

### What each backtest path uses

| Input | Default (`confluence`) | LLM (`--llm`) | Hybrid (`SIGNAL_MODE=hybrid`) |
|-------|------------------------|---------------|-------------------------------|
| Archived news for replay day | Yes | Yes | Yes |
| Hourly chart → 1h/4h/24h sub-trends | Yes | Yes | Yes |
| Macro cycle phase | Yes | Yes | Yes |
| Ollama analysis | No — lexicon proxy | Yes — every poll | Only on borderline / FLAT-with-news days |

June 2026 results (73.7% WR, +15.7% chart PnL) were run on **confluence**, not full LLM. `prop_account.py` and `backtest_month.py` call the same default unless you opt in.

### Why LLM is not the default in backtests

1. **Speed** — Full June ≈ 26 days × 4 polls = 100+ Ollama calls (hours). Lexicon replay ≈ 30 seconds.
2. **Reproducibility** — Lexicon is deterministic; LLM answers can vary run-to-run.
3. **Lexicon as LLM proxy** — `signal_engine.py` + macro-weighted `news_sentiment.py` mirror the live pipeline: headlines → sentiment → trend filters → LONG/SHORT/FLAT.
4. **Hybrid did not beat confluence on the benchmark week** (Jun 22–26): same 100% WR and PnL; no borderline days triggered LLM.
5. **Historical LLM replay caveat** — `--llm` runs **today’s model** on **old news**. Useful, but not “what the model would have said on that date in the past.”

Losses in June were mostly **SHORT into chop** (weak news, 24h rallying, afternoon entries) — rule/filter issues, not missing LLM.

### How to backtest with LLM

**Full LLM every poll** (slow; Ollama required):

```bash
cd work && source .venv/bin/activate
export OLLAMA_BASE_URL=http://localhost:11434
export OLLAMA_MODEL=qwen3:14b

python backtest_yesterday.py --date 2026-06-24 --llm
python backtest_week.py --from 2026-06-01 --to 2026-06-26 --llm
```

**Hybrid** (confluence first, Ollama on borderline cases):

```bash
SIGNAL_MODE=hybrid python backtest_yesterday.py --date 2026-06-24
SIGNAL_MODE=hybrid python upgrade_eval.py --keep-hybrid
```

**Live pipeline** always uses LLM when Ollama is up:

```bash
python trade_cycle.py
```

### Scripts and LLM support

| Script | Default signal | Enable LLM |
|--------|----------------|------------|
| `backtest_yesterday.py` | Confluence / hybrid | `--llm` or `SIGNAL_MODE=hybrid` |
| `backtest_week.py` | Confluence / hybrid | `--llm` or `SIGNAL_MODE=hybrid` |
| `backtest_month.py` | Confluence only | `SIGNAL_MODE=hybrid` (no `--llm` flag yet) |
| `prop_account.py` | Confluence only | Uses `run_replay()` default |
| `trade_cycle.py` | Full LLM | Always (falls back if Ollama down) |

To compare confluence vs hybrid vs full LLM on the same date range, use `upgrade_eval.py` or run week backtests with and without `--llm`.

---

## License

MIT — use at your own risk. No warranty.
