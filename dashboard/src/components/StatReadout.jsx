import SourceBadge from './SourceBadge.jsx'

export default function StatReadout({ label, value, unit, hint, source, accent = 'signal' }) {
  const accentClass = accent === 'signal' ? 'text-signal-400' : 'text-origin-400'
  return (
    <div className="flex flex-col gap-2 border border-ink-700 bg-ink-900/60 p-4">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[11px] uppercase tracking-wider text-ink-500">
          {label}
        </span>
        {source && <SourceBadge source={source} />}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className={`font-mono text-3xl font-semibold tabular-nums ${accentClass}`}>
          {value}
        </span>
        {unit && <span className="text-sm text-ink-500">{unit}</span>}
      </div>
      {hint && <span className="text-xs text-ink-500">{hint}</span>}
    </div>
  )
}
