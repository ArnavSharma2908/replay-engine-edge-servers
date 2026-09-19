export default function EngineToggle({ mode, onChange }) {
  const isEdge = mode === 'edge'
  return (
    <button
      onClick={() => onChange(isEdge ? 'origin' : 'edge')}
      aria-pressed={isEdge}
      className="group flex items-center gap-4 rounded-sm border border-ink-600 bg-ink-900 px-5 py-3 transition-colors hover:border-ink-500 focus-visible:outline-signal-500"
    >
      <span className="flex flex-col items-start">
        <span className="font-mono text-[11px] uppercase tracking-wider text-ink-500">
          Route
        </span>
        <span
          className={`text-sm font-semibold transition-colors ${
            isEdge ? 'text-signal-400' : 'text-origin-400'
          }`}
        >
          {isEdge ? 'Edge Active' : 'Origin Direct'}
        </span>
      </span>
      <span
        className={`relative h-7 w-14 shrink-0 rounded-full border transition-colors ${
          isEdge ? 'border-signal-500 bg-signal-500/15' : 'border-origin-500 bg-origin-500/15'
        }`}
      >
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full transition-all duration-200 ${
            isEdge ? 'left-8 bg-signal-500' : 'left-0.5 bg-origin-500'
          }`}
        />
      </span>
    </button>
  )
}
