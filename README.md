# JOAT: Jack of All Trades

## The mission

I'm building what I want to be the most effective automated trading solution in the world:
one app that hunts for an edge across very different markets, starting with prediction
markets (Kalshi) and Solana meme coins. It sizes every bet with discipline and never puts
money at risk unless the person using it has explicitly turned that on.

This repo is the starting point. It holds the first, bare-bones versions of the two engines
at the core of JOAT, as they were first written: before tests, fixes or tuning. They are
here so anyone can see where it started, take it apart and help make it better.

## All are welcome to join me | collaborate

Traders, quants, developers, designers, testers, skeptics: if you want to build this with me,
you're welcome. Open an issue with an idea, a question or a bug you spotted, or send a pull
request. Pointing out what's wrong is as useful as adding something new.

## The engines

Prediction markets run on Odin's two ravens, Munin and Hugin. Meme coins run on Thor.

- **Thor** (`thor.js`): the Solana meme-coin engine. It decodes pump.fun launch and trade
  events, screens each new token (age, bonding-curve progress, unique buyers, volume,
  whale and early-sniper share, creator behaviour), and manages each paper position with
  stop loss, trailing stop, staleness and max-hold exits, plus cooldowns after a loss and
  after a losing streak.
  No keys, no RPC, no network: every trade is simulated.
- **Munin** (`munin/`): the edge finder. It turns a market's Yes/No prices into a probability,
  combines several data sources into its own estimate, refuses to trade when the sources
  disagree, sizes the stake with fractional Kelly, and checks order book depth before placing
  a limit order.
- **Hugin** (`hugin.py`): the second check. It takes an order exactly as written and re-does
  the money math (edge after fees, Kelly size, caps), listing every problem before anything
  is sent.

## Run

Munin and Hugin: Python 3.10 or newer. Thor: Node.js 18 or newer. No dependencies.

```
python3 -m munin.main     # Munin demo loop with fake prices and fake sources
```

Hugin is a library: call `hugin.verify_order(order, judged, hugin.Config())`.
Thor is a library: `const { PaperEngine, decodeEvents, CONFIG, newState } = require('./thor')`.
Feed it decoded pump.fun events; it has no live feed of its own.

## Read this first

**Simulation only. Not financial advice.** Munin only logs orders (`dry_run=True`), and its
market client is a placeholder that connects to nothing. These are untested first drafts with
known flaws, published as a starting point, not a finished product. Prediction markets and
meme coins are high risk; you can lose everything you put in.
