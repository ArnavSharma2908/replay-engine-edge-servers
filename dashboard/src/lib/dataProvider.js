// Single seam between the dashboard and the local replay telemetry API.

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

async function tryFetch(path, options) {
  if (!API_BASE) return null
  try {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 1500)
    const res = await fetch(`${API_BASE}${path}`, { ...options, signal: controller.signal })
    clearTimeout(timeout)
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

export async function setEngineMode(mode) {
  return { source: 'local', data: { mode } }
}

// Advances the simulation exactly once per call — call this once per tick from the
// UI's poll loop so every panel (metrics, heatmap, request feed) stays in lockstep.
export async function getMetrics(mode) {
  const real = await tryFetch(`/api/metrics?mode=${mode}`)
  return real ? { source: 'live', data: real } : { source: 'unavailable', data: null }
}

export async function getLiveRequests() {
  const real = await tryFetch('/api/requests/live?limit=20')
  return real ? { source: 'live', data: real.requests } : { source: 'unavailable', data: [] }
}

export async function getReplayStatus() {
  const real = await tryFetch('/api/replay/status')
  return real ? { source: 'live', data: real } : { source: 'unavailable', data: null }
}

export async function getShardStats() {
  const real = await tryFetch('/api/shards')
  return real ? { source: 'live', data: real } : { source: 'unavailable', data: null }
}
