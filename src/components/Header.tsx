import type { LastCycle } from '../types'

function timeAgo(ts: number): string {
  const s = Math.floor(Date.now() / 1000) - ts
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ago`
}

interface Props {
  symbol: string
  dryRun: boolean
  dataTimestamp: number
  lastCycle: LastCycle | null
  onRefresh: () => void
}

export default function Header({ symbol, dryRun, dataTimestamp, lastCycle, onRefresh }: Props) {
  const cycleInfo = lastCycle
    ? `Cycle ${timeAgo(lastCycle.timestamp)} · ${lastCycle.last_signal}${lastCycle.last_skip_reason ? ` · skipped: ${lastCycle.last_skip_reason}` : ''}`
    : 'No cycle yet'

  return (
    <header className="header">
      <div>
        <div className="header-title">🤖 FGI Trading Bot</div>
        <div className="header-sub">
          {symbol} · Refreshed {timeAgo(dataTimestamp)} · {cycleInfo}
        </div>
      </div>
      <div className="header-right">
        <span className={`badge ${dryRun ? 'badge-dry' : 'badge-live'}`}>
          {dryRun ? 'DRY RUN' : '● LIVE'}
        </span>
        <button className="btn" onClick={onRefresh}>↻ Refresh</button>
      </div>
    </header>
  )
}
