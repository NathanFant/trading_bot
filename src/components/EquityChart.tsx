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
  const points: { ts: number; usd: number; trade: MockTrade }[] = sorted.map(t => {
    running += t.net_pnl
    return { ts: t.exit_ts, usd: running, trade: t }
  })

  const labels   = [fmtTime(sorted[0].entry_ts), ...points.map(p => fmtTime(p.ts))]
  const values   = [startUsd, ...points.map(p => parseFloat(p.usd.toFixed(2)))]
  const baseline = Array(labels.length).fill(startUsd)
  const isGreen  = values[values.length - 1] >= startUsd

  // Per-point colors: green dot for win, red for loss
  const pointColors = [
    'transparent',
    ...points.map(p => (p.trade.net_pnl > 0 ? '#3fb950' : '#f85149')),
  ]

  const chartData = {
    labels,
    datasets: [
      {
        label: 'Equity',
        data: values,
        borderColor: isGreen ? '#3fb950' : '#f85149',
        backgroundColor: isGreen ? 'rgba(63,185,80,0.08)' : 'rgba(248,81,73,0.06)',
        tension: 0,
        pointRadius: [0, ...points.map(() => 5)],
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
            if (idx === 0) return `Start: $${startUsd}`
            const p = points[idx - 1]
            const pct = ((p.usd - startUsd) / startUsd * 100)
            const sign = p.trade.net_pnl >= 0 ? '+' : ''
            return [
              `Equity: $${p.usd.toFixed(2)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)`,
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
