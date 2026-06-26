# June Backtest Notes (2026-06-01 → 2026-06-26)

Generated: 2026-06-26T12:29:11.644863+00:00

## Summary
- **Baseline win rate:** 73.7%
- **Walk-forward adaptive win rate:** 72.2%
- Trades: 19 · Flat days: 7
- Wins/Losses: 14/5

## Loss patterns

- **2026-06-04** SHORT (with_macro_and_subtrend) MAX_HOLD -0.02% · news -25.04 · poll 8 · 1h ↓ (-0.65%) · 4h ↓ (-1.16%) · 24h ↑ (+0.46%)
- **2026-06-07** SHORT (with_macro_and_subtrend) STOP_LOSS -1.17% · news -1.0 · poll 16 · 1h ↑ (+0.31%) · 4h ↓ (-0.85%) · 24h ↑ (+2.15%)
- **2026-06-10** SHORT (macro_correction) STOP_LOSS -0.87% · news -8.12 · poll 8 · 1h ↑ (+0.36%) · 4h → (+0.22%) · 24h ↓ (-0.38%)
- **2026-06-17** SHORT (with_macro_and_subtrend) STOP_LOSS -0.74% · news -13.8 · poll 8 · 1h → (-0.12%) · 4h ↓ (-0.49%) · 24h ↓ (-0.26%)
- **2026-06-19** SHORT (macro_correction) STOP_LOSS -0.73% · news -3.56 · poll 12 · 1h → (+0.17%) · 4h → (+0.01%) · 24h ↓ (-0.58%)

## What worked

- **2026-06-01** SHORT (with_macro_and_subtrend) +1.87% · news -10.24
- **2026-06-03** SHORT (macro_correction) +3.27% · news -16.8
- **2026-06-05** SHORT (macro_correction) +2.23% · news -18.36
- **2026-06-06** SHORT (macro_correction) +0.48% · news -12.24
- **2026-06-09** SHORT (macro_correction) +3.20% · news -15.8
- **2026-06-13** LONG (with_subtrend) +0.70% · news 2.12
- **2026-06-14** SHORT (macro_correction) +0.66% · news -2.56
- **2026-06-15** LONG (with_subtrend) +0.12% · news 3.68
- **2026-06-20** LONG (with_subtrend) +0.20% · news 1.12
- **2026-06-21** SHORT (macro_correction) +1.36% · news -3.56
- **2026-06-22** LONG (with_subtrend) +0.33% · news 4.68
- **2026-06-23** SHORT (with_macro_and_subtrend) +0.66% · news -8.68
- **2026-06-24** SHORT (macro_correction) +2.72% · news -5.56
- **2026-06-26** SHORT (macro_correction) +1.43% · news -5.56

## Recommendations

- SHORT macro_correction fades need |news_score| >= 3 when 24h is rallying.
- Avoid entries after 14:00 UTC — 1 losses from afternoon polls.
- Best wins: 2026-06-03 SHORT macro_correction (+3.27%), 2026-06-09 SHORT macro_correction (+3.20%), 2026-06-24 SHORT macro_correction (+2.72%), 2026-06-05 SHORT macro_correction (+2.23%), 2026-06-01 SHORT with_macro_and_subtrend (+1.87%)

## Self-improvement loop

1. Each loss appends to `data/trade_learning.json` with pattern signatures.
2. Walk-forward mode applies **learned instincts only to future days** (no peeking).
3. Live pipeline: call `trade_learning.apply_learned_rules()` after `signal_engine`.
4. Review instincts weekly; promote repeated patterns into `signal_engine.py` filters.
