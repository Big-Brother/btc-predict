"""Perpetual macro cycle: 364-day bear → 1064-day bull → repeat. Fixed 100% belief."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

BEAR_DAYS = 364
BULL_DAYS = 1064
CYCLE_DAYS = BEAR_DAYS + BULL_DAYS
MACRO_BELIEF_PCT = "100%"

FIRST_BEAR_END = date(2026, 10, 5)
CYCLE_EPOCH = FIRST_BEAR_END - timedelta(days=BEAR_DAYS - 1)

Phase = Literal["downtrend", "bull"]


@dataclass(frozen=True)
class CycleState:
    phase: Phase
    cycle_number: int
    day_in_phase: int
    days_in_phase: int
    days_remaining: int
    phase_start: date
    phase_end: date
    next_phase: Phase
    next_flip: date

    @property
    def phase_label(self) -> str:
        return "Downtrend" if self.phase == "downtrend" else "Bull Trend"

    @property
    def macro_belief_field(self) -> str:
        return (
            "downtrend_until_oct5_probability"
            if self.phase == "downtrend"
            else "bull_phase_probability"
        )

    @property
    def macro_belief_label(self) -> str:
        if self.phase == "downtrend":
            return f"Downtrend until {self.phase_end:%b %d, %Y}"
        return f"Bull trend until {self.phase_end:%b %d, %Y}"

    def fixed_macro_belief(self) -> dict[str, str]:
        """User's non-negotiable 100% macro conviction — never LLM-estimated."""
        return {self.macro_belief_field: MACRO_BELIEF_PCT}

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "phase_label": self.phase_label,
            "cycle_number": self.cycle_number,
            "day_in_phase": self.day_in_phase,
            "days_in_phase": self.days_in_phase,
            "days_remaining": self.days_remaining,
            "phase_start": self.phase_start.isoformat(),
            "phase_end": self.phase_end.isoformat(),
            "next_phase": self.next_phase,
            "next_flip": self.next_flip.isoformat(),
            "macro_belief_pct": MACRO_BELIEF_PCT,
            **self.fixed_macro_belief(),
        }


def _as_date(value: date | datetime | None = None) -> date:
    if value is None:
        return datetime.now(timezone.utc).date()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date()
    return value


def get_cycle_state(as_of: date | datetime | None = None) -> CycleState:
    as_of = _as_date(as_of)
    days_since_epoch = (as_of - CYCLE_EPOCH).days

    position = days_since_epoch % CYCLE_DAYS
    cycle_number = (days_since_epoch - position) // CYCLE_DAYS

    if position < BEAR_DAYS:
        phase: Phase = "downtrend"
        day_in_phase = position + 1
        days_in_phase = BEAR_DAYS
        days_remaining = BEAR_DAYS - day_in_phase
        phase_start = CYCLE_EPOCH + timedelta(days=cycle_number * CYCLE_DAYS)
        phase_end = phase_start + timedelta(days=BEAR_DAYS - 1)
        next_phase: Phase = "bull"
        next_flip = phase_end + timedelta(days=1)
    else:
        phase = "bull"
        bull_offset = position - BEAR_DAYS
        day_in_phase = bull_offset + 1
        days_in_phase = BULL_DAYS
        days_remaining = BULL_DAYS - day_in_phase
        phase_start = CYCLE_EPOCH + timedelta(days=cycle_number * CYCLE_DAYS + BEAR_DAYS)
        phase_end = phase_start + timedelta(days=BULL_DAYS - 1)
        next_phase = "downtrend"
        next_flip = phase_end + timedelta(days=1)

    display_cycle = cycle_number + 1 if cycle_number >= 0 else cycle_number

    return CycleState(
        phase=phase,
        cycle_number=display_cycle,
        day_in_phase=day_in_phase,
        days_in_phase=days_in_phase,
        days_remaining=days_remaining,
        phase_start=phase_start,
        phase_end=phase_end,
        next_phase=next_phase,
        next_flip=next_flip,
    )


def build_macro_context(cycle: CycleState) -> str:
    """Structural macro belief — NOT every candle is red/green."""
    if cycle.phase == "downtrend":
        return f"""=== STRUCTURAL MACRO (100% BELIEF — BACKGROUND ONLY) ===
Bitcoin is in a **364-day overall downtrend** ending **{cycle.phase_end:%B %d, %Y}**
(day {cycle.day_in_phase}/{cycle.days_in_phase}). `downtrend_until_oct5_probability` = **100%**.

CRITICAL: This is the **big-picture southward bias**, NOT a rule that every minute, hour, day,
week, or month must be a red candle. Real charts have **ups and downs inside the macro move** —
bounces, squeezes, relief rallies, and local bull legs are normal and tradable.

Your job is to read **today's news + active sub-trends** and trade the **current swing**:
- In macro bear: SHORT pullbacks AND **LONG bounces** when news/price support it
- Prefer trades **with the active sub-trend** (1h / 4h / 24h); counter-trend scalps are fine when clear

Do NOT output macro probability. It is fixed at 100%. Do NOT refuse LONG signals solely because macro is bear."""

    return f"""=== STRUCTURAL MACRO (100% BELIEF — BACKGROUND ONLY) ===
Bitcoin is in a **1064-day overall bull trend** ending **{cycle.phase_end:%B %d, %Y}**
(day {cycle.day_in_phase}/{cycle.days_in_phase}). `bull_phase_probability` = **100%**.

CRITICAL: Macro bull does **not** mean every candle is green. Pullbacks, dumps, and local bear
legs happen constantly — trade them.

Your job is to read **today's news + active sub-trends** and trade the **current swing**:
- In macro bull: LONG dips AND **SHORT overheated spikes** when news/price support it
- Prefer trades **with the active sub-trend** (1h / 4h / 24h)

Do NOT output macro probability. It is fixed at 100%. Do NOT refuse SHORT signals solely because macro is bull."""


def build_day_trade_schema() -> str:
    return """{
  "timestamp": "YYYY-MM-DD HH:MM",
  "day_trade_signal": "LONG | SHORT | FLAT",
  "day_trade_confidence": 0-100,
  "day_trade_timeframe": "5m | 15m | 1h | 4h | 1D — hold window for THIS swing only",
  "day_trade_rationale": "Why THIS direction for the active sub-trend from news + price",
  "dominant_subtrend": "up | down | sideways — which sub-trend (1h/4h/24h) you are trading with",
  "trade_style": "with_subtrend | with_macro_and_subtrend | counter_trend_scalp | macro_pullback",
  "key_drivers": ["top 3-5 news items driving THIS trade"],
  "risk_level": "Low/Medium/High",
  "news_sentiment": "bearish | bullish | neutral"
}"""
