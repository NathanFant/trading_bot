import type { MockStatusData } from '../types'

function fmtUSD(n: number, digits = 2) {
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function pnlColor(n: number) {
  return n > 0 ? 'var(--green)' : n < 0 ? 'var(--red)' : 'var(--text)'
}

interface Props { data: MockStatusData }

export default function PortfolioCards({ data }: Props) {
  const pnlUsd = data.portfolio_usd - data.start_usd

  return (
    <div className="cards">
      <div className="card">
        <div className="card-label">Portfolio Value</div>
        <div className="card-value">{fmtUSD(data.portfolio_usd)}</div>
        <div className="card-sub">started {fmtUSD(data.start_usd)}</div>
      </div>

      <div className="card">
        <div className="card-label">Total PnL</div>
        <div className="card-value" style={{ color: pnlColor(pnlUsd) }}>
          {pnlUsd >= 0 ? '+' : ''}{fmtUSD(pnlUsd)}
        </div>
        <div className="card-sub" style={{ color: pnlColor(data.pnl_pct) }}>
          {data.pnl_pct >= 0 ? '+' : ''}{data.pnl_pct.toFixed(2)}%
        </div>
      </div>

      <div className="card">
        <div className="card-label">SOL B&amp;H</div>
        <div className="card-value" style={{ color: data.sol_bh_pct != null ? pnlColor(data.sol_bh_pct) : 'var(--muted)' }}>
          {data.sol_bh_pct != null
            ? `${data.sol_bh_pct >= 0 ? '+' : ''}${data.sol_bh_pct.toFixed(2)}%`
            : '—'}
        </div>
        <div className="card-sub">buy &amp; hold benchmark</div>
      </div>

      <div className="card">
        <div className="card-label">Max Drawdown</div>
        <div className="card-value" style={{ color: data.stats.max_drawdown_pct > 10 ? 'var(--red)' : 'var(--text)' }}>
          {data.stats.max_drawdown_pct.toFixed(1)}%
        </div>
        <div className="card-sub">from peak equity</div>
      </div>
    </div>
  )
}
