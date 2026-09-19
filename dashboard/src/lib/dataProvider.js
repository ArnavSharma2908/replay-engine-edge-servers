// The two pipeline-backed calls use the local API. The comparison layer remains
// a deliberately local simulation until a real edge/cache measurement exists.
import { createMockEngine } from './mockEngine'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'
const engine = createMockEngine()

async function tryFetch(path, options) {
  try {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 1500)
    const response = await fetch(`${API_BASE}${path}`, { ...options, signal: controller.signal })
    clearTimeout(timeout)
    if (!response.ok) return null
    return await response.json()
  } catch {
    return null
  }
}

export async function setEngineMode(mode) {
  engine.setMode(mode)
  return { source: 'simulated', data: { mode } }
}

export async function getMetrics(mode) {
  const data = engine.step()
  return { source: 'simulated', data: { ...data, mode } }
}

export async function getLiveRequests() {
  return { source: 'simulated', data: engine.getRequestFeedBatch() }
}

export async function getReplayStatus() {
  const live = await tryFetch('/api/replay/status')
  return live
    ? { source: 'live', data: live }
    : { source: 'simulated', data: engine.getReplayStatus() }
}

export async function getShardStats() {
  const live = await tryFetch('/api/shards')
  return live
    ? { source: 'live', data: live }
    : { source: 'simulated', data: engine.getShardSnapshot() }
}
