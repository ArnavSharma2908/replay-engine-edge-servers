import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'

export default function LatencyChart({ data }) {
  return (
    <div className="h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="#2A2317" vertical={false} />
          <XAxis dataKey="tick" hide />
          <YAxis
            width={36}
            tick={{ fill: '#5C5138', fontSize: 11, fontFamily: 'IBM Plex Mono' }}
            axisLine={{ stroke: '#3A3121' }}
            tickLine={false}
            unit="ms"
          />
          <Tooltip
            contentStyle={{
              background: '#141109',
              border: '1px solid #3A3121',
              borderRadius: 2,
              fontFamily: 'IBM Plex Mono',
              fontSize: 12
            }}
            labelFormatter={() => ''}
          />
          <Line
            type="monotone"
            dataKey="origin"
            name="Origin direct"
            stroke="#4A90D9"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="edge"
            name="Edge active"
            stroke="#FFB000"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
