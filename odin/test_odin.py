"""Tests for the decision logic. Run with: python -m unittest odin.test_odin"""

from __future__ import annotations

import math
import unittest

from .execution import ExecutionController, TradeConfig, kelly_fraction
from .market import MarketProbabilityModule, OrderBookLevel, available_depth
from .oracle import OracleReading, TrueProbabilityEngine


class FixedOracle:
    def __init__(self, name: str, p: float, w: float) -> None:
        self.name, self._r = name, OracleReading(name, p, w)

    def read(self, market_id: str) -> OracleReading | None:
        return self._r


class NoDataOracle:
    name = "down"

    def read(self, market_id: str) -> OracleReading | None:
        return None


class Transport:
    def __init__(self, yes: float, no: float, asks: list[tuple[float, float]]) -> None:
        self.yes, self.no = yes, no
        self.asks = [{"price": p, "size": s} for p, s in asks]
        self.orders: list[tuple[str, float, float]] = []

    def get_prices(self, market_id: str) -> tuple[float, float]:
        return self.yes, self.no

    def get_order_book(self, market_id: str, side: str) -> list[dict]:
        return self.asks

    def place_limit_order(self, market_id: str, side: str, price: float, size: float) -> dict:
        self.orders.append((market_id, price, size))
        return {}


class ProbabilityConversionTests(unittest.TestCase):
    def test_normalises_overround(self) -> None:
        self.assertAlmostEqual(MarketProbabilityModule.to_probability(0.66, 0.38), 0.66 / 1.04)

    def test_rejects_price_outside_open_interval(self) -> None:
        with self.assertRaises(ValueError):
            MarketProbabilityModule.to_probability(0.0, 0.5)


class KellyTests(unittest.TestCase):
    def test_matches_worked_example(self) -> None:
        self.assertAlmostEqual(kelly_fraction(0.75, 0.60), 0.375)

    def test_no_edge_is_zero_not_negative(self) -> None:
        self.assertEqual(kelly_fraction(0.40, 0.60), 0.0)


class TrueProbabilityTests(unittest.TestCase):
    def test_weighted_average(self) -> None:
        eng = TrueProbabilityEngine([FixedOracle("a", 0.70, 2), FixedOracle("b", 0.60, 1)], max_std_dev=1)
        self.assertAlmostEqual(eng.estimate("m").probability, 2 / 3 * 0.70 + 1 / 3 * 0.60)

    def test_disagreement_gives_no_trade(self) -> None:
        eng = TrueProbabilityEngine([FixedOracle("a", 0.10, 1), FixedOracle("b", 0.90, 1)], max_std_dev=0.1)
        self.assertIsNone(eng.estimate("m").probability)

    def test_missing_source_is_skipped_but_quorum_enforced(self) -> None:
        eng = TrueProbabilityEngine([FixedOracle("a", 0.5, 1), NoDataOracle()], min_sources=2)
        self.assertIsNone(eng.estimate("m").probability)


    def test_zero_weight_source_does_not_count_toward_quorum(self) -> None:
        eng = TrueProbabilityEngine([FixedOracle("a", 0.9, 1), FixedOracle("b", 0.1, 0)], max_std_dev=1)
        self.assertIsNone(eng.estimate("m").probability)

    def test_non_finite_weight_is_rejected(self) -> None:
        eng = TrueProbabilityEngine([FixedOracle("a", 0.8, math.nan), FixedOracle("b", 0.8, 1)])
        with self.assertRaises(ValueError):
            eng.estimate("m")


class DepthTests(unittest.TestCase):
    def test_stops_at_max_price(self) -> None:
        asks = (OrderBookLevel(0.60, 10), OrderBookLevel(0.90, 100))
        self.assertEqual(available_depth(asks, max_price=0.65, stake_shares=50), 10)


class ControllerTests(unittest.TestCase):
    def _controller(self, transport: Transport, oracles: list) -> ExecutionController:
        market = MarketProbabilityModule(transport)
        return ExecutionController(market, TrueProbabilityEngine(oracles, max_std_dev=1), transport,
                                   TradeConfig(dry_run=False, max_fraction=1.0))

    def test_below_threshold_does_not_trade(self) -> None:
        t = Transport(0.60, 0.42, [(0.60, 1000)])
        ctl = self._controller(t, [FixedOracle("a", 0.65, 1), FixedOracle("b", 0.65, 1)])
        self.assertEqual(ctl.run_once("m", 1000).action, "NO_TRADE")
        self.assertEqual(t.orders, [])

    def test_trade_sends_one_limit_order_at_quoted_price(self) -> None:
        t = Transport(0.50, 0.50, [(0.50, 1000)])
        ctl = self._controller(t, [FixedOracle("a", 0.80, 1), FixedOracle("b", 0.80, 1)])
        d = ctl.run_once("m", 1000)
        self.assertEqual(d.action, "BUY_YES")
        self.assertEqual(len(t.orders), 1)
        self.assertEqual(t.orders[0][1], 0.50)

    def test_edge_is_measured_against_price_paid_not_normalised_probability(self) -> None:
        t = Transport(0.60, 0.46, [(0.60, 1000)])  # normalised 0.566, so 0.68 would look like 0.114 of edge
        ctl = self._controller(t, [FixedOracle("a", 0.68, 1), FixedOracle("b", 0.68, 1)])
        self.assertEqual(ctl.run_once("m", 1000).action, "NO_TRADE")

    def test_same_market_is_not_bought_twice(self) -> None:
        t = Transport(0.50, 0.50, [(0.50, 1000)])
        ctl = self._controller(t, [FixedOracle("a", 0.80, 1), FixedOracle("b", 0.80, 1)])
        ctl.run_once("m", 1000)
        self.assertEqual(ctl.run_once("m", 1000).action, "NO_TRADE")
        self.assertEqual(len(t.orders), 1)

    def test_bad_quote_is_no_trade_not_a_crash(self) -> None:
        t = Transport(1.0, 0.0, [])  # resolved market
        ctl = self._controller(t, [FixedOracle("a", 0.80, 1), FixedOracle("b", 0.80, 1)])
        self.assertEqual(ctl.run_once("m", 1000).action, "NO_TRADE")

    def test_dry_run_never_calls_transport(self) -> None:
        t = Transport(0.50, 0.50, [(0.50, 1000)])
        ctl = ExecutionController(MarketProbabilityModule(t),
                                  TrueProbabilityEngine([FixedOracle("a", 0.8, 1), FixedOracle("b", 0.8, 1)], max_std_dev=1),
                                  t, TradeConfig(dry_run=True))
        ctl.run_once("m", 1000)
        self.assertEqual(t.orders, [])


if __name__ == "__main__":
    unittest.main()
