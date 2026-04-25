import { useStatusData } from './hooks/useStatusData'
import Header from './components/Header'
import PortfolioCards from './components/PortfolioCards'
import PositionPanel from './components/PositionPanel'
import SignalPanel from './components/SignalPanel'
import EquityChart from './components/EquityChart'
import StatsRow from './components/StatsRow'
import TradesTable from './components/TradesTable'

export default function App() {
  const { data, error, loading, refetch, lastFetch } = useStatusData()

  if (loading) return <div className="loading">Connecting…</div>

  if (!data) {
    return (
      <div className="app">
        {error && <div className="error-banner">Failed to load: {error}</div>}
        <div className="loading">No data — run <code>python api/mock_cycle.py</code> first</div>
      </div>
    )
  }

  return (
    <div className="app">
      <Header data={data} lastFetch={lastFetch} onRefresh={refetch} />
      {error && <div className="error-banner">Refresh failed: {error}</div>}
      <PortfolioCards data={data} />
      <div className="row-2">
        <PositionPanel position={data.position} solPrice={data.sol_price} />
        <SignalPanel indicators={data.indicator_state} lastCycleTs={data.last_cycle_ts} strategyConfig={data.strategy_config} />
      </div>
      <EquityChart trades={data.trades} startUsd={data.start_usd} />
      <StatsRow stats={data.stats} />
      <TradesTable trades={data.trades} />
    </div>
  )
}
