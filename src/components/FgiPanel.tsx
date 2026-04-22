import { useRef, useEffect } from 'react'
import { useFlash } from '../hooks/useFlash'
import type { FgiData, Config } from '../types'

function fgiColor(v: number): string {
  if (v <= 24) return '#ef4444'
  if (v <= 44) return '#f97316'
  if (v <= 55) return '#eab308'
  if (v <= 74) return '#84cc16'
  return '#22c55e'
}

interface Props {
  fgi: FgiData
  config: Config
}

export default function FgiPanel({ fgi, config }: Props) {
  const flash = useFlash(fgi.value)
  const prevSignalRef = useRef(fgi.signal)
  const badgeRef = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (prevSignalRef.current !== fgi.signal && badgeRef.current) {
      badgeRef.current.classList.remove('changing')
      void badgeRef.current.offsetWidth
      badgeRef.current.classList.add('changing')
      prevSignalRef.current = fgi.signal
    }
  }, [fgi.signal])

  if (fgi.error) {
    return (
      <div className="fgi-panel">
        <div className="fgi-panel-title">Fear &amp; Greed Index</div>
        <div style={{ color: 'var(--red)', fontSize: 13 }}>{fgi.error}</div>
      </div>
    )
  }

  const color = fgiColor(fgi.value)
  const fires = fgi.confidence >= config.min_confidence && fgi.signal !== 'HOLD'

  return (
    <div className="fgi-panel">
      <div className="fgi-panel-title">Fear &amp; Greed Index</div>

      <div className="fgi-value-row">
        <div
          className={`fgi-number ${flash === 'up' ? 'flash-up' : flash === 'down' ? 'flash-down' : ''}`}
          style={{ color }}
        >
          {fgi.value}
        </div>
        <div className="fgi-label-text" style={{ color }}>{fgi.label}</div>
      </div>

      <div className="fgi-bar-wrap">
        <div className="fgi-marker" style={{ left: `${fgi.value}%` }} />
      </div>
      <div className="fgi-axis">
        <span>Extreme Fear</span><span>Fear</span><span>Neutral</span>
        <span>Greed</span><span>Extreme Greed</span>
      </div>

      <div className="signal-row">
        <span ref={badgeRef} className={`signal-badge signal-${fgi.signal}`}>
          {fgi.signal}
        </span>
        <span className="signal-meta">
          Z-score <strong>{fgi.z_score >= 0 ? '+' : ''}{fgi.z_score.toFixed(2)}σ</strong>
        </span>
        <span className="signal-meta">
          Confidence <strong>{(fgi.confidence * 100).toFixed(0)}%</strong>
          {' '}(need {(config.min_confidence * 100).toFixed(0)}% to trade)
        </span>
        {fires && <span className="would-fire">WOULD FIRE</span>}
      </div>
      <div className="signal-reason">{fgi.reason}</div>
    </div>
  )
}
