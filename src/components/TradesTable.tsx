import type { Trade } from '../types'

function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function fmtUSD(n: number): string {
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

interface Props {
  trades: Trade[]
  symbol: string
}

export default function TradesTable({ trades, symbol }: Props) {
  const asset = symbol.split('-')[0]

  return (
    <div className="trades-panel">
      <div className="trades-title">Recent Trades</div>
      {trades.length === 0 ? (
        <div className="no-trades">No trades recorded yet</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Action</th>
              <th>Quantity</th>
              <th>Price</th>
              <th>USD</th>
              <th>Signal</th>
            </tr>
          </thead>
          <tbody>
            {trades.slice(0, 20).map(t => (
              <tr key={t.id ?? t.timestamp}>
                <td>{fmtDate(t.timestamp)}</td>
                <td>
                  <span className={`trade-${t.action.toLowerCase()}`}>{t.action}</span>
                  {t.dry_run && <span className="dry-tag">dry</span>}
                </td>
                <td>{t.quantity.toFixed(6)} {asset}</td>
                <td>{fmtUSD(t.price)}</td>
                <td>{fmtUSD(t.usd_amount)}</td>
                <td>FGI {t.fgi_value} ({t.z_score >= 0 ? '+' : ''}{t.z_score.toFixed(2)}σ)</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
