import type { IndicatorState } from '../types'

function timeAgo(ts: number): string {
  if (!ts) return '—'
  const s = Math.floor(Date.now() / 1000) - ts
  if (s < 60)   return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ago`
}

interface Dot { ok: boolean }
function Dot({ ok }: Dot) {
  return (
    <span style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
      background: ok ? 'var(--green)' : 'var(--red)',
      marginRight: 6,
    }} />
  )
}

interface Props {
  indicators: IndicatorState | null
  lastCycleTs: number
}

export default function SignalPanel({ indicators, lastCycleTs }: Props) {
  if (!indicators) {
    return (
      <div className="mini-card">
        <div className="mini-card-title">Strategy Signals</div>
        <div style={{ color: 'var(--muted)', fontSize: 14, marginTop: 8 }}>
          No signal data yet
        </div>
      </div>
    )
  }

  const isBull    = indicators.regime === 'BULL'
  const sigLabel  = indicators.last_signal === 1 ? 'LONG' : indicators.last_signal === -1 ? 'SHORT' : 'NONE'
  const sigColor  = indicators.last_signal === 1 ? 'var(--green)' : indicators.last_signal === -1 ? 'var(--red)' : 'var(--muted)'

  return (
    <div className="mini-card">
      <div className="mini-card-title">Strategy Signals</div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <span style={{
          padding: '3px 10px', borderRadius: 20, fontSize: 12, fontWeight: 700,
          background: isBull ? 'rgba(63,185,80,.12)' : 'rgba(248,81,73,.12)',
          color: isBull ? 'var(--green)' : 'var(--red)',
          border: `1px solid ${isBull ? 'rgba(63,185,80,.4)' : 'rgba(248,81,73,.4)'}`,
        }}>
          {indicators.regime} REGIME
        </span>
        <span style={{
          padding: '3px 10px', borderRadius: 20, fontSize: 12, fontWeight: 700,
          color: sigColor, border: `1px solid ${sigColor}55`,
          background: `${sigColor}11`,
        }}>
          {sigLabel}
        </span>
      </div>

      <div className="stat-row">
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>ADX (trend strength)</span>
        <span style={{ fontSize: 13, color: indicators.adx >= 18 ? 'var(--green)' : 'var(--muted)' }}>
          {indicators.adx.toFixed(1)} {indicators.adx >= 18 ? '✓' : '✗ <18'}
        </span>
      </div>
      <div className="stat-row">
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>EMA(9) &gt; EMA(21)</span>
        <span style={{ fontSize: 13 }}>
          <Dot ok={indicators.ema_aligned} />
          {indicators.ema_aligned ? 'bullish' : 'bearish'}
        </span>
      </div>
      <div className="stat-row">
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>Volume surge</span>
        <span style={{ fontSize: 13 }}>
          <Dot ok={indicators.vol_surge} />
          {indicators.vol_surge ? 'yes' : 'no'}
        </span>
      </div>
      <div className="stat-row" style={{ marginTop: 8 }}>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>Last bar</span>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>
          {timeAgo(indicators.latest_bar_ts)}
        </span>
      </div>
      <div className="stat-row">
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>Last cycle</span>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>
          {timeAgo(lastCycleTs)}
        </span>
      </div>
    </div>
  )
}
