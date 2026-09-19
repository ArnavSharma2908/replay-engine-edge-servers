import { useEffect, useRef, useState } from 'react'
import EngineToggle from './components/EngineToggle.jsx'
import StatReadout from './components/StatReadout.jsx'
import LatencyChart from './components/LatencyChart.jsx'
import RequestHeatmap from './components/RequestHeatmap.jsx'
import RequestFeed from './components/RequestFeed.jsx'
import SourceBadge from './components/SourceBadge.jsx'
import { getMetrics, getLiveRequests, getReplayStatus, getShardStats, setEngineMode } from './lib/dataProvider.js'

const TICK_MS = 1200

function formatBytes(bytes) {
  if (bytes < 1024) return `${Math.round(bytes)} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

export default function App() {
  const [mode, setMode] = useState('origin')
  const modeRef = useRef(mode)
  useEffect(() => {
    modeRef.current = mode
  }, [mode])

  const [metrics, setMetrics] = useState(null)
  const [requests, setRequests] = useState([])
  const [replay, setReplay] = useState(null)
  const [shards, setShards] = useState(null)
  const [history, setHistory] = useState([])

  useEffect(() => {
    let alive = true

    async function tick() {
      const [metricsRes, requestsRes, replayRes, shardsRes] = await Promise.all([
        getMetrics(modeRef.current),
        getLiveRequests(),
        getReplayStatus(),
        getShardStats()
      ])
      if (!alive) return

      setMetrics(metricsRes)
      setRequests((prev) => [...requestsRes.data, ...prev].slice(0, 40))
      setReplay(replayRes)
      setShards(shardsRes)
      if (metricsRes.data) setHistory((prev) => {
        const last = prev[prev.length - 1] || {}
        const point = {
          tick: (prev[prev.length - 1]?.tick ?? 0) + 1,
          origin: metricsRes.data.mode === 'origin' ? metricsRes.data.latencyMs : last.origin,
          edge: metricsRes.data.mode === 'edge' ? metricsRes.data.latencyMs : last.edge
        }
        return [...prev, point].slice(-40)
      })
    }

    tick()
    const id = setInterval(tick, TICK_MS)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [])

  async function handleToggle(nextMode) {
    setMode(nextMode)
    await setEngineMode(nextMode)
  }

  const latency = metrics?.data?.latencyMs ?? '—'
  const cacheHitPct = metrics?.data?.cacheHitRate == null
    ? '—'
    : Math.round(metrics.data.cacheHitRate * 100)
  const bandwidthSaved = metrics?.data?.bandwidthSavedBytes == null
    ? '—'
    : formatBytes(metrics.data.bandwidthSavedBytes)

  const lastPoint = history[history.length - 1]
  const delta =
    lastPoint && lastPoint.origin != null && lastPoint.edge != null
      ? Math.round(lastPoint.origin - lastPoint.edge)
      : null

  return (
    <div className="min-h-screen bg-ink-950 px-6 py-8 text-parchment sm:px-10">
      <div className="mx-auto flex max-w-5xl flex-col gap-8">
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-ink-700 pb-6">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">
              Replay Engine <span className="text-ink-500">/ Edge vs. Origin</span>
            </h1>
            <p className="mt-1 max-w-md text-sm text-ink-500">
              Same recorded traffic, replayed twice. One switch decides which path it takes.
            </p>
          </div>
          <EngineToggle mode={mode} onChange={handleToggle} />
        </header>

        <section className="grid grid-cols-1 gap-4 sm:grid-cols-[1.3fr_1fr_1fr_1fr]">
          <div className="flex flex-col justify-between gap-3 border border-ink-700 bg-ink-900/60 p-5">
            <div className="flex items-center justify-between">
              <span className="font-mono text-[11px] uppercase tracking-wider text-ink-500">
                Current latency
              </span>
              {metrics && <SourceBadge source={metrics.source} />}
            </div>
            <div className="flex items-baseline gap-2">
              <span
                className={`font-mono text-5xl font-semibold tabular-nums ${
                  mode === 'edge' ? 'text-signal-400' : 'text-origin-400'
                }`}
              >
                {latency}
              </span>
              <span className="text-base text-ink-500">ms</span>
            </div>
            <p className="text-xs text-ink-500">
              {delta !== null
                ? mode === 'edge'
                  ? `${delta} ms faster than origin-direct`
                  : `${Math.abs(delta)} ms slower than edge-active`
                : 'A measured run for both modes is required for comparison'}
            </p>
          </div>

          <StatReadout
            label="Cache hit rate"
            value={cacheHitPct}
            unit="%"
            hint={metrics?.data ? 'Measured by replay telemetry' : 'No measured run available'}
            source={metrics?.source}
          />
          <StatReadout
            label="Bandwidth saved"
            value={bandwidthSaved}
            hint={metrics?.data ? 'Measured by replay telemetry' : 'No measured run available'}
            source={metrics?.source}
          />
          <StatReadout
            label="Requests replayed"
            value={replay?.data?.requests_replayed ?? '—'}
            unit={`/ ${replay?.data?.requests_total ?? '—'}`}
            hint="From the k6 replay run"
            source={replay?.source}
          />
        </section>

        <section className="border border-ink-700 bg-ink-900/40 p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-mono text-[11px] uppercase tracking-wider text-ink-500">
              Latency over time
            </h2>
            <div className="flex items-center gap-4 text-xs">
              <span className="flex items-center gap-1.5 text-origin-400">
                <span className="h-2 w-2 rounded-full bg-origin-500" /> Origin direct
              </span>
              <span className="flex items-center gap-1.5 text-signal-400">
                <span className="h-2 w-2 rounded-full bg-signal-500" /> Edge active
              </span>
            </div>
          </div>
          <LatencyChart data={history} />
        </section>

        <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="border border-ink-700 bg-ink-900/40 p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-mono text-[11px] uppercase tracking-wider text-ink-500">
                Shard activity
              </h2>
              {shards && <SourceBadge source={shards.source} />}
            </div>
            <RequestHeatmap values={shards?.data?.shards?.map((shard) => shard.connection_count) ?? []} mode={mode} />
            <p className="mt-3 text-xs text-ink-500">
              {shards?.data?.shard_count ?? '—'} shards · peak concurrency{' '}
              {shards?.data?.peak_concurrency ?? '—'}
            </p>
          </div>

          <div className="border border-ink-700 bg-ink-900/40 p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-mono text-[11px] uppercase tracking-wider text-ink-500">
                Live request feed
              </h2>
            </div>
            <RequestFeed requests={requests} />
          </div>
        </section>

        <footer className="border-t border-ink-700 pt-4 text-xs text-ink-500">
          Panels marked <span className="text-signal-400">live</span> read directly from the
          replay pipeline's output. Panels show <span className="text-origin-400">unavailable</span>{' '}
          until measured k6 telemetry exists; no synthetic values are shown.
        </footer>
      </div>
    </div>
  )
}
