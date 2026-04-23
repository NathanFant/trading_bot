export interface Position {
  dir: 'LONG' | 'SHORT'
  entry_px: number
  current_px: number
  sl: number
  tp: number
  contracts: number
  unrealized_pnl: number
  sl_dist_pct: number
  tp_dist_pct: number
  progress_pct: number
  entry_ts: number
}

export interface IndicatorState {
  regime: 'BULL' | 'BEAR'
  adx: number
  ema_fast: number
  ema_slow: number
  ema_trend: number
  ema_aligned: boolean
  vol_surge: boolean
  latest_bar_ts: number
  last_signal: number  // -1, 0, +1
}

export interface MockTrade {
  dir: 'LONG' | 'SHORT'
  entry_px: number
  exit_px: number
  contracts: number
  gross_pnl: number
  fees: number
  net_pnl: number
  exit_reason: 'TP' | 'SL' | 'FLIP' | 'END'
  entry_ts: number
  exit_ts: number
}

export interface EquityPoint {
  ts: number
  usd: number
}

export interface MockStats {
  num_trades: number
  win_rate: number
  profit_factor: number | null
  total_fees: number
  max_drawdown_pct: number
}

export interface MockStatusData {
  timestamp: number
  portfolio_usd: number
  cash_usd: number
  start_usd: number
  pnl_pct: number
  sol_price: number | null
  sol_bh_pct: number | null
  position: Position | null
  indicator_state: IndicatorState | null
  trades: MockTrade[]
  equity_history: EquityPoint[]
  stats: MockStats
  last_cycle_ts: number
  last_cycle_result: Record<string, string>
}
