"""
Walk-forward learning from real trade outcomes — no retroactive cheating.

After each loss, records a pattern signature. Learned rules apply only to FUTURE days.
Rules tighten entry when the same context repeatedly lost.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORK_DIR = Path(__file__).resolve().parent
LEARNING_FILE = WORK_DIR / "data" / "trade_learning.json"


@dataclass
class TradeRecord:
    date: str
    signal: str
    outcome: str
    pnl_pct: float
    macro_phase: str
    trade_style: str
    news_score: float
    confidence: int
    poll_hour: int | None
    subtrends: dict[str, Any]
    reject_reason: str | None = None
    day_direction: str | None = None

    @property
    def win(self) -> bool:
        return self.pnl_pct > 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LearnedInstinct:
    """Atomic rule learned from one or more losses."""

    id: str
    description: str
    loss_count: int = 1
    win_count_when_bypassed: int = 0
    action: str = "flat"  # flat | require_higher_score
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LearningState:
    version: int = 1
    trade_history: list[dict] = field(default_factory=list)
    instincts: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def save(self, path: Path = LEARNING_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path = LEARNING_FILE) -> LearningState:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(
            version=data.get("version", 1),
            trade_history=data.get("trade_history", []),
            instincts=data.get("instincts", []),
            stats=data.get("stats", {}),
        )


def _t24_up(sub: dict) -> bool:
    return sub.get("24h") == "up" or float(sub.get("pct_24h") or 0) > 0.15


def _t4_up(sub: dict) -> bool:
    return sub.get("4h") == "up"


def derive_instincts_from_loss(rec: TradeRecord) -> list[LearnedInstinct]:
    """Propose instincts from a single losing trade."""
    out: list[LearnedInstinct] = []
    sub = rec.subtrends or {}

    if rec.macro_phase == "downtrend" and rec.signal == "LONG":
        if rec.trade_style in ("with_subtrend", "counter_trend_scalp") and not _t24_up(sub):
            out.append(
                LearnedInstinct(
                    id="bear_long_need_24h_up",
                    description="Macro bear LONG lost without 24h uptrend — require 24h up",
                    action="flat",
                )
            )
        if rec.trade_style == "with_subtrend" and _t4_up(sub) and not _t24_up(sub):
            out.append(
                LearnedInstinct(
                    id="bear_long_4h_bounce_trap",
                    description="LONG on 4h bounce in bear while 24h flat/down — fade trap",
                    action="flat",
                )
            )
        if abs(rec.news_score) >= 8 and rec.trade_style == "with_subtrend":
            out.append(
                LearnedInstinct(
                    id="bear_long_extreme_news_skeptic",
                    description="Very bullish news score in bear still lost — cap LONG unless 24h up strongly",
                    action="flat",
                )
            )

    if rec.macro_phase == "downtrend" and rec.signal == "SHORT" and rec.trade_style == "macro_correction":
        if _t4_up(sub) and _t24_up(sub) and float(sub.get("pct_24h") or 0) > 1.0:
            out.append(
                LearnedInstinct(
                    id="bear_short_vs_strong_rally",
                    description="SHORT fade lost against 24h rally >1% — need stronger bear news",
                    action="flat",
                )
            )

    if rec.outcome == "STOP_LOSS" and rec.poll_hour and rec.poll_hour >= 12:
        out.append(
            LearnedInstinct(
                id="late_poll_stop_loss",
                description=f"Stop loss on {rec.poll_hour}:00 UTC entry — prefer morning polls",
                action="flat",
            )
        )

    return out


def merge_instinct(state: LearningState, instinct: LearnedInstinct) -> None:
    for existing in state.instincts:
        if existing.get("id") == instinct.id:
            existing["loss_count"] = existing.get("loss_count", 1) + 1
            return
    state.instincts.append(instinct.to_dict())


def apply_learned_rules(
    signal: str,
    *,
    macro_phase: str,
    trade_style: str,
    news_score: float,
    confidence: int,
    subtrends: dict[str, Any],
    poll_hour: int | None,
    instincts: list[dict],
) -> tuple[str, str | None]:
    """Apply walk-forward learned instincts. Returns (signal, reject_reason)."""
    if signal == "FLAT" or not instincts:
        return signal, None

    sub = subtrends or {}
    t24_up = _t24_up(sub)
    t4_up = _t4_up(sub)
    pct_24 = float(sub.get("pct_24h") or 0)
    active_ids = {i["id"] for i in instincts if i.get("loss_count", 0) >= 1}

    if "bear_long_need_24h_up" in active_ids:
        if macro_phase == "downtrend" and signal == "LONG":
            if not t24_up and float(sub.get("pct_24h") or 0) < 0.35:
                return "FLAT", "learned:bear_long_need_24h_up"

    if "bear_long_4h_bounce_trap" in active_ids:
        if macro_phase == "downtrend" and signal == "LONG" and t4_up and not t24_up:
            return "FLAT", "learned:bear_long_4h_bounce_trap"

    if "bear_long_extreme_news_skeptic" in active_ids:
        if macro_phase == "downtrend" and signal == "LONG" and abs(news_score) >= 8 and not t24_up:
            return "FLAT", "learned:bear_long_extreme_news_skeptic"

    if "bear_short_vs_strong_rally" in active_ids:
        if (
            macro_phase == "downtrend"
            and signal == "SHORT"
            and t4_up
            and t24_up
            and pct_24 > 1.0
            and abs(news_score) < 3.0
        ):
            return "FLAT", "learned:bear_short_vs_strong_rally"

    if "late_poll_stop_loss" in active_ids:
        # Only block afternoon LONG in bear (most late stops were LONG)
        if poll_hour and poll_hour >= 14 and macro_phase == "downtrend" and signal == "LONG":
            return "FLAT", "learned:late_poll_stop_loss"

    return signal, None


def ingest_outcome(state: LearningState, rec: TradeRecord) -> list[str]:
    """Add trade to history; on loss, derive and merge instincts. Returns new instinct ids."""
    state.trade_history.append(rec.to_dict())
    new_ids: list[str] = []
    if not rec.win and rec.signal in ("LONG", "SHORT"):
        for inst in derive_instincts_from_loss(rec):
            merge_instinct(state, inst)
            new_ids.append(inst.id)
    return new_ids


def pattern_report(records: list[TradeRecord]) -> dict[str, Any]:
    """Aggregate patterns for improvement notes."""
    taken = [r for r in records if r.signal in ("LONG", "SHORT")]
    wins = [r for r in taken if r.win]
    losses = [r for r in taken if not r.win]
    flats = [r for r in records if r.signal == "FLAT"]

    def bucket(items, key_fn):
        from collections import Counter

        c = Counter(key_fn(x) for x in items)
        return dict(c.most_common())

    loss_notes = []
    for r in losses:
        loss_notes.append(
            {
                "date": r.date,
                "signal": r.signal,
                "style": r.trade_style,
                "news_score": r.news_score,
                "outcome": r.outcome,
                "pnl_pct": r.pnl_pct,
                "poll_hour": r.poll_hour,
                "subtrends_summary": r.subtrends.get("summary") if r.subtrends else None,
                "macro": r.macro_phase,
            }
        )

    win_notes = [
        {
            "date": r.date,
            "signal": r.signal,
            "style": r.trade_style,
            "news_score": r.news_score,
            "pnl_pct": r.pnl_pct,
        }
        for r in wins
    ]

    recommendations = []
    if losses:
        long_bear_losses = [r for r in losses if r.macro_phase == "downtrend" and r.signal == "LONG"]
        if len(long_bear_losses) >= 2:
            recommendations.append(
                "Block or heavily filter LONG in macro bear unless 24h trend is up — "
                f"{len(long_bear_losses)} June losses from bear LONGs."
            )
        short_fade_losses = [
            r for r in losses if r.trade_style == "macro_correction" and r.signal == "SHORT"
        ]
        if short_fade_losses:
            recommendations.append(
                "SHORT macro_correction fades need |news_score| >= 3 when 24h is rallying."
            )
        late_losses = [r for r in losses if r.poll_hour and r.poll_hour >= 14]
        if late_losses:
            recommendations.append(
                f"Avoid entries after 14:00 UTC — {len(late_losses)} losses from afternoon polls."
            )

    if wins:
        best = sorted(wins, key=lambda x: x.pnl_pct, reverse=True)[:5]
        recommendations.append(
            "Best wins: "
            + ", ".join(f"{w.date} {w.signal} {w.trade_style} ({w.pnl_pct:+.2f}%)" for w in best)
        )

    return {
        "days_total": len(records),
        "trades_taken": len(taken),
        "flat_days": len(flats),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(taken), 3) if taken else 0,
        "by_style_win": bucket(wins, lambda r: r.trade_style),
        "by_style_loss": bucket(losses, lambda r: r.trade_style),
        "by_signal_win": bucket(wins, lambda r: r.signal),
        "by_signal_loss": bucket(losses, lambda r: r.signal),
        "by_outcome": bucket(taken, lambda r: r.outcome),
        "loss_details": loss_notes,
        "win_details": win_notes,
        "recommendations": recommendations,
    }
