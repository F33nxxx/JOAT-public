"""Market Probability Module.

Reads Yes/No share prices from a prediction market and turns them into
probabilities. Endpoints and keys are placeholders: fill them in for a real venue.

Math:
    A Yes share pays 1 if the event happens, 0 if not. So a Yes price of 0.65
    means the market thinks there is a 65% chance. Real books are not perfect:
    Yes + No can add up to more than 1 (the "overround", the venue's built-in margin).
    We remove it by normalising:
        p_yes = yes_price / (yes_price + no_price)
    Example: yes = 0.66, no = 0.38 -> sum 1.04 -> p_yes = 0.66 / 1.04 = 0.635.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class OrderBookLevel:
    """One price level in the book: a price (0..1) and the size available there (shares)."""

    price: float
    size: float


@dataclass(frozen=True)
class MarketSnapshot:
    """Prices and depth for one market at one moment."""

    market_id: str
    yes_price: float
    no_price: float
    market_probability: float          # normalised p_yes, 0..1
    yes_asks: tuple[OrderBookLevel, ...] = field(default_factory=tuple)


class MarketTransport(Protocol):
    """What the market module needs from a venue client. Implement this per venue."""

    def get_prices(self, market_id: str) -> tuple[float, float]: ...
    def get_order_book(self, market_id: str, side: str) -> list[dict[str, Any]]: ...


class PlaceholderTransport:
    """Stand-in transport. Replace with a real HTTP client for your venue.

    The URL and key are placeholders so nothing reaches a real endpoint by accident.
    """

    def __init__(self, base_url: str = "https://api.example-market.invalid", api_key: str = "REPLACE_ME") -> None:
        self.base_url = base_url
        self.api_key = api_key

    def get_prices(self, market_id: str) -> tuple[float, float]:
        raise NotImplementedError("Wire this to the venue's price endpoint.")

    def get_order_book(self, market_id: str, side: str) -> list[dict[str, Any]]:
        raise NotImplementedError("Wire this to the venue's order book endpoint.")


class MarketProbabilityModule:
    """Fetches prices and depth, and converts them to a market probability."""

    def __init__(self, transport: MarketTransport) -> None:
        self.transport = transport

    @staticmethod
    def to_probability(yes_price: float, no_price: float) -> float:
        """Convert Yes/No prices to a normalised Yes probability (0..1).

        Raises ValueError if the prices cannot describe a market.
        """
        if not (0.0 < yes_price < 1.0 and 0.0 < no_price < 1.0):
            raise ValueError(f"prices must be strictly between 0 and 1: yes={yes_price}, no={no_price}")
        return yes_price / (yes_price + no_price)

    def snapshot(self, market_id: str) -> MarketSnapshot:
        yes_price, no_price = self.transport.get_prices(market_id)
        asks = tuple(
            OrderBookLevel(price=float(level["price"]), size=float(level["size"]))
            for level in self.transport.get_order_book(market_id, side="yes_ask")
        )
        return MarketSnapshot(
            market_id=market_id,
            yes_price=yes_price,
            no_price=no_price,
            market_probability=self.to_probability(yes_price, no_price),
            yes_asks=asks,
        )


def available_depth(asks: tuple[OrderBookLevel, ...], max_price: float, stake_shares: float) -> float:
    """Shares you can buy at or below max_price, up to stake_shares.

    Used as the liquidity check: if this is smaller than the stake, the trade is not viable.
    """
    filled = 0.0
    for level in sorted(asks, key=lambda lvl: lvl.price):
        if level.price > max_price:
            break
        filled += level.size
        if filled >= stake_shares:
            return stake_shares
    return min(filled, stake_shares)
