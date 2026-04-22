import { useState, useEffect, useCallback } from 'react'
import type { StatusData } from '../types'

export function useStatusData(intervalMs = 60_000) {
  const [data, setData] = useState<StatusData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [lastFetch, setLastFetch] = useState(0)

  const refetch = useCallback(async () => {
    try {
      const res = await fetch('/api/status')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = (await res.json()) as StatusData
      setData(json)
      setError(null)
      setLastFetch(Date.now())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refetch()
    const id = setInterval(refetch, intervalMs)
    return () => clearInterval(id)
  }, [refetch, intervalMs])

  return { data, error, loading, refetch, lastFetch }
}
