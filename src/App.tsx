import { useStatusData } from './hooks/useStatusData'
import Header from './components/Header'
import PortfolioCards from './components/PortfolioCards'
import FgiPanel from './components/FgiPanel'
import BayesianCard from './components/BayesianCard'
import PerfChart from './components/PerfChart'
import TradesTable from './components/TradesTable'

export default function App() {
  const { data, error, loading, refetch } = useStatusData()

  if (loading) return <div className="loading">Loading…</div>

  if (!data) {
    return (
      <div className="app">
        {error && <div className="error-banner">Failed to load: {error}</div>}
      </div>
    )
  }

  return (
    <div className="app">
      <Header
        symbol={data.symbol}
        dryRun={data.dry_run}
        dataTimestamp={data.timestamp}
        lastCycle={data.last_cycle}
        onRefresh={refetch}
      />

      {error && <div className="error-banner">Refresh failed: {error}</div>}

      <PortfolioCards portfolio={data.portfolio} symbol={data.symbol} />

      <FgiPanel fgi={data.fgi} config={data.config} />

      <div className="row-2">
        <BayesianCard bayesian={data.bayesian} />
        <PerfChart perf={data.perf} />
      </div>

      <TradesTable trades={data.trades} symbol={data.symbol} />
    </div>
  )
}
