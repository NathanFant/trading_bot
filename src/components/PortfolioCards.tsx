import { useAnimatedValue } from '../hooks/useAnimatedValue'
import { useFlash } from '../hooks/useFlash'
import type { Portfolio } from '../types'

function fmtUSD(n: number) {
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

interface AnimatedCardProps {
  label: string
  value: number
  format: (n: number) => string
  sub?: string
}

function AnimatedCard({ label, value, format, sub }: AnimatedCardProps) {
  const animated = useAnimatedValue(value)
  const flash = useFlash(value)

  return (
    <div className="card">
      <div className="card-label">{label}</div>
      <div className={`card-value ${flash === 'up' ? 'flash-up' : flash === 'down' ? 'flash-down' : ''}`}>
        {format(animated)}
      </div>
      {sub && <div className="card-sub">{sub}</div>}
    </div>
  )
}

interface Props {
  portfolio: Portfolio
  symbol: string
}

export default function PortfolioCards({ portfolio, symbol }: Props) {
  const asset = symbol.split('-')[0]

  if (portfolio.error) {
    return (
      <div className="cards">
        <div className="card">
          <div className="card-label">Portfolio</div>
          <div style={{ color: 'var(--red)', fontSize: 13 }}>{portfolio.error}</div>
        </div>
      </div>
    )
  }

  return (
    <div className="cards">
      <AnimatedCard label="Total Value" value={portfolio.total} format={fmtUSD} sub={symbol} />
      <AnimatedCard label="Cash" value={portfolio.cash} format={fmtUSD} sub="available" />
      <AnimatedCard
        label={`${asset} Holdings`}
        value={portfolio.sol_qty}
        format={n => `${n.toFixed(6)} ${asset}`}
        sub={fmtUSD(portfolio.sol_value)}
      />
      <AnimatedCard
        label={`${asset} Price`}
        value={portfolio.sol_price}
        format={fmtUSD}
        sub="mid-market"
      />
    </div>
  )
}
