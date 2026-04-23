import { useState } from 'react'
import type { MockStatusData } from '../types'

function timeAgo(ts: number): string {
  if (!ts) return 'never'
  const s = Math.floor(Date.now() / 1000) - ts
  if (s < 60)   return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ago`
}

interface Props {
  data: MockStatusData
  lastFetch: number
  onRefresh: () => Promise<void> | void
}

export default function Header({ data, lastFetch, onRefresh }: Props) {
  const [refreshing, setRefreshing] = useState(false)
  const cycleAgo  = data.last_cycle_ts ? timeAgo(data.last_cycle_ts) : 'never'
  const cycleAction = data.last_cycle_result?.action ?? '—'

  const handleRefresh = async () => {
    setRefreshing(true)
    const startTime = Date.now()
    try {
      await Promise.resolve(onRefresh())
    } finally {
      // Ensure at least one full rotation (1000ms) before stopping
      const elapsed = Date.now() - startTime
      const remainingTime = Math.max(0, 1000 - elapsed)
      setTimeout(() => {
        setRefreshing(false)
      }, remainingTime)
    }
  }

  return (
    <header className="header">
      <div>
        <div className="header-title">SOL Perp Paper Trading</div>
        <div className="header-sub">
          EMA-ADX+Regime V2 · 30m · 5× lev · Cycle {cycleAgo} ({cycleAction})
          {lastFetch ? ` · Refreshed ${Math.round((Date.now() - lastFetch) / 1000)}s ago` : ''}
        </div>
      </div>
      <div className="header-right">
        {data.sol_price && (
          <span className="badge badge-price">SOL ${data.sol_price.toFixed(2)}</span>
        )}
        <span className="badge badge-paper">PAPER</span>
        <button className="btn" onClick={handleRefresh} disabled={refreshing}>
          <span className={refreshing ? 'refresh-icon-rotating' : ''}>↻</span> Refresh
        </button>
      </div>
    </header>
  )
}
