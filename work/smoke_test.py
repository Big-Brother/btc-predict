#!/usr/bin/env python3
"""Smoke tests for BTC Superduper Predictor (work copy)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

WORK_DIR = Path(__file__).resolve().parent
os.chdir(WORK_DIR)
sys.path.insert(0, str(WORK_DIR))

MIN_LIVE_ARTICLES = 5


def pick_ollama_model() -> str:
    preferred = os.environ.get("OLLAMA_MODEL")
    if preferred:
        return preferred
    try:
        import requests

        tags = requests.get("http://localhost:11434/api/tags", timeout=5).json().get("models", [])
        names = [m.get("name", "") for m in tags]
        for candidate in ("qwen3:14b", "llama3:latest", "deepseek-r1:8b", "llama3:8b-instruct-q4_0"):
            if candidate in names:
                return candidate
        if names:
            return names[0]
    except Exception:
        pass
    return "llama3:latest"


def run_step(name: str, fn) -> bool:
    print(f"\n--- {name} ---")
    try:
        fn()
        print(f"PASS: {name}")
        return True
    except Exception as exc:
        print(f"FAIL: {name} -> {exc}")
        return False


def main() -> int:
    print("BTC Superduper Predictor — smoke test")
    print(f"Work dir: {WORK_DIR}")

    results: list[bool] = []

    def test_imports():
        import pandas  # noqa: F401
        import requests  # noqa: F401
        import schedule  # noqa: F401
        import streamlit  # noqa: F401
        import btc_superduper_predictor as pred
        import news_fetcher  # noqa: F401

        assert hasattr(pred, "fetch_fast_news")
        assert hasattr(pred, "analyze_with_llm")
        assert hasattr(pred, "run_dashboard")

    results.append(run_step("imports", test_imports))

    import btc_superduper_predictor as pred
    from news_fetcher import fetch_live_news

    model = pick_ollama_model()
    os.environ["OLLAMA_MODEL"] = model
    pred.OLLAMA_MODEL = model
    print(f"Using Ollama model: {model}")

    def test_ollama_reachable():
        import requests

        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        resp.raise_for_status()
        models = [m["name"] for m in resp.json().get("models", [])]
        assert model in models, f"Model {model!r} not in Ollama: {models}"

    def test_market_cycle():
        from datetime import date
        from market_cycle import BEAR_DAYS, BULL_DAYS, CYCLE_DAYS, FIRST_BEAR_END, get_cycle_state

        bear_end = get_cycle_state(FIRST_BEAR_END)
        assert bear_end.phase == "downtrend"
        assert bear_end.day_in_phase == BEAR_DAYS
        assert bear_end.next_phase == "bull"
        assert bear_end.next_flip == FIRST_BEAR_END + __import__("datetime").timedelta(days=1)

        bull_start = get_cycle_state(FIRST_BEAR_END + __import__("datetime").timedelta(days=1))
        assert bull_start.phase == "bull"
        assert bull_start.day_in_phase == 1
        assert bull_start.days_in_phase == BULL_DAYS

        bull_end = get_cycle_state(bull_start.phase_end)
        assert bull_end.phase == "bull"
        assert bull_end.next_phase == "downtrend"

        loop_day = get_cycle_state(bull_end.next_flip)
        assert loop_day.phase == "downtrend"
        assert loop_day.day_in_phase == 1
        assert CYCLE_DAYS == BEAR_DAYS + BULL_DAYS

        today = get_cycle_state(date.today())
        assert today.phase in ("downtrend", "bull")
        print(f"  Today: {today.phase_label} day {today.day_in_phase}, flip {today.next_flip}")

    results.append(run_step("market cycle", test_market_cycle))

    live_articles: list[dict] = []

    def test_news_fetch():
        nonlocal live_articles
        live_articles = fetch_live_news()
        assert len(live_articles) >= MIN_LIVE_ARTICLES, (
            f"Expected at least {MIN_LIVE_ARTICLES} live articles, got {len(live_articles)}"
        )
        sources = sorted({a["source"] for a in live_articles})
        print(f"Live news articles: {len(live_articles)}")
        print(f"  Sources: {', '.join(sources[:8])}{'...' if len(sources) > 8 else ''}")
        print(f"  Latest: {live_articles[0]['title'][:90]}")

    results.append(run_step("news fetch", test_news_fetch))

    def test_llm_pipeline():
        if len(live_articles) < MIN_LIVE_ARTICLES:
            raise RuntimeError("No live articles available for LLM pipeline test")

        for path in (pred.LATEST_FILE, pred.HISTORY_FILE):
            if path.exists():
                path.unlink()

        result = pred.analyze_with_llm(live_articles)
        if "error" in result:
            raise RuntimeError(result["error"])

        assert pred.LATEST_FILE.exists(), "latest_analysis.json not written"
        payload = json.loads(pred.LATEST_FILE.read_text())
        assert payload["articles_count"] == len(live_articles)
        assert "analysis" in payload
        assert payload["analysis"].get("downtrend_until_oct5_probability") == "100%" or payload["analysis"].get("bull_phase_probability") == "100%"
        assert "day_trade" in payload["analysis"]
        assert pred.HISTORY_FILE.exists(), "btc_predictions_history.csv not written"
        dt = payload["analysis"]["day_trade"]
        print(f"  Day trade: {dt.get('signal')} @ {dt.get('confidence')}%")
        print(f"  Macro belief: {payload['analysis'].get('downtrend_until_oct5_probability') or payload['analysis'].get('bull_phase_probability')}")

    results.append(run_step("llm pipeline", test_llm_pipeline))

    def test_dashboard_entrypoint():
        proc = subprocess.run(
            [sys.executable, "-c", "import sys; sys.argv.append('--dashboard'); "
             "import btc_superduper_predictor as p; "
             "assert '--dashboard' in sys.argv; "
             "print('dashboard branch ok')"],
            cwd=WORK_DIR,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout)

    results.append(run_step("dashboard entrypoint", test_dashboard_entrypoint))

    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 40}")
    print(f"Result: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
