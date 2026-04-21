# FGI Crypto Trading Bot

A systematic SOL trading bot that uses the **Fear & Greed Index** as a contrarian signal, combined with z-score normalization, Bayesian confidence tracking, and z-score-scaled position sizing. Runs on Robinhood Crypto via their Ed25519-authenticated API.

## How it works

Every hour the bot:

1. **Fetches the current Fear & Greed Index** (Alternative.me → CoinMarketCap → CoinGecko fallback)
2. **Computes a z-score** of today's FGI against a rolling 55-day history — how extreme is the current sentiment relative to recent baseline?
3. **Applies a Bayesian confidence filter** — a Beta-Binomial updater that learns from the bot's own trade outcomes and adjusts signal confidence over time
4. **Scales the buy size with fear intensity** — the more extreme the fear, the larger the position. Mild fear buys 24% of available cash; peak fear buys up to 74%
5. **Sells a fixed 65%** of SOL holdings on greed signals, letting the rest compound

```
FGI z-score < -1.95  →  BUY  (scaled by |z|, from 24% → 74% of cash)
FGI z-score > +2.65  →  SELL (65% of holdings)
confidence < 0.53    →  HOLD
```

## Strategy performance

Backtested on SOL-USD with 200-simulation Monte Carlo per period, $100 starting capital:

| Period | SOL Buy & Hold | VOO (S&P 500) | Symmetric | Asymmetric | **Z-Scaled** |
|--------|---------------|---------------|-----------|------------|-------------|
| 1 year | -19.8%        | +37.0%        | +6.3%     | +16.0%     | +12.5%      |
| 2 year | +12.1%        | +41.6%        | +67.7%    | +66.8%     | **+73.2%**  |
| 3 year | +171.5%       | +80.6%        | +172.0%   | +199.3%    | **+208.3%** |
| 5 year | +46.3%        | +85.3%        | +202.1%   | +222.4%    | **+309.9%** |

The z-scaled approach wins 3 of 4 periods. The 5-year edge is the most meaningful — multiple deep fear cycles where the bot loaded up heavily then held through the recovery.

## Position sizing

Three sizing modes were tested before settling on z-scaled:

- **Symmetric** — fixed % of cash on buys, same % of holdings on sells
- **Asymmetric** — buy% and sell% vary independently
- **Z-Scaled** *(deployed)* — buy fraction ramps with fear intensity, sell fraction is fixed

The intuition: when FGI hits extreme fear (z = -3.5), that's exactly when you want maximum exposure. Buying the same amount at z = -2.0 and z = -4.0 leaves money on the table.

## Setup

### Prerequisites

- Python 3.12+
- A [Robinhood Crypto developer account](https://robinhood.com/crypto/developer) with API key + Ed25519 keypair
- (Optional) CoinMarketCap free API key for FGI

### Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env.local
# Fill in ROBINHOOD_API_KEY and ROBINHOOD_PRIVATE_KEY
# Run python public_key.py to generate a keypair if needed
```

### Run

```bash
# Dry run (default) — simulates trades without executing
python main.py

# Check account status and recent trades
python main.py --status

# Run Monte Carlo backtest
python main.py --backtest

# Five-way strategy comparison (Symmetric / Asymmetric / Z-Scaled / SOL B&H / VOO B&H)
python run_scaled.py
```

Set `DRY_RUN=false` in `.env.local` when ready to trade live.

## Deploy (free)

Runs on [Vercel](https://vercel.com) free tier. The trading cycle is a serverless function at `/api/cycle` triggered hourly by Vercel Cron. Bayesian state persists in Vercel KV (free Upstash Redis).

```bash
npm i -g vercel
vercel login
vercel --prod
```

Then add a **KV Database** in the Vercel dashboard (Storage tab) and set your environment variables. See [DEPLOY.md](DEPLOY.md) for the full walkthrough.

## Project structure

```
├── main.py           # CLI entry point (--backtest, --status, live loop)
├── trader.py         # Hourly trading loop
├── robinhood.py      # Robinhood Crypto API client (Ed25519 auth)
├── fgi.py            # Fear & Greed Index fetcher (multi-source)
├── signals.py        # Z-score + Bayesian signal engine
├── database.py       # SQLite persistence (trades, FGI cache, Bayesian state)
├── notifications.py  # Discord webhook alerts
├── backtest.py       # Backtesting engine + Monte Carlo (symmetric / asymmetric / z-scaled)
├── run_scaled.py     # Five-way comparison script
├── public_key.py     # Ed25519 keypair generator
├── Dockerfile
└── fly.toml
```

## Key parameters (optimised via Monte Carlo)

| Parameter | Value | Description |
|-----------|-------|-------------|
| `BUY_Z_THRESHOLD` | -1.95 | Minimum z-score to trigger a buy |
| `SELL_Z_THRESHOLD` | 2.65 | Minimum z-score to trigger a sell |
| `MIN_BUY_PCT` | 24% | Buy fraction at threshold z-score |
| `MAX_BUY_PCT` | 74% | Buy fraction at peak fear |
| `MAX_SCALE_Z` | 4.0 | Z-score where max buy kicks in |
| `SELL_PCT` | 65% | Fraction of holdings sold per signal |
| `MIN_CONFIDENCE` | 0.53 | Bayesian confidence floor |
| `FGI_HISTORY_DAYS` | 55 | Rolling baseline window |

Parameters were averaged across the 3 periods where z-scaled sizing outperformed (2, 3, and 5 years).

## Disclaimer

This is an experimental project built for learning purposes. Past backtest performance does not guarantee future results. Crypto is volatile. Don't trade money you can't afford to lose.
