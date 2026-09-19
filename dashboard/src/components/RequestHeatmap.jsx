export default function RequestHeatmap({ values, mode }) {
  const accentRgb = mode === 'edge' ? '255,176,0' : '74,144,217'
  const maximum = Math.max(...values, 1)
  return (
    <div className="grid grid-cols-8 gap-1">
      {values.map((v, i) => (
        <div
          key={i}
          className="aspect-square rounded-[2px] transition-colors duration-500"
          style={{ backgroundColor: `rgba(${accentRgb}, ${(0.12 + (v / maximum) * 0.85).toFixed(2)})` }}
        />
      ))}
    </div>
  )
}
