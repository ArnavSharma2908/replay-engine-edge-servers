const REQUEST_PATHS = [
  { method: 'GET', path: '/api/catalog/search?q=wireless-headphones' },
  { method: 'GET', path: '/static/js/app.bundle.js' },
  { method: 'POST', path: '/api/cart/items' },
  { method: 'GET', path: '/api/users/profile' },
  { method: 'GET', path: '/images/products/thumb-2291.webp' },
  { method: 'POST', path: '/api/checkout/session' },
  { method: 'GET', path: '/api/recommendations/home' },
  { method: 'GET', path: '/api/inventory/status' },
  { method: 'GET', path: '/static/css/main.css' },
  { method: 'GET', path: '/api/orders/history' }
]

const AVG_OBJECT_BYTES = 46 * 1024
const REPLAY_TOTAL = 12000

function gaussianJitter(mean, stdDev) {
  const u1 = Math.random() || 1e-6
  const u2 = Math.random()
  const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2)
  return Math.max(4, mean + z * stdDev)
}

function randomId() {
  return Math.random().toString(16).slice(2, 10)
}

export function createMockEngine() {
  const state = {
    mode: 'origin',
    ticksInMode: 0,
    bandwidthSavedBytes: 0,
    requestsReplayedTotal: 0,
    lastRequestBatch: [],
    shardCycle: 0
  }

  function setMode(mode) {
    if (mode !== state.mode) {
      state.mode = mode
      state.ticksInMode = 0
    }
  }

  function warmupProgress() {
    return Math.min(1, state.ticksInMode / 12)
  }

  function step() {
    state.ticksInMode += 1
    const warm = warmupProgress()
    const mean = state.mode === 'origin' ? 178 : 178 - 140 * warm
    const latencyMs = Math.round(gaussianJitter(mean, state.mode === 'origin' ? 24 : 10))
    const cacheHitRate = state.mode === 'origin'
      ? 0
      : Math.min(0.94, 0.05 + warm * 0.85 + (Math.random() - 0.5) * 0.03)
    const requestsThisTick = 3 + Math.floor(Math.random() * 6)
    state.requestsReplayedTotal = (state.requestsReplayedTotal + requestsThisTick) % REPLAY_TOTAL
    if (state.mode === 'edge') {
      state.bandwidthSavedBytes += cacheHitRate * AVG_OBJECT_BYTES * requestsThisTick
    }

    state.lastRequestBatch = Array.from({ length: 2 + Math.floor(Math.random() * 3) }, () => {
      const request = REQUEST_PATHS[Math.floor(Math.random() * REQUEST_PATHS.length)]
      const hit = state.mode === 'edge' && Math.random() < cacheHitRate
      return {
        id: randomId(),
        method: request.method,
        path: request.path,
        status: hit ? 304 : 200,
        latencyMs: Math.round(hit ? latencyMs * 0.2 : latencyMs),
        cache: state.mode === 'edge' ? (hit ? 'HIT' : 'MISS') : '—'
      }
    })

    return {
      mode: state.mode,
      latencyMs,
      cacheHitRate,
      bandwidthSavedBytes: Math.round(state.bandwidthSavedBytes),
      requestsThisTick,
      timestamp: Date.now()
    }
  }

  function getShardSnapshot() {
    state.shardCycle += 1
    const statuses = ['queued', 'replaying', 'done']
    return {
      shard_count: 4,
      peak_concurrency: 4,
      shards: [1, 3, 1, 2].map((connection_count, id) => ({
        id,
        connection_count,
        status: statuses[(state.shardCycle + id) % statuses.length]
      }))
    }
  }

  return {
    setMode,
    step,
    getRequestFeedBatch: () => state.lastRequestBatch,
    getReplayStatus: () => ({
      running: true,
      requests_replayed: state.requestsReplayedTotal,
      requests_total: REPLAY_TOTAL
    }),
    getShardSnapshot
  }
}
