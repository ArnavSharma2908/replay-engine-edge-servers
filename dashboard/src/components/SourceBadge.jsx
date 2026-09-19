export default function SourceBadge({ source }) {
  const isLive = source === 'live'
  const label = isLive ? 'live' : source === 'simulated' ? 'simulated' : 'unavailable'
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-[11px] tracking-wide text-ink-500">
      <span
        className={`h-1.5 w-1.5 rounded-full ${
          isLive ? 'bg-signal-500 pulse-dot' : 'bg-origin-400'
        }`}
      />
      {label}
    </span>
  )
}
