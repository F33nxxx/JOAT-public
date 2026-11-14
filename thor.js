// Thor: the paper-trading engine for solana-bot: event decoding, entry filters, exit rules and risk limits.
// No keys, RPC or network here. All values are simulated SOL.
const crypto = require('crypto');

const CONFIG = {
    // Entry filters (all must pass)
    minAgeSec: 60,             // skip minute zero
    maxWatchSec: 300,          // stop watching a token after this long
    minProgressPct: 5,
    maxProgressPct: 60,
    minUniqueBuyers: 12,
    minBuyVolumeSol: 1.5,      // excluding the creator
    maxTop3SharePct: 35,       // of tokens bought
    maxEarlySharePct: 30,      // of tokens bought in the first earlyWindowSec
    earlyWindowSec: 3,
    minVirtualSol: 25,         // rejects curves not quoted in SOL
    maxCreatorLaunches: 2,     // per session
    creatorDumpPct: 50,        // creator sold this share of what they bought
    trendWindowSec: 60,        // holders and curve SOL must not fall over this window

    // USD screens: off until reviewed. Need a SOL/USD price from price.js
    usdScreens: false,
    minHoldersUsd: 200,        // holders with a positive balance
    mcapMinUsd: 50000,
    mcapMaxUsd: 500000,
    minLiquidityUsd: 10000,    // curve SOL reserves x SOL price

    // Sizing and limits
    bankrollSol: 1,
    tradeSizeSol: 0.05,
    maxConcurrent: 3,
    lossCooldownSec: 60,
    streakLosses: 3,
    streakCooldownSec: 600,

    // Exits
    stopLossPct: 35,
    takeProfitMultiple: 2,
    takeProfitFraction: 0.5,
    trailingStopPct: 25,
    staleExitSec: 300,
    staleMinMultiple: 1.1,
    maxHoldSec: 1800,

    // Paper fill assumptions
    entryLatencyMs: 1500,
    entryTimeoutMs: 20000,
    feePct: 1.25,
    slippagePct: 3,
    tipSol: 0.005,             // charged on every buy and sell
};

const eventDiscriminator = (name) =>
    crypto.createHash('sha256').update(`event:${name}`).digest().subarray(0, 8).toString('hex');
const TRADE_EVENT = eventDiscriminator('TradeEvent');
const CREATE_EVENT = eventDiscriminator('CreateEvent');
const DATA_PREFIX = 'Program data: ';

function decodeTrade(b) {
    let o = 8;
    const mint = b.subarray(o, o + 32).toString('hex'); o += 32;
    const sol = Number(b.readBigUInt64LE(o)) / 1e9; o += 8;
    const tokens = Number(b.readBigUInt64LE(o)); o += 8;
    const isBuy = b[o] === 1; o += 1;
    const user = b.subarray(o, o + 32).toString('hex'); o += 32;
    o += 8; // timestamp
    const virtualSol = Number(b.readBigUInt64LE(o)) / 1e9; o += 8;
    const virtualTokens = Number(b.readBigUInt64LE(o));
    return { type: 'trade', mint, sol, tokens, isBuy, user, virtualSol, virtualTokens };
}

function decodeCreate(b) {
    let o = 8;
    const text = () => {
        const n = b.readUInt32LE(o); o += 4;
        const v = b.subarray(o, o + n).toString('utf8'); o += n;
        return v;
    };
    const name = text(), symbol = text(), uri = text();
    const mint = b.subarray(o, o + 32).toString('hex');
    o += 32 + 32 + 32; // mint, bonding curve, user
    const creator = b.subarray(o, o + 32).toString('hex');
    return { type: 'create', mint, creator, name, symbol, uri };
}

function decodeEvents(logLines) {
    const events = [];
    for (const line of logLines) {
        if (!line.startsWith(DATA_PREFIX)) continue;
        const b = Buffer.from(line.slice(DATA_PREFIX.length), 'base64');
        const d = b.subarray(0, 8).toString('hex');
        try {
            if (d === TRADE_EVENT) events.push(decodeTrade(b));
            else if (d === CREATE_EVENT) events.push(decodeCreate(b));
        } catch { /* truncated or older layout */ }
    }
    return events;
}

const curveProgress = (virtualTokens) =>
    Math.min(((1073000000e6 - virtualTokens) * 100) / 793100000e6, 100);

// Pump.fun supply is 1e9 whole tokens; the curve quotes SOL per whole token
const curveMarketCapSol = (res) => (res.virtualSol / (res.virtualTokens / 1e6)) * 1e9;

// Constant-product fills, after fees and assumed slippage
function quoteBuy(sol, res, cfg) {
    const net = sol * (1 - cfg.feePct / 100);
    const tokens = (res.virtualTokens * net) / (res.virtualSol + net);
    return tokens * (1 - cfg.slippagePct / 100);
}
function quoteSell(tokens, res, cfg) {
    const sol = (res.virtualSol * tokens) / (res.virtualTokens + tokens);
    return sol * (1 - cfg.feePct / 100) * (1 - cfg.slippagePct / 100);
}

function newToken(event, ageStartSec) {
    return {
        ...event, startSec: ageStartSec, status: 'watching', reasons: ['no trades seen'],
        buyers: new Map(), boughtTokens: 0, earlyTokens: 0, buyVolumeSol: 0,
        creatorBought: 0, creatorSold: 0, reserves: null,
        balances: new Map(), history: [],
    };
}

const holderCount = (token) => [...token.balances.values()].filter((v) => v > 0).length;

// Latest snapshot at or before sec, else the earliest one
function snapshotAt(token, sec) {
    let snap = token.history[0];
    for (const h of token.history) {
        if (h.t > sec) break;
        snap = h;
    }
    return snap;
}

function recordTrade(token, t, nowSec, cfg) {
    token.reserves = { virtualSol: t.virtualSol, virtualTokens: t.virtualTokens };
    if (t.user !== token.creator) {
        const held = (token.balances.get(t.user) || 0) + (t.isBuy ? t.tokens : -t.tokens);
        token.balances.set(t.user, Math.max(held, 0));
    }
    token.history.push({ t: nowSec, holders: holderCount(token), virtualSol: t.virtualSol });
    if (t.user === token.creator) {
        if (t.isBuy) token.creatorBought += t.tokens;
        else token.creatorSold += t.tokens;
        return;
    }
    if (!t.isBuy) return;
    token.buyers.set(t.user, (token.buyers.get(t.user) || 0) + t.tokens);
    token.boughtTokens += t.tokens;
    token.buyVolumeSol += t.sol;
    if (nowSec - token.startSec <= cfg.earlyWindowSec) token.earlyTokens += t.tokens;
}

const creatorDumped = (token, cfg) =>
    token.creatorBought > 0 && (token.creatorSold / token.creatorBought) * 100 >= cfg.creatorDumpPct;

// Rejections no later trade can undo
function hardReject(token, creatorLaunches, blacklist, cfg) {
    if (blacklist.has(token.creatorAddress)) return 'creator blacklisted';
    if (creatorLaunches > cfg.maxCreatorLaunches) return 'serial launcher';
    if (!token.name.trim() || !token.symbol.trim() || !token.uri.trim()) return 'missing metadata';
    if (creatorDumped(token, cfg)) return 'creator dumped';
    return null;
}

// Default deny: returns every failed condition, passes only on an empty list
function entryFailures(token, nowSec, cfg, solUsd = null) {
    const failed = [];
    const age = nowSec - token.startSec;
    if (age < cfg.minAgeSec) failed.push(`age ${age.toFixed(0)}s`);
    if (!token.reserves) return [...failed, 'no trades seen'];

    const progress = curveProgress(token.reserves.virtualTokens);
    if (token.reserves.virtualSol < cfg.minVirtualSol) failed.push('not a SOL curve');
    if (progress < cfg.minProgressPct) failed.push(`progress ${progress.toFixed(1)}% too low`);
    if (progress > cfg.maxProgressPct) failed.push(`progress ${progress.toFixed(1)}% too high`);
    if (token.buyers.size < cfg.minUniqueBuyers) failed.push(`${token.buyers.size} buyers`);
    if (token.buyVolumeSol < cfg.minBuyVolumeSol) failed.push(`buy volume ${token.buyVolumeSol.toFixed(2)} SOL`);

    if (token.boughtTokens > 0) {
        const top3 = [...token.buyers.values()].sort((a, b) => b - a).slice(0, 3).reduce((s, v) => s + v, 0);
        const top3Pct = (top3 / token.boughtTokens) * 100;
        const earlyPct = (token.earlyTokens / token.boughtTokens) * 100;
        if (top3Pct > cfg.maxTop3SharePct) failed.push(`top 3 hold ${top3Pct.toFixed(0)}%`);
        if (earlyPct > cfg.maxEarlySharePct) failed.push(`early buys ${earlyPct.toFixed(0)}%`);
    }

    // Distribution and pulled liquidity, compared over the trend window
    const now = token.history[token.history.length - 1];
    const then = snapshotAt(token, nowSec - cfg.trendWindowSec);
    if (now.holders < then.holders) failed.push(`holders fell ${then.holders} to ${now.holders}`);
    if (now.virtualSol < then.virtualSol) failed.push(`curve SOL fell ${then.virtualSol.toFixed(2)} to ${now.virtualSol.toFixed(2)}`);

    if (cfg.usdScreens) {
        if (!solUsd) {
            failed.push('SOL price unknown');
        } else {
            const mcapUsd = curveMarketCapSol(token.reserves) * solUsd;
            const liqUsd = token.reserves.virtualSol * solUsd;
            if (now.holders < cfg.minHoldersUsd) failed.push(`${now.holders} holders, need ${cfg.minHoldersUsd}`);
            if (mcapUsd < cfg.mcapMinUsd || mcapUsd > cfg.mcapMaxUsd) failed.push(`market cap $${Math.round(mcapUsd)}`);
            if (liqUsd < cfg.minLiquidityUsd) failed.push(`liquidity $${Math.round(liqUsd)}`);
        }
    }
    return failed;
}

// Next exit for a position, or null to hold
function exitDecision(pos, nowSec, cfg) {
    const value = quoteSell(pos.remaining, pos.reserves, cfg);
    const multiple = value / (pos.sizeSol * (pos.remaining / pos.tokens));
    pos.peak = Math.max(pos.peak, multiple);
    pos.multiple = multiple;
    const age = nowSec - pos.openedSec;

    if (curveProgress(pos.reserves.virtualTokens) >= 100) return { fraction: 1, reason: 'curve complete' };
    const floor = pos.tookProfit ? 1 : 1 - cfg.stopLossPct / 100;
    if (multiple <= floor) return { fraction: 1, reason: pos.tookProfit ? 'breakeven stop' : 'stop loss' };
    if (pos.tookProfit && multiple <= pos.peak * (1 - cfg.trailingStopPct / 100)) {
        return { fraction: 1, reason: 'trailing stop' };
    }
    if (!pos.tookProfit && multiple >= cfg.takeProfitMultiple) {
        return { fraction: cfg.takeProfitFraction, reason: 'take profit' };
    }
    if (age >= cfg.maxHoldSec) return { fraction: 1, reason: 'max hold' };
    if (age >= cfg.staleExitSec && multiple < cfg.staleMinMultiple) return { fraction: 1, reason: 'stale' };
    return null;
}

const utcDay = (ms) => new Date(ms).toISOString().slice(0, 10);
const newState = () => ({ day: null, dayPnl: 0, totalPnl: 0, consecutiveLosses: 0, cooldownUntil: 0, closed: 0, wins: 0 });

class PaperEngine {
    constructor({ cfg = CONFIG, state = newState(), blacklist = new Set(), log, persist = () => {}, toAddress = (h) => h, now = Date.now, intel = null }) {
        Object.assign(this, { cfg, state, blacklist, log, persist, toAddress, now, intel });
        this.tokens = new Map();
        this.positions = new Map();
        this.pending = new Map();
        this.launches = new Map();
        this.halted = null;
        this.solUsd = null;
    }

    nowSec() { return this.now() / 1000; }

    rollDay() {
        const day = utcDay(this.now());
        if (this.state.day === day) return;
        if (this.state.day) this.log({ event: 'new_day', previous: this.state.day, dayPnl: this.state.dayPnl });
        Object.assign(this.state, { day, dayPnl: 0 });
    }

    // Why no new position may open, or null
    blockReason() {
        const { cfg, state } = this;
        this.rollDay();
        if (this.halted) return this.halted;
        const marketBlock = this.intel?.marketBlock();
        if (marketBlock) return marketBlock;
        if (this.now() < state.cooldownUntil) return 'cooldown';
        if (this.positions.size + this.pending.size >= cfg.maxConcurrent) return 'max concurrent';
        if (cfg.bankrollSol + state.totalPnl < cfg.tradeSizeSol + 2 * cfg.tipSol) return 'bankroll exhausted';
        return null;
    }

    onCreate(e) {
        if (this.tokens.has(e.mint)) return;
        const launches = (this.launches.get(e.creator) || 0) + 1;
        this.launches.set(e.creator, launches);
        const token = newToken(e, this.nowSec());
        token.mintAddress = this.toAddress(e.mint);
        token.creatorAddress = this.toAddress(e.creator);
        this.tokens.set(e.mint, token);
        const reason = hardReject(token, launches, this.blacklist, this.cfg);
        if (reason) this.reject(token, [reason]);
    }

    reject(token, reasons) {
        token.status = 'rejected';
        this.log({ event: 'reject', mint: token.mintAddress, symbol: token.symbol, reasons });
    }

    onTrade(t) {
        const token = this.tokens.get(t.mint);
        if (!token) return;
        const nowSec = this.nowSec();
        recordTrade(token, t, nowSec, this.cfg);

        const dumped = creatorDumped(token, this.cfg);
        if (dumped && !this.blacklist.has(token.creatorAddress)) {
            this.blacklist.add(token.creatorAddress);
            this.log({ event: 'blacklist', creator: token.creatorAddress, reason: 'creator dumped' });
            this.persist();
        }
        if (dumped && token.status === 'watching') this.reject(token, ['creator dumped']);

        const pos = this.positions.get(t.mint);
        if (pos) {
            pos.reserves = token.reserves;
            if (dumped) this.sell(pos, 1, 'creator dumped');
            else this.checkExit(pos);
            return;
        }
        if (this.pending.has(t.mint)) {
            this.tryFill(token);
            return;
        }
        if (token.status !== 'watching') return;
        const failed = entryFailures(token, nowSec, this.cfg, this.solUsd);
        token.reasons = failed;
        if (failed.length) return;

        const veto = this.intel?.veto(token.mintAddress);
        if (veto) {
            this.reject(token, [veto]);
            return;
        }

        const blocked = this.blockReason();
        if (blocked) {
            token.reasons = [`passed filters, blocked: ${blocked}`];
            return;
        }
        token.status = 'entering';
        this.pending.set(t.mint, this.now());
        this.log({
            event: 'signal', mint: token.mintAddress, symbol: token.symbol,
            progressPct: +curveProgress(token.reserves.virtualTokens).toFixed(2),
            buyers: token.buyers.size, holders: holderCount(token), buyVolumeSol: +token.buyVolumeSol.toFixed(3),
            solUsd: this.solUsd,
            intel: this.intel?.tags(token),
        });
    }

    // Fills on the first trade after the assumed latency, at that trade's reserves
    tryFill(token) {
        const { cfg } = this;
        const startedAt = this.pending.get(token.mint);
        if (this.now() - startedAt < cfg.entryLatencyMs) return;
        this.pending.delete(token.mint);
        if (creatorDumped(token, cfg)) return;
        const tokens = quoteBuy(cfg.tradeSizeSol, token.reserves, cfg);
        const pos = {
            mint: token.mint, mintAddress: token.mintAddress, symbol: token.symbol,
            sizeSol: cfg.tradeSizeSol, cost: cfg.tradeSizeSol + cfg.tipSol, proceeds: 0,
            tokens, remaining: tokens, reserves: token.reserves, peak: 1, multiple: 1,
            tookProfit: false, openedSec: this.nowSec(),
        };
        this.positions.set(token.mint, pos);
        token.status = 'entered';
        this.log({ event: 'buy', mint: pos.mintAddress, symbol: pos.symbol, sol: pos.sizeSol, tipSol: cfg.tipSol, tokens });
    }

    checkExit(pos) {
        const decision = exitDecision(pos, this.nowSec(), this.cfg);
        if (!decision) return;
        this.sell(pos, decision.fraction, decision.reason);
        if (decision.reason === 'take profit') pos.tookProfit = true;
    }

    sell(pos, fraction, reason) {
        const { cfg, state } = this;
        const tokens = fraction >= 1 ? pos.remaining : pos.remaining * fraction;
        const sol = quoteSell(tokens, pos.reserves, cfg);
        pos.remaining -= tokens;
        pos.proceeds += sol - cfg.tipSol;
        this.log({ event: 'sell', mint: pos.mintAddress, symbol: pos.symbol, reason, sol: +sol.toFixed(6), tipSol: cfg.tipSol, multiple: +pos.multiple.toFixed(3) });
        if (pos.remaining > 0) return;

        this.positions.delete(pos.mint);
        const pnl = pos.proceeds - pos.cost;
        this.rollDay();
        state.dayPnl += pnl;
        state.totalPnl += pnl;
        state.closed += 1;
        if (pnl > 0) {
            state.wins += 1;
            state.consecutiveLosses = 0;
        } else {
            state.consecutiveLosses += 1;
            const streak = state.consecutiveLosses >= cfg.streakLosses;
            state.cooldownUntil = this.now() + (streak ? cfg.streakCooldownSec : cfg.lossCooldownSec) * 1000;
        }
        this.log({ event: 'close', mint: pos.mintAddress, symbol: pos.symbol, reason, pnlSol: +pnl.toFixed(6), dayPnl: +state.dayPnl.toFixed(6), totalPnl: +state.totalPnl.toFixed(6) });
        this.persist();
    }

    // Runs on a timer, so exits fire even when a token stops trading
    tick() {
        const nowSec = this.nowSec();
        this.rollDay();
        for (const pos of [...this.positions.values()]) this.checkExit(pos);
        for (const [mint, startedAt] of [...this.pending]) {
            if (this.now() - startedAt < this.cfg.entryTimeoutMs) continue;
            this.pending.delete(mint);
            const token = this.tokens.get(mint);
            token.status = 'rejected';
            this.log({ event: 'entry_timeout', mint: token.mintAddress, symbol: token.symbol });
        }
        for (const [mint, token] of this.tokens) {
            if (nowSec - token.startSec < this.cfg.maxWatchSec) continue;
            if (this.positions.has(mint) || this.pending.has(mint)) continue;
            if (token.status === 'watching') this.reject(token, token.reasons);
            this.tokens.delete(mint);
        }
    }

    // Emergency flatten: sells everything and blocks new entries
    flatten(reason) {
        this.halted = reason;
        this.pending.clear();
        this.log({ event: 'flatten', reason, open: this.positions.size });
        for (const pos of [...this.positions.values()]) {
            exitDecision(pos, this.nowSec(), this.cfg);
            this.sell(pos, 1, reason);
        }
    }

    summary() {
        const { state } = this;
        return { watching: this.tokens.size, open: this.positions.size, closed: state.closed, wins: state.wins, dayPnl: +state.dayPnl.toFixed(4), totalPnl: +state.totalPnl.toFixed(4), blocked: this.blockReason() };
    }
}

module.exports = { CONFIG, TRADE_EVENT, CREATE_EVENT, PaperEngine, decodeEvents, curveProgress, curveMarketCapSol, quoteBuy, quoteSell, entryFailures, exitDecision, newState };
