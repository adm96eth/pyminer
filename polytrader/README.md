# polytrader

An automated **Polymarket internal-arbitrage** bot. It scans active markets,
detects when a *complete set* of a market's outcome tokens can be bought for
less than its guaranteed $1.00 redemption value, and executes the buy across
every leg — in **paper** (simulated) or **live** (real funds) mode, switchable
with one flag.

> ⚠️ **Read this first.** This places (or simulates) real trades. Internal-set
> arbitrage is the *closest thing* to riskless on Polymarket, but it is **not
> free money**: edges are small and rare, you compete with faster bots, and
> real fills slip vs. the quoted book. The viral "$68 → $1.7M" story that
> prompted this was a copytrade scam — do not expect those returns. Run in
> paper mode until you trust it, then go live with the smallest possible size.

## The edge

For a market whose outcomes are mutually exclusive and exhaustive (binary
YES/NO, or a categorical N-way), exactly one outcome resolves to $1 and the
rest to $0. So **one of every outcome token = a complete set = $1.00**, always.
A complete set can also be *merged* back into $1 of USDC collateral on-chain at
any time, so you don't even need to wait for resolution.

    edge_per_set = $1.00 − (cost to buy one of every outcome) − fees − gas

When that's positive (after costs) and clears your threshold, it's an
opportunity. The bot sizes it by the **thinnest leg's** order-book depth
(you can't hold a partial set) and walks real book depth so a tiny
top-of-book quote doesn't overstate the edge.

## Layout

| Module | Role | Network? |
|--------|------|----------|
| `models.py`   | order books, markets, opportunities | no |
| `strategy.py` | arb detection math (pure, unit-tested) | no |
| `risk.py`     | pre-trade caps + kill switch | no |
| `brokers/paper.py` | simulated fills vs. live book snapshot | no |
| `brokers/live.py`  | real orders via `py-clob-client` | yes |
| `client.py`   | read-only market + book data | yes |
| `engine.py`   | the loop: fetch → detect → risk → execute | yes |
| `journal.py`  | JSONL event log + CSV of trades | no |
| `notify.py`   | console / webhook / Telegram alerts | webhook+TG only |
| `snapshot.py` | serialize book snapshots to JSONL frames | no |
| `backtest.py` | replay snapshots through the real strategy | no |
| `report.py`   | roll a journal up into P&L / activity stats | no |

## Install

```bash
pip install -r polytrader/requirements.txt   # py-clob-client only needed for live
```

## Run (paper)

```bash
python -m polytrader --paper --once -v        # one scan, verbose
python -m polytrader --paper --ticks 100      # 100 loops
python -m polytrader --config polytrader/config.example.json --paper
```

Paper mode starts with `paper_starting_usdc`, simulates fills against the live
order book it just fetched, merges complete sets to realize P&L, and prints the
running balance. No keys, no money.

## Run (live)

Live mode places **real orders with real funds**. It is gated three ways:

1. `--live` selects the live broker.
2. It refuses to start unless these env vars are set (secrets come from the
   environment **only**, never the config file):

   ```bash
   export POLY_PRIVATE_KEY=...        # wallet private key
   export POLY_API_KEY=...            # CLOB API creds
   export POLY_API_SECRET=...
   export POLY_API_PASSPHRASE=...
   export POLY_FUNDER=0x...           # funder/proxy address (optional)
   ```
3. Even then it runs **dry-run** (logs intended orders, sends nothing) until you
   add `--arm`:

   ```bash
   python -m polytrader --live --once -v          # dry-run: logs orders only
   python -m polytrader --live --arm --ticks 50   # ARMED: real orders
   ```

Start armed runs with `max_usdc_per_trade` set to a few dollars and verify the
fills, fees, and on-chain settlement match what the bot reports **before**
increasing size.

## Logging trades (`--journal` / `--csv`)

```bash
python -m polytrader --paper --journal trades.jsonl --csv trades.csv
```

`trades.jsonl` gets one JSON object per event (`opportunity_detected`,
`trade_executed`, `set_merged`, `opportunity_skipped`, `leg_unwound`, `halt`),
flushed per write so a crash still leaves a complete record. `trades.csv` is a
flat sheet of executed/merged trades for spreadsheets. Both files are
gitignored by default.

### P&L / stats rollup (`--report`)

Summarize a journal at any time — during or after a run — with no network:

```bash
python -m polytrader --report trades.jsonl
# === polytrader report (2026-06-01 -> 2026-06-02) ===
# detected=120 executed=42 merged=42 skipped=78 unwound=1 halts=0
# realized PnL: $+18.40  |  volume: $1430.00  |  win rate: 100% (42W/0L)  | ...
# -- by day --   ...   -- top markets by realized PnL --   ...
```

You get totals, win rate, best/worst trade, and per-day + per-market
breakdowns. It tolerates a torn last line (e.g. from a crash mid-write).

## Notifications (`--notify`)

```bash
python -m polytrader --paper --notify              # console only
export POLYTRADER_WEBHOOK_URL=https://hooks.slack.com/...   # Slack/Discord/generic
export TELEGRAM_BOT_TOKEN=123:abc TELEGRAM_CHAT_ID=456      # Telegram bot
python -m polytrader --paper --notify              # fans out to all configured
```

You get a ping on every detected edge and on a kill-switch halt. Notifier
secrets come from the environment only, and a failing notifier is logged but
never stops trading.

## Record + backtest (no network, no risk)

Archive every tick's order books while running, then replay them through the
*exact same* strategy/risk/broker code to tune thresholds:

```bash
python -m polytrader --paper --record books.jsonl     # capture snapshots (paper)
python -m polytrader --live  --record books.jsonl      # also works while live
python -m polytrader --backtest books.jsonl \
       --config polytrader/config.example.json         # replay offline
# -> BACKTEST frames=240 opps=18 trades=12 ... realizedPnL=$4.30 (+0.43%)
```

`--record` runs in the engine loop independent of the broker, so it captures
the same book snapshots whether you're paper or live — let it run during real
trading, then backtest threshold tweaks against exactly what you traded into.

Because it replays your *own* recorded liquidity, backtest P&L is an optimistic
upper bound (real execution competes for that depth) — same caveat as paper.

## Risk controls (`limits` in config)

- `max_usdc_per_trade` / `max_usdc_per_market` / `max_total_exposure` — notional
  caps; an opportunity is auto-down-sized to fit, or rejected if no room.
- `min_edge_per_set` — ignore edges thinner than this (cost noise / one adverse
  tick would erase them).
- `min_set_liquidity` — require at least N sets of depth so you're not chasing
  dust.
- `max_daily_loss` — **kill switch**: realized loss past this halts the engine.

## What paper mode does *not* model

Instant fills against a snapshot (no slippage between quote and fill), no queue
position, no partial-fill latency, no maker rebates, and it assumes you merge
sets immediately. Live results will be worse. Treat paper P&L as an upper bound.

## Tests

```bash
pip install pytest
python -m pytest tests/test_polytrader_*.py -q
```

The strategy, paper broker, risk manager, and a full engine pass run entirely
offline (fake data source) — no network or keys required.
