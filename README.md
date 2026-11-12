# ODIN

A small Python engine that looks for an edge in yes/no prediction markets.

It compares a market's price with your own probability estimate, decides whether the gap is
big enough to trade, and sizes the stake with a fractional Kelly formula.

**Simulation only. Not financial advice.** Orders are only logged (`dry_run=True`) and the
market client is a placeholder that connects to nothing. Prediction markets are high risk.

## Parts

- `odin/market.py`: turns Yes/No prices into a probability and checks order book depth.
- `odin/oracle.py`: combines several data sources into one probability, and refuses to
  answer when the sources disagree too much.
- `odin/execution.py`: checks the edge against the price paid, sizes the stake, and places
  a limit order (logged only, by default).
- `odin/main.py`: a demo loop with fake prices and fake sources.

## Run

Python 3.10 or newer. No dependencies.

```
python3 -m odin.main                 # demo loop
python3 -m unittest odin.test_odin   # tests
```
