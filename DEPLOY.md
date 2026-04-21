# Deploying to Vercel (free)

The bot runs as a Vercel serverless function at `/api/cycle`.
Vercel Cron triggers it every hour. Bayesian state persists in Vercel KV (free Upstash Redis).

## One-time setup

### 1. Install Vercel CLI and deploy

```bash
npm i -g vercel
vercel login
vercel --prod
```

### 2. Add Vercel KV (persistent Bayesian state)

In the Vercel dashboard → your project → Storage → Create → KV Database.
Vercel automatically adds `KV_REST_API_URL` and `KV_REST_API_TOKEN` to your env.

### 3. Set environment variables

In the Vercel dashboard → Settings → Environment Variables, add:

```
ROBINHOOD_API_KEY        rh-api-xxxx
ROBINHOOD_PRIVATE_KEY    <base64 private key>
COINMARKETCAP_API_KEY    <optional>
DISCORD_WEBHOOK_URL      <optional>
CRON_SECRET              <any random string — protects /api/cycle>
SYMBOL                   BTC-USD
DRY_RUN                  true
SCALE_BUY_WITH_Z         true
MIN_BUY_PCT              0.24
MAX_BUY_PCT              0.74
MAX_SCALE_Z              4.0
SELL_PCT                 0.65
MIN_CONFIDENCE           0.53
BUY_Z_THRESHOLD          -1.95
SELL_Z_THRESHOLD         2.65
FGI_HISTORY_DAYS         55
DATA_DIR                 /tmp
```

### 4. Go live

Once dry-run logs look correct:

```
DRY_RUN → false
```

## How it works

- Vercel Cron calls `GET /api/cycle` every hour (`vercel.json`)
- The function runs one complete trading cycle and exits
- Bayesian state (the bot's learned confidence) is stored in Vercel KV and persists across invocations
- Trade history and logs appear in Vercel's function log viewer
- Discord webhook (optional) sends trade alerts

## Cron schedule note

Vercel Hobby (free) supports cron jobs — `vercel.json` is already configured for hourly.
If your plan doesn't support sub-daily crons, use [cron-job.org](https://cron-job.org) (free):

1. Create an account at cron-job.org
2. Add a new cron job pointing to `https://your-app.vercel.app/api/cycle`
3. Set Authorization header: `Bearer <your CRON_SECRET>`
4. Schedule: every 60 minutes

## Useful commands

```bash
vercel logs               # recent function logs
vercel env ls             # list env var names
vercel --prod             # redeploy
```

## Local development

```bash
# Run the full polling loop locally
python main.py

# Simulate what Vercel runs (single cycle)
python -c "import database as db; from trader import Trader; db.init_db(); Trader().run_once()"
```
