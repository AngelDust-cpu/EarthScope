import { TrendChart } from './TrendChart'
import type { ExtremesResponse } from '../lib/extremes'

/** A separate axis keeps modeled frequency distinct from the raw annual count. */
export function ExtremesRateChart({ data }: { data: ExtremesResponse }) {
  if (data.trend?.p_value == null || !data.series.some(point => point.fitted !== null)) return null
  const unit = 'дней событий на 365 дней'
  return <section className="min-w-0 space-y-2 rounded border border-line bg-card p-4">
    <h3 className="font-semibold">Частота событий и линия тренда</h3>
    <p className="text-xs text-muted">Ось показывает частоту на 365 дней. Подгонка линейная и может выходить за физические границы; её нельзя экстраполировать.</p>
    <div className="h-[300px] min-w-0">
      <TrendChart
        series={data.series.map(row => ({ ...row, value: row.fraction === null ? null : 365 * row.fraction }))}
        variable="temperature"
        unit={unit}
        observationLabel="Частота событий"
      />
    </div>
  </section>
}
