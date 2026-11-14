"""True Probability Engine.

An Oracle is any data source that returns a probability (0..1) for the event,
plus a confidence weight. The engine combines several oracles and refuses to
answer when they disagree too much.

Math (weighted average):
    p_true = sum(w_i * p_i) / sum(w_i)
    Example: oracle A says 0.70 with weight 2, oracle B says 0.60 with weight 1
             -> (2*0.70 + 1*0.60) / 3 = 0.6667

Math (disagreement check):
    We compute the weighted standard deviation of the oracle probabilities:
        var = sum(w_i * (p_i - p_true)^2) / sum(w_i)
        sd  = sqrt(var)
    If sd > max_std_dev, the sources do not agree, so the engine returns NO TRADE.
    This is a simple confidence band: about 95% of outcomes fall within
    p_true +/- 2*sd if the sources are roughly normal. Here it is used as a gate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class OracleReading:
    """One source's view of the event."""

    name: str
    probability: float   # 0..1
    weight: float        # confidence weight, >= 0


class Oracle(Protocol):
    """Anything that can produce a reading. Implement one per data source."""

    name: str

    def read(self, market_id: str) -> OracleReading | None: ...


@dataclass(frozen=True)
class TrueProbability:
    """Result of the engine. probability is None when the engine says NO TRADE."""

    probability: float | None
    std_dev: float
    readings: tuple[OracleReading, ...]
    reason: str


class TrueProbabilityEngine:
    """Combines oracles into one true probability and gates on their disagreement."""

    def __init__(self, oracles: Sequence[Oracle], max_std_dev: float = 0.10, min_sources: int = 2) -> None:
        if max_std_dev < 0 or min_sources < 1:
            raise ValueError("max_std_dev must be >= 0 and min_sources >= 1")
        self.oracles = list(oracles)
        self.max_std_dev = max_std_dev
        self.min_sources = min_sources

    def estimate(self, market_id: str) -> TrueProbability:
        readings: list[OracleReading] = []
        for oracle in self.oracles:
            reading = oracle.read(market_id)  # a source may be down; None means no data
            if reading is None:
                continue
            if not (0.0 <= reading.probability <= 1.0) or reading.weight < 0:
                raise ValueError(f"oracle {oracle.name} gave an invalid reading: {reading}")
            readings.append(reading)

        if len(readings) < self.min_sources:
            return TrueProbability(None, math.nan, tuple(readings), f"NO TRADE: {len(readings)} source(s), need {self.min_sources}")

        total_weight = sum(r.weight for r in readings)
        if total_weight == 0:
            return TrueProbability(None, math.nan, tuple(readings), "NO TRADE: all weights are zero")

        p_true = sum(r.weight * r.probability for r in readings) / total_weight
        variance = sum(r.weight * (r.probability - p_true) ** 2 for r in readings) / total_weight
        std_dev = math.sqrt(variance)

        if std_dev > self.max_std_dev:
            return TrueProbability(None, std_dev, tuple(readings), f"NO TRADE: sources disagree (sd {std_dev:.3f} > {self.max_std_dev:.3f})")
        return TrueProbability(p_true, std_dev, tuple(readings), "ok")
