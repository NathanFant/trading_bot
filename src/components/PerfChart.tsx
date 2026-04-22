import {
  Chart as ChartJS,
  CategoryScale, LinearScale, PointElement,
  LineElement, Tooltip, Legend, Filler,
  type ChartOptions,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import type { PerfData, PerfSnapshot } from '../types'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend, Filler)

function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function pct(current: number, base: number): number {
  return parseFloat(((current / base - 1) * 100).toFixed(2))
}

interface Props {
  perf: PerfData | null
}

export default function PerfChart({ perf }: Props) {
  const inception = perf?.inception
  const snapshots = perf?.snapshots ?? []

  if (!inception || snapshots.length === 0) {
    return (
      <div className="mini-card">
        <div className="mini-card-title">Performance vs Benchmarks</div>
        <div className="perf-empty">No data yet — populates after first cycle run</div>
      </div>
    )
  }

  const seen = new Set<number>()
  const points: PerfSnapshot[] = [inception, ...snapshots]
    .filter(p => {
      const day = Math.floor(p.timestamp / 86400)
      if (seen.has(day)) return false
      seen.add(day)
      return true
    })
    .sort((a, b) => a.timestamp - b.timestamp)

  const labels  = points.map(p => fmtDate(p.timestamp))
  const botPct  = points.map(p => pct(p.bot_usd, inception.bot_usd))
  const btcPct  = points.map(p => p.btc_price != null && inception.btc_price != null
    ? pct(p.btc_price, inception.btc_price) : null)
  const vooPct  = points.map(p => p.voo_price != null && inception.voo_price != null
    ? pct(p.voo_price, inception.voo_price) : null)

  const data = {
    labels,
    datasets: [
      {
        label: 'Bot',
        data: botPct,
        borderColor: '#3fb950',
        backgroundColor: 'rgba(63,185,80,0.08)',
        tension: 0.35,
        pointRadius: 3,
        pointHoverRadius: 5,
        fill: true,
      },
      {
        label: 'BTC',
        data: btcPct,
        borderColor: '#f7931a',
        backgroundColor: 'transparent',
        tension: 0.35,
        pointRadius: 3,
        pointHoverRadius: 5,
        spanGaps: true,
      },
      {
        label: 'VOO',
        data: vooPct,
        borderColor: '#58a6ff',
        backgroundColor: 'transparent',
        tension: 0.35,
        pointRadius: 3,
        pointHoverRadius: 5,
        spanGaps: true,
      },
    ],
  }

  const options: ChartOptions<'line'> = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 600 },
    plugins: {
      legend: {
        labels: { color: '#8b949e', font: { size: 11 }, boxWidth: 12, padding: 12 },
      },
      tooltip: {
        callbacks: {
          label: ctx => {
            const v = ctx.parsed.y ?? 0
            return `${ctx.dataset.label ?? ''}: ${v >= 0 ? '+' : ''}${v.toFixed(1)}%`
          },
        },
      },
    },
    scales: {
      x: {
        ticks: { color: '#8b949e', maxTicksLimit: 6, font: { size: 11 } },
        grid: { color: '#21262d' },
      },
      y: {
        ticks: {
          color: '#8b949e',
          font: { size: 11 },
          callback: v => v == null ? '' : (Number(v) >= 0 ? '+' : '') + Number(v).toFixed(0) + '%',
        },
        grid: { color: '#21262d' },
      },
    },
  }

  return (
    <div className="mini-card">
      <div className="mini-card-title">Performance vs Benchmarks</div>
      <div className="perf-chart-wrap">
        <Line data={data} options={options} />
      </div>
    </div>
  )
}
