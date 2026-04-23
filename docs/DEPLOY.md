# Deploying to Vercel (free)

The dashboard and API run as Vercel serverless functions.
State persists in Vercel KV (free Upstash Redis).

## One-time setup

### 1. Install Vercel CLI and deploy

```bash
npm i -g vercel
vercel login
vercel --prod
```

### 2. Add Vercel KV (required — all state stored here)

Vercel dashboard → your project → Storage → Create → KV Database.
Vercel automatically adds `KV_REST_API_URL` and `KV_REST_API_TOKEN` to your env.

### 3. Set environment variables

Vercel dashboard → Settings → Environment Variables:

```
# Coinbase API (for paper trading live data)
COINBASE_API_KEY_NAME    organizations/xxx/apiKeys/yyy
COINBASE_API_PRIVATE_KEY <EC private key — use literal \n, not real newlines>

# Shared
CRON_SECRET              <any random string — protects cycle endpoints>

# Legacy FGI bot (optional — only needed if /api/cycle is still active)
ROBINHOOD_API_KEY        rh-api-xxxx
ROBINHOOD_PRIVATE_KEY    <base64 private key>
COINMARKETCAP_API_KEY    <optional>
DISCORD_WEBHOOK_URL      <optional>
SYMBOL                   SOL-USD
DRY_RUN                  true
```

### 4. Set up paper trading cron (every 30 min)

Use [cron-job.org](https://cron-job.org) (free):

1. Create account at cron-job.org
2. Add cron job: `GET https://your-app.vercel.app/api/mock_cycle`
3. Add header: `Authorization: Bearer <your CRON_SECRET>`
4. Schedule: every 30 minutes (matches the 30m candle bars)

### 5. Deploy

```bash
vercel --prod
```

The dashboard will be live at your Vercel URL. The paper trader starts tracking
on the first cron hit. Reset state at any time:

```bash
# Curl to reset (get a fresh $1,000 start)
curl -X GET https://your-app.vercel.app/api/mock_cycle \
  -H "Authorization: Bearer <CRON_SECRET>" \
  # then in Python: python api/mock_cycle.py --reset (local only)
```

Or locally: `python api/mock_cycle.py --reset`

## Endpoints

| URL | Auth | Description |
|-----|------|-------------|
| `/` | public | Dashboard (React SPA) |
| `/api/mock_status` | public | Paper trading state JSON |
| `/api/mock_cycle` | CRON_SECRET | Run one 30m trading cycle |
| `/api/status` | public | Legacy FGI bot status |
| `/api/cycle` | CRON_SECRET | Legacy FGI trading cycle |

## How the paper trader works

- `/api/mock_cycle` fires every 30 min via cron-job.org
- Fetches the last 150 bars of 30m SOL perp candles from Coinbase
- Fetches 50 bars of 6h candles for the regime filter
- Evaluates EMA-ADX+Regime strategy (the 90-day backtest winner: +26.5%, Sharpe 19.54)
- Manages a paper position: opens on signal, closes on SL/TP hit against live price
- State (portfolio, trades, equity curve) persists in KV between invocations
- Dashboard polls `/api/mock_status` every 60s and shows everything live

## Useful commands

```bash
vercel logs        # recent function logs
vercel env ls      # list env var names
vercel --prod      # redeploy after code changes
```

## Local development

```bash
# Backend (port 3000) + auto-cycle every 30m
python scripts/dev_server.py

# Frontend (port 5173, proxies /api → 3000)
npm run dev

# Run one cycle manually
python api/mock_cycle.py

# Reset paper state
python api/mock_cycle.py --reset
```
