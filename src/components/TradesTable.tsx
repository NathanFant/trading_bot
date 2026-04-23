import type { MockTrade } from '../types'

function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function exitColor(reason: string): string {
  if (reason === 'TP')   return 'var(--green)'
  if (reason === 'SL')   return 'var(--red)'
  if (reason === 'FLIP') return 'var(--blue)'
  return 'var(--muted)'
}

interface Props { trades: MockTrade[] }

export default function TradesTable({ trades }: Props) {
  return (
    <div className="trades-panel">
      <div className="trades-title">Trade History (newest first)</div>
      {trades.length === 0 ? (
        <div className="no-trades">No trades yet — waiting for first signal</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Exit Time</th>
              <th>Dir</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Reason</th>
              <th>Net PnL</th>
              <th>Fees</th>
            </tr>
          </thead>
          <tbody>
            {trades.slice(0, 25).map((t, i) => (
              <tr key={i}>
                <td style={{ fontSize: 11, color: 'var(--muted)' }}>{fmtDate(t.exit_ts)}</td>
                <td>
                  <span style={{
                    color: t.dir === 'LONG' ? 'var(--green)' : 'var(--red)',
                    fontWeight: 600, fontSize: 12,
                  }}>
                    {t.dir}
                  </span>
                </td>
                <td>${t.entry_px.toFixed(2)}</td>
                <td>${t.exit_px.toFixed(2)}</td>
                <td>
                  <span style={{ color: exitColor(t.exit_reason), fontWeight: 600, fontSize: 12 }}>
                    {t.exit_reason}
                  </span>
                </td>
                <td style={{ color: t.net_pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                  {t.net_pnl >= 0 ? '+' : ''}${t.net_pnl.toFixed(2)}
                </td>
                <td style={{ color: 'var(--muted)', fontSize: 11 }}>${t.fees.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
