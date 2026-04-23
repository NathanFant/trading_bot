import {
  Chart as ChartJS,
  CategoryScale, LinearScale, PointElement,
  LineElement, Tooltip, Filler,
  type ChartOptions,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import type { EquityPoint } from '../types'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler)

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

interface Props {
  history: EquityPoint[]
  startUsd: number
}

export default function EquityChart({ history, startUsd }: Props) {
  if (history.length < 2) {
    return (
      <div className="mini-card" style={{ marginBottom: 12 }}>
        <div className="mini-card-title">Equity Curve</div>
        <div className="perf-empty">Populates after first cycle run</div>
      </div>
    )
  }

  // Sample to max 200 points to keep chart snappy
  const step   = Math.max(1, Math.floor(history.length / 200))
  const points = history.filter((_, i) => i % step === 0 || i === history.length - 1)

  const labels  = points.map(p => fmtTime(p.ts))
  const values  = points.map(p => p.usd)
  const baseline = Array(points.length).fill(startUsd)

  const isGreen = values[values.length - 1] >= startUsd

  const chartData = {
    labels,
    datasets: [
      {
        label: 'Portfolio',
        data: values,
        borderColor: isGreen ? '#3fb950' : '#f85149',
        backgroundColor: isGreen ? 'rgba(63,185,80,0.08)' : 'rgba(248,81,73,0.06)',
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
        fill: true,
        borderWidth: 2,
      },
      {
        label: `Start ($${startUsd})`,
        data: baseline,
        borderColor: 'rgba(139,148,158,0.3)',
        backgroundColor: 'transparent',
        borderDash: [4, 4],
        pointRadius: 0,
        tension: 0,
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
            const v = ctx.parsed.y ?? 0
            const pct = ((v - startUsd) / startUsd * 100)
            return `${ctx.dataset.label}: $${v.toFixed(2)} (${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%)`
          },
        },
      },
    },
    scales: {
      x: {
        ticks: { color: '#8b949e', maxTicksLimit: 6, font: { size: 10 }, maxRotation: 0 },
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
