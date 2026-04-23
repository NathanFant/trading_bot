import type { Position } from '../types'

function fmtUSD(n: number) {
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(2)
}

function timeHeld(entryTs: number): string {
  const s = Math.floor(Date.now() / 1000) - entryTs
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

interface Props {
  position: Position | null
  solPrice: number | null
}

export default function PositionPanel({ position, solPrice }: Props) {
  if (!position) {
    return (
      <div className="mini-card">
        <div className="mini-card-title">Open Position</div>
        <div style={{ color: 'var(--muted)', fontSize: 14, marginTop: 8 }}>
          Flat — no open position
        </div>
        {solPrice && (
          <div style={{ color: 'var(--muted)', fontSize: 12, marginTop: 8 }}>
            SOL ${solPrice.toFixed(2)}
          </div>
        )}
      </div>
    )
  }

  const isLong   = position.dir === 'LONG'
  const dirColor = isLong ? 'var(--green)' : 'var(--red)'
  const pnlColor = position.unrealized_pnl >= 0 ? 'var(--green)' : 'var(--red)'
  const progress = Math.max(0, Math.min(100, position.progress_pct))

  return (
    <div className="mini-card">
      <div className="mini-card-title">Open Position</div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span className="signal-badge" style={{
          background: isLong ? 'rgba(63,185,80,.12)' : 'rgba(248,81,73,.12)',
          color: dirColor,
          border: `1px solid ${dirColor}55`,
          padding: '4px 12px', borderRadius: 20, fontWeight: 700, fontSize: 13,
        }}>
          {position.dir}
        </span>
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>
          {position.contracts} contracts · held {timeHeld(position.entry_ts)}
        </span>
      </div>

      <div className="stat-row">
        <span style={{ color: 'var(--muted)', fontSize: 12 }}>Entry</span>
        <span style={{ fontSize: 13 }}>${position.entry_px.toFixed(2)}</span>
      </div>
      <div className="stat-row">
        <span style={{ color: 'var(--muted)', fontSize: 12 }}>Current</span>
        <span style={{ fontSize: 13 }}>${position.current_px.toFixed(2)}</span>
      </div>
      <div className="stat-row">
        <span style={{ color: 'var(--muted)', fontSize: 12 }}>Unrealized</span>
        <span style={{ fontSize: 13, color: pnlColor, fontWeight: 600 }}>
          {fmtUSD(position.unrealized_pnl)}
        </span>
      </div>

      {/* SL / TP progress bar */}
      <div style={{ marginTop: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>
          <span>SL {position.sl.toFixed(2)} ({position.sl_dist_pct.toFixed(1)}% away)</span>
          <span>TP {position.tp.toFixed(2)} ({position.tp_dist_pct.toFixed(1)}% away)</span>
        </div>
        <div style={{ height: 6, borderRadius: 3, background: 'var(--border)', overflow: 'hidden' }}>
          <div style={{
            height: '100%',
            width: `${progress}%`,
            borderRadius: 3,
            background: `linear-gradient(to right, ${isLong ? 'var(--green)' : 'var(--red)'}, var(--yellow))`,
            transition: 'width 0.4s ease',
          }} />
        </div>
        <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 3, textAlign: 'center' }}>
          {progress.toFixed(0)}% to TP
        </div>
      </div>
    </div>
  )
}
