export interface Portfolio {
  cash: number
  sol_qty: number
  sol_value: number
  total: number
  sol_price: number
  error?: string
}

export interface FgiData {
  value: number
  label: string
  z_score: number
  mean: number
  std: number
  signal: 'BUY' | 'SELL' | 'HOLD'
  confidence: number
  reason: string
  error?: string
}

export interface BayesianData {
  buy_confidence: number
  sell_confidence: number
}

export interface Config {
  min_buy_pct: number
  max_buy_pct: number
  sell_pct: number
  buy_z: number
  sell_z: number
  min_confidence: number
}

export interface Trade {
  id: number
  timestamp: number
  action: string
  symbol: string
  quantity: number
  price: number
  usd_amount: number
  fgi_value: number
  z_score: number
  dry_run: boolean
}

export interface LastCycle {
  timestamp: number
  last_signal: string
  last_skip_reason: string
}

export interface PerfSnapshot {
  timestamp: number
  bot_usd: number
  btc_price: number | null
  voo_price: number | null
}

export interface PerfData {
  inception: PerfSnapshot | null
  snapshots: PerfSnapshot[]
}

export interface StatusData {
  timestamp: number
  dry_run: boolean
  symbol: string
  config: Config
  fgi: FgiData
  portfolio: Portfolio
  bayesian: BayesianData | null
  trades: Trade[]
  last_cycle: LastCycle | null
  perf: PerfData | null
}
