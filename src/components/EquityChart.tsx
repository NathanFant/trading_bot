import {
  Chart as ChartJS,
  CategoryScale, LinearScale, PointElement,
  LineElement, Tooltip, Filler,
  type ChartOptions,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import type { MockTrade } from '../types'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler)

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

interface Props {
  trades: MockTrade[]
  startUsd: number
}

export default function EquityChart({ trades, startUsd }: Props) {
  const sorted = [...trades].sort((a, b) => a.exit_ts - b.exit_ts)

  if (sorted.length === 0) {
    return (
      <div className="mini-card" style={{ marginBottom: 12 }}>
        <div className="mini-card-title">Equity Curve</div>
        <div className="perf-empty">Populates after first trade</div>
      </div>
    )
  }

  // Build equity curve: one point per trade exit
  let running = startUsd
  const tradePoints: { ts: number; usd: number; trade: MockTrade }[] = sorted.map(t => {
    running += t.net_pnl
    return { ts: t.exit_ts, usd: running, trade: t }
  })

  const nowTs      = Math.floor(Date.now() / 1000)
  const preTs      = sorted[0].entry_ts - 86400   // 1 day before first trade

  // Full timeline: pre-trade anchor → trade exits → now
  const tsTicks    = [preTs, ...tradePoints.map(p => p.ts), nowTs]
  const valueTicks = [startUsd, ...tradePoints.map(p => parseFloat(p.usd.toFixed(2))), parseFloat(running.toFixed(2))]
  const baseline   = Array(tsTicks.length).fill(startUsd)
  const isGreen    = running >= startUsd

  // Per-point colors: invisible on anchors, green/red on trade exits
  const pointColors = tsTicks.map((_, i) => {
    if (i === 0 || i === tsTicks.length - 1) return 'transparent'
    const p = tradePoints[i - 1]
    return p.trade.net_pnl > 0 ? '#3fb950' : '#f85149'
  })
  const pointRadii = tsTicks.map((_, i) =>
    (i === 0 || i === tsTicks.length - 1) ? 0 : 5
  )

  const chartData = {
    labels: tsTicks.map(fmtTime),
    datasets: [
      {
        label: 'Equity',
        data: valueTicks,
        borderColor: isGreen ? '#3fb950' : '#f85149',
        backgroundColor: isGreen ? 'rgba(63,185,80,0.08)' : 'rgba(248,81,73,0.06)',
        tension: 0,
        pointRadius: pointRadii,
        pointHoverRadius: 7,
        pointBackgroundColor: pointColors,
        pointBorderColor: pointColors,
        fill: true,
        borderWidth: 2,
        stepped: true,
      },
      {
        label: `Start ($${startUsd})`,
        data: baseline,
        borderColor: 'rgba(139,148,158,0.3)',
        backgroundColor: 'transparent',
        borderDash: [4, 4],
        pointRadius: 0,
        tension: 0,
        stepped: false,
      },
    ],
  }

  const options: ChartOptions<'line'> = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 400 },
    plugins: {
      legend: {
        labels: { color: '#8b949e', font: { size: 11 }, boxWidth: 12, padding: 12 },
      },
      tooltip: {
        callbacks: {
          label: ctx => {
            if (ctx.datasetIndex !== 0) return `Start: $${startUsd}`
            const idx = ctx.dataIndex
            const usd = valueTicks[idx]
            const pct = ((usd - startUsd) / startUsd * 100)
            const pctStr = `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`
            // anchor points (pre-trade or trailing now-point)
            if (idx === 0 || idx === tsTicks.length - 1) {
              return `Equity: $${usd.toFixed(2)} (${pctStr})`
            }
            const p = tradePoints[idx - 1]
            const sign = p.trade.net_pnl >= 0 ? '+' : ''
            return [
              `Equity: $${usd.toFixed(2)} (${pctStr})`,
              `${p.trade.dir} ${p.trade.exit_reason}  ${sign}$${p.trade.net_pnl.toFixed(2)}`,
            ]
          },
        },
      },
    },
    scales: {
      x: {
        ticks: { color: '#8b949e', maxTicksLimit: 8, font: { size: 10 }, maxRotation: 0 },
        grid: { color: '#21262d' },
      },
      y: {
        ticks: {
          color: '#8b949e',
          font: { size: 11 },
          callback: v => `$${Number(v).toFixed(0)}`,
        },
        grid: { color: '#21262d' },
      },
    },
  }

  return (
    <div className="mini-card" style={{ marginBottom: 12 }}>
      <div className="mini-card-title">Equity Curve</div>
      <div className="perf-chart-wrap">
        <Line data={chartData} options={options} />
      </div>
    </div>
  )
}
