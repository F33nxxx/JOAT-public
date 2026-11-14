"""Execution Module.

Compares market and true probability, decides whether to trade, sizes the stake,
and places a limit order.

Math (edge): edge = p_true - p_market. This is the gap in probability points.
    Example: p_true 0.75, p_market 0.60 -> edge 0.15 (15 points).

Math (Kelly criterion for buying a binary share):
    A Yes share costs price c (0..1). If the event happens it pays 1, so the
    profit per dollar staked is b = (1 - c) / c. If it does not happen you lose the stake.
    With true probability q and loss probability (1 - q), the Kelly fraction of
    the bankroll to bet is:
        f* = (b*q - (1 - q)) / b
    Since b = (1-c)/c, this simplifies to:
        f* = (q - c) / (1 - c)
    Example: q = 0.75, c = 0.60 -> f* = 0.15 / 0.40 = 0.375 (37.5% of bankroll).
    Full Kelly is very aggressive, because any error in q is costly. So we use
    fractional Kelly (e.g. 0.25 x f*) and cap the result at max_fraction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .market import MarketProbabilityModule, MarketSnapshot, available_depth
from .oracle import TrueProbability, TrueProbabilityEngine

log = logging.getLogger("munin.execution")


def kelly_fraction(true_p: float, price: float) -> float:
    """Full Kelly fraction for buying a Yes share at `price` with true probability `true_p`.

    Returns 0 when there is no edge. Never negative: a negative value means "do not buy".
    """
    if not (0.0 < price < 1.0):
        raise ValueError(f"price must be between 0 and 1, got {price}")
    return max(0.0, (true_p - price) / (1.0 - price))


@dataclass(frozen=True)
class TradeConfig:
    """Settings. Change these, not the code."""

    edge_threshold: float = 0.10       # trade only if p_true - p_market >= this (10 points)
    kelly_multiplier: float = 0.25     # fractional Kelly
    max_fraction: float = 0.05         # never risk more than 5% of bankroll on one trade
    min_stake: float = 1.0             # below this, skip (in bankroll units)
    max_price: float = 0.95            # do not pay more than this for a Yes share
    dry_run: bool = True               # True: log the order, do not send it


@dataclass(frozen=True)
class Decision:
    """What the controller decided and why."""

    action: str                        # "BUY_YES" or "NO_TRADE"
    reason: str
    edge: float | None = None
    stake_fraction: float = 0.0
    stake_amount: float = 0.0
    limit_price: float | None = None


class OrderTransport(Protocol):
    """Sends orders to a venue. Implement per venue."""

    def place_limit_order(self, market_id: str, side: str, price: float, size: float) -> dict[str, Any]: ...


class ExecutionController:
    """Compares the two probabilities and places a limit order when the trigger fires."""

    def __init__(
        self,
        market: MarketProbabilityModule,
        truth: TrueProbabilityEngine,
        orders: OrderTransport,
        config: TradeConfig | None = None,
    ) -> None:
        self.market = market
        self.truth = truth
        self.orders = orders
        self.config = config or TradeConfig()

    def decide(self, snapshot: MarketSnapshot, true: TrueProbability, bankroll: float) -> Decision:
        """Pure decision logic. No network calls, so it is easy to test."""
        if true.probability is None:
            return Decision("NO_TRADE", true.reason)

        cfg = self.config
        edge = true.probability - snapshot.market_probability
        if edge < cfg.edge_threshold:
            return Decision("NO_TRADE", f"edge {edge:.3f} below threshold {cfg.edge_threshold:.3f}", edge=edge)

        limit_price = snapshot.yes_price
        if limit_price > cfg.max_price:
            return Decision("NO_TRADE", f"price {limit_price:.3f} above max {cfg.max_price:.3f}", edge=edge)

        fraction = min(cfg.kelly_multiplier * kelly_fraction(true.probability, limit_price), cfg.max_fraction)
        amount = fraction * bankroll
        if amount < cfg.min_stake:
            return Decision("NO_TRADE", f"stake {amount:.2f} below minimum {cfg.min_stake:.2f}", edge=edge)

        return Decision("BUY_YES", "edge above threshold", edge=edge, stake_fraction=fraction,
                        stake_amount=amount, limit_price=limit_price)

    def run_once(self, market_id: str, bankroll: float) -> Decision:
        snapshot = self.market.snapshot(market_id)
        true = self.truth.estimate(market_id)
        decision = self.decide(snapshot, true, bankroll)
        if decision.action == "NO_TRADE":
            log.info("%s: no trade (%s)", market_id, decision.reason)
            return decision

        size = decision.stake_amount / decision.limit_price  # shares = dollars / price
        viable = available_depth(snapshot.yes_asks, decision.limit_price, size)
        if viable < size:
            return Decision("NO_TRADE", f"depth {viable:.1f} < needed {size:.1f} shares", edge=decision.edge)

        self.send_order(market_id, decision.limit_price, size)
        return decision

    def send_order(self, market_id: str, price: float, size: float) -> dict[str, Any] | None:
        """Place a Yes limit order. In dry_run mode it only logs."""
        if self.config.dry_run:
            log.info("DRY RUN: would buy %.2f Yes shares of %s at limit %.3f", size, market_id, price)
            return None
        return self.orders.place_limit_order(market_id, side="yes", price=price, size=size)
