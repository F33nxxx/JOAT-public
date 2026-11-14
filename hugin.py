"""
hugin.py: the second check on an order the AI wants to place.

The judge decides on a candidate at the market's ask. The AI then writes the actual order,
and nothing forces that order to match what was judged: it can name a higher price, more
contracts, or a market that was never judged. Hugin re-does the money math on the order
exactly as written, using the judge's own fee and Kelly functions, and lists what is wrong.

It does not block. The person can still ask for a specific trade; the problems are shown on
the order card and in the send confirmation so the choice is informed.

Stripped version: the few fee and Kelly helpers it needs from the judge are copied in below,
so this file runs on its own. The judge itself is not part of this repo.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    min_net_edge: float = 0.05           # 5 percentage points after fees
    kelly_fraction: float = 0.25         # quarter Kelly: estimates are noisy
    max_market_fraction: float = 0.05    # of bankroll at risk in one market
    max_portfolio_fraction: float = 0.25 # of bankroll at risk across open positions
    max_order_cost: float = 25.0         # dollars


# The judge's verdicts on these two are made at the ask, so Hugin recomputes them instead.
RECOMPUTED = ("9 net edge", "10 position sizes")


def taker_fee_per_contract(price: float) -> float:
    """Kalshi taker fee for ONE contract: ceil(0.07 * P * (1 - P)) in cents. Worst case per contract."""
    return math.ceil(round(0.07 * price * (1.0 - price) * 100, 9)) / 100.0


def all_in_cost(price: float) -> float:
    return price + taker_fee_per_contract(price)


def net_edge(p_win: float, price: float) -> float:
    return p_win - all_in_cost(price)


def kelly_fraction(p_win: float, price: float, fraction: float) -> float:
    """Fractional Kelly for a binary contract paying $1. Zero if there is no positive edge."""
    cost = all_in_cost(price)
    if cost >= 1.0:
        return 0.0
    return max(0.0, (p_win - cost) / (1.0 - cost)) * fraction


def size_contracts(p_win: float, price: float, bankroll: float, open_exposure: float, cfg: Config) -> int:
    """Contracts to buy: fractional Kelly dollars, capped by every limit, rounded down."""
    cost = all_in_cost(price)
    if cost <= 0 or bankroll <= 0:
        return 0
    dollars = min(
        bankroll * kelly_fraction(p_win, price, cfg.kelly_fraction),
        bankroll * cfg.max_market_fraction,
        cfg.max_order_cost,
        bankroll * cfg.max_portfolio_fraction - open_exposure,
    )
    if dollars <= 0:
        return 0
    return int(math.floor(dollars / cost + 1e-9))


def verify_order(order, judged, cfg):
    """Return a list of problems with a prepared order. Empty means none found.

    order:  {"ticker", "side", "order_action", "count", "price" (cents)}
    judged: {(TICKER, side): (candidate, decision, bankroll)} where candidate has .p_win
            and decision has .failed (names of the judge's criteria that failed)
    """
    if order["order_action"] != "buy":
        return []  # a sell closes a position; the entry criteria do not apply
    entry = judged.get((order["ticker"].upper(), order["side"]))
    if entry is None:
        return ["not judged: this order never went through the 10 criteria"]
    candidate, decision, bankroll = entry

    # Edge and size are recomputed at the order's own price, so the judge's verdict on
    # those two (made at the ask) is replaced, not repeated.
    problems = [f"judge failed: {name}" for name in decision.failed if not name.startswith(RECOMPUTED)]
    price = order["price"] / 100.0
    edge = net_edge(candidate.p_win, price)
    if edge < cfg.min_net_edge - 1e-9:
        problems.append(f"edge at {order['price']}c is {edge * 100:.1f} points after fees, "
                        f"below the {cfg.min_net_edge * 100:.0f}-point minimum")
    allowed = size_contracts(candidate.p_win, price, bankroll, 0.0, cfg)
    if order["count"] > allowed:
        problems.append(f"{order['count']} contracts is above the {allowed} that Kelly and the caps allow")
    return problems
