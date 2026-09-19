function statusColor(status) {
  if (status >= 500) return 'text-red-400'
  if (status >= 400) return 'text-amber-300'
  if (status === 304) return 'text-signal-400'
  return 'text-ink-400'
}

function cacheColor(cache) {
  if (cache === 'HIT') return 'text-signal-400'
  if (cache === 'MISS') return 'text-origin-400'
  return 'text-ink-600'
}

export default function RequestFeed({ requests }) {
  return (
    <div className="h-56 overflow-y-auto pr-1 font-mono text-xs leading-relaxed">
      {requests.length === 0 && (
        <div className="py-2 text-ink-500">Waiting for replayed requests…</div>
      )}
      {requests.map((r) => (
        <div
          key={r.id}
          className="flex items-center gap-3 border-b border-ink-800/80 py-1.5 last:border-none"
        >
          <span className="w-10 shrink-0 text-ink-500">{r.method}</span>
          <span className="flex-1 truncate text-parchment/90">{r.path}</span>
          <span className={`w-8 shrink-0 text-right ${statusColor(r.status)}`}>{r.status}</span>
          <span className="w-14 shrink-0 text-right text-ink-500">{r.latencyMs}ms</span>
          <span className={`w-9 shrink-0 text-right ${cacheColor(r.cache)}`}>{r.cache}</span>
        </div>
      ))}
    </div>
  )
}
