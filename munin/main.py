"""Demo loop: fetch market price -> true probability -> compare edge -> stake -> execute.

Runs in dry-run mode with fake data. Replace the stubs with real transports and oracles.
Run from the repo root:  python -m munin.main
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .execution import ExecutionController, TradeConfig
from .market import MarketProbabilityModule, PlaceholderTransport
from .oracle import OracleReading, TrueProbabilityEngine


class FakeMarketTransport(PlaceholderTransport):
    """Demo prices: Yes 0.60, No 0.42 -> market probability ~0.588."""

    def get_prices(self, market_id: str) -> tuple[float, float]:
        return 0.60, 0.42

    def get_order_book(self, market_id: str, side: str) -> list[dict[str, Any]]:
        return [{"price": 0.60, "size": 500}, {"price": 0.62, "size": 500}]


class FakeOracle:
    """Demo data source with a fixed opinion."""

    def __init__(self, name: str, probability: float, weight: float) -> None:
        self.name = name
        self._reading = OracleReading(name, probability, weight)

    def read(self, market_id: str) -> OracleReading | None:
        return self._reading


class LoggingOrderTransport:
    """Stand-in order sender. Replace with the venue's authenticated order call."""

    def place_limit_order(self, market_id: str, side: str, price: float, size: float) -> dict[str, Any]:
        logging.info("order placed: %s %s %.2f @ %.3f", market_id, side, size, price)
        return {"status": "simulated"}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    market = MarketProbabilityModule(FakeMarketTransport())
    truth = TrueProbabilityEngine(
        [FakeOracle("poll_avg", 0.75, 2.0), FakeOracle("model", 0.70, 1.0)],
        max_std_dev=0.10,
    )
    controller = ExecutionController(market, truth, LoggingOrderTransport(), TradeConfig(dry_run=True))

    bankroll = 1000.0
    for _ in range(3):  # three demo ticks; in production this loops forever
        decision = controller.run_once("DEMO-MARKET", bankroll)
        logging.info("decision: %s | %s | edge=%s stake=%.2f",
                     decision.action, decision.reason, decision.edge, decision.stake_amount)
        time.sleep(0.1)


if __name__ == "__main__":
    main()
