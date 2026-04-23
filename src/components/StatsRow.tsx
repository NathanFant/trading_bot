import type { MockStats } from '../types'

interface Props { stats: MockStats }

export default function StatsRow({ stats }: Props) {
  const pfDisplay = stats.profit_factor != null
    ? stats.profit_factor.toFixed(2)
    : stats.num_trades > 0 ? '∞' : '—'

  return (
    <div className="cards" style={{ marginTop: 0 }}>
      <div className="card">
        <div className="card-label">Trades</div>
        <div className="card-value">{stats.num_trades}</div>
        <div className="card-sub">
          {stats.num_trades > 0 ? `${stats.win_rate.toFixed(1)}% win rate` : 'no trades yet'}
        </div>
      </div>
      <div className="card">
        <div className="card-label">Win Rate</div>
        <div className="card-value" style={{ color: stats.win_rate >= 47 ? 'var(--green)' : stats.win_rate >= 40 ? 'var(--yellow)' : 'var(--red)' }}>
          {stats.num_trades > 0 ? `${stats.win_rate.toFixed(1)}%` : '—'}
        </div>
        <div className="card-sub">target ≥ 47%</div>
      </div>
      <div className="card">
        <div className="card-label">Profit Factor</div>
        <div className="card-value" style={{ color: (stats.profit_factor ?? 0) >= 2 ? 'var(--green)' : 'var(--muted)' }}>
          {pfDisplay}
        </div>
        <div className="card-sub">wins / losses</div>
      </div>
      <div className="card">
        <div className="card-label">Fees Paid</div>
        <div className="card-value" style={{ fontSize: 18 }}>${stats.total_fees.toFixed(2)}</div>
        <div className="card-sub">0.1% taker + $0.15 NFA</div>
      </div>
    </div>
  )
}
