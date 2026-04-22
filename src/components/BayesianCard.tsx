import type { BayesianData } from '../types'

interface Props {
  bayesian: BayesianData | null
}

export default function BayesianCard({ bayesian }: Props) {
  return (
    <div className="mini-card">
      <div className="mini-card-title">Bayesian Confidence</div>
      {bayesian ? (
        <>
          <div className="stat-row">
            <span>BUY success rate</span>
            <span className="stat-val up">{(bayesian.buy_confidence * 100).toFixed(1)}%</span>
          </div>
          <div className="stat-row">
            <span>SELL success rate</span>
            <span className="stat-val up">{(bayesian.sell_confidence * 100).toFixed(1)}%</span>
          </div>
        </>
      ) : (
        <div style={{ color: 'var(--muted)', fontSize: 13 }}>
          No data yet — improves with live trades
        </div>
      )}
    </div>
  )
}
