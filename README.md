# SOL Perp Paper Trading — GRACE Strategy

A live paper-trading dashboard for SOL perpetual futures on Coinbase, running the **GRACE** strategy (Gated Regime-Aligned Cross Entry). Tracks a virtual $1,000 portfolio with real market data, 5× leverage, and honest fees.

**Live dashboard:** deployed on Vercel · state persists in Upstash KV · cycles every minute via cron

---

## Strategy: GRACE

GRACE is an event-driven EMA crossover system with four confirmation gates. A signal fires only at the bar where the EMA(9)/EMA(21) cross *first occurs* — not while sitting in an already-crossed state — which prevents fee death spirals on choppy markets.

### Signal formula

$$
S(t) = \text{sgn}(\Delta(t))
\cdot \mathbf{1}\!\left[\Delta(t)\cdot\Delta(t-1) < 0\right]
\cdot \mathbf{1}\!\left[\text{ADX}_{14}(t) \geq 18\right]
\cdot \mathbf{1}\!\left[\Delta(t)\cdot\bigl(e_{21}(t) - e_{55}(t)\bigr) > 0\right]
\cdot \mathbf{1}\!\left[\Delta(t)\cdot\bigl(c_{6h}(t) - e_{6h,21}(t)\bigr) > 0\right]
$$

where

$$\Delta(t) = \text{EMA}(9,t) - \text{EMA}(21,t)$$

- $S(t) \in \{-1,\, 0,\, +1\}$; entry executes at $\text{open}(t+1)$
- **Gate 1** — cross event: signal fires only on the bar of the EMA(9)/EMA(21) sign change
- **Gate 2** — momentum filter: $\text{ADX}_{14} \geq 18$ (trend must have strength)
- **Gate 3** — trend alignment: cross direction must match $e_{21}$ vs $e_{55}$ relationship
- **Gate 4** — macro regime: 30m cross direction must match 6h close vs 6h EMA(21)

### Position sizing and exits

$$
\text{SL} = \text{open}(t{+}1) - S(t)\cdot 1.5\cdot\text{ATR}_{14}(t)
$$
$$
\text{TP} = \text{open}(t{+}1) + S(t)\cdot 4.0\cdot\text{ATR}_{14}(t)
$$

- Leverage: **5×** via SOL perpetual futures (`SLP-20DEC30-CDE`)
- Risk/reward: **1 : 2.67** (SL = 1.5 ATR, TP = 4.0 ATR)
- Position size: contracts = floor(portfolio × MARGIN\_FRACTION / (contract\_size × price))

### Backtest results (90-day SOL data)

| Metric | Value |
|--------|-------|
| Total return | **+26.5%** |
| Sharpe ratio | **19.54** |
| Max drawdown | 8.0% |
| Win rate | 45.2% |
| Trades | 31 |
| Fees included | yes (taker + NFA) |

---

## Architecture

```
cron-job.org (every 1 min)
    └─► GET /api/mock_cycle  (Bearer CRON_SECRET)
            ├─ fast path  (every call)  : fetch live price → SL/TP check
            └─ slow path  (new 30m bar) : fetch 150 candles → GRACE signal → open/flip position

Upstash KV  ──────  mock_state  (portfolio, position, trades, equity curve)
                          │
                    GET /api/mock_status  (public)
                          │
                    React dashboard  (polls every 60s, instant refresh button)
```

### Key files

| Path | Purpose |
|------|---------|
| `core/mock_trader.py` | GRACE strategy engine, `run_cycle()` entry point |
| `core/micro_backtest.py` | Vectorised backtest, indicator math (EMA, ATR, ADX) |
| `core/coinbase.py` | Coinbase Advanced Trade API client (ES256 JWT auth) |
| `storage/mock_store.py` | KV-first state persistence (Upstash Redis + local JSON fallback) |
| `api/mock_cycle.py` | Cron endpoint — runs one trading cycle |
| `api/mock_status.py` | Public endpoint — enriched state for the dashboard |
| `src/` | React + TypeScript dashboard (Vite, Chart.js) |

---

## Setup

### Prerequisites

- Python 3.12+
- Node.js 18+ (for the dashboard)
- [Coinbase Advanced Trade API key](https://www.coinbase.com/settings/api) with EC private key
- Vercel account (free tier works)
- Upstash KV database (created via Vercel Storage)

### Local development

```bash
# Install Python deps
pip install -r requirements.txt

# Install JS deps
npm install

# Copy and fill in env vars
cp .env.local.example .env.local   # add COINBASE_API_KEY_NAME, COINBASE_API_PRIVATE_KEY

# Run backend (port 3000) — serves /api/* and auto-fires cycle every 30m
python scripts/dev_server.py

# Run frontend (port 5173) — proxies /api → 3000
npm run dev

# Manual cycle
python api/mock_cycle.py

# Reset paper portfolio to $1,000
python api/mock_cycle.py --reset
```

### Deploy to Vercel

```bash
npm i -g vercel
vercel login
vercel --prod
```

Add these environment variables in the Vercel dashboard (Settings → Environment Variables):

```
COINBASE_API_KEY_NAME     organizations/xxx/apiKeys/yyy
COINBASE_API_PRIVATE_KEY  <EC private key — literal \n, not real newlines>
CRON_SECRET               <any random string>
KV_REST_API_URL           <set automatically when you add Upstash KV>
KV_REST_API_TOKEN         <set automatically when you add Upstash KV>
```

Add Upstash KV: Vercel dashboard → your project → Storage → Create → KV Database.

### Cron setup (cron-job.org — free)

1. Create account at [cron-job.org](https://cron-job.org)
2. New cron job: `GET https://your-app.vercel.app/api/mock_cycle`
3. Header: `Authorization: Bearer <your CRON_SECRET>`
4. Schedule: **every 1 minute** (fast SL/TP polling; signal computation only fires on 30m bar boundaries)

---

## API endpoints

| URL | Auth | Description |
|-----|------|-------------|
| `/` | public | React dashboard |
| `/api/mock_status` | public | Paper trading state JSON |
| `/api/mock_cycle` | CRON\_SECRET | Run one trading cycle |

---

## Strategy notes

**Why event-based signals?** Holding a cross condition re-fires the signal every bar. On choppy markets this means entering, getting stopped out, and re-entering immediately — a fee spiral that destroys returns. GRACE fires only on the bar the cross *happens*, not while the condition persists.

**Why the 6h regime filter?** Gate 4 requires the 30m signal direction to agree with the 6h macro trend. This eliminates counter-trend trades that look valid on the 30m but are fighting a dominant macro move.

**Why 1.5 ATR stop / 4.0 ATR target?** Backtested variants from 0.5–3.0× SL and 1.5–5.0× TP. The 1.5/4.0 pair maximises Sharpe while keeping drawdown under 10%.
