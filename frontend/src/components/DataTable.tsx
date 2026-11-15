import { useMemo, useState } from 'react'

import { cn } from '../lib/cn'
import { formatValue } from '../lib/format'
import { ru, unitLabel } from '../lib/ru'
import type { TrendResponse } from '../lib/types'

type Filter = 'all' | 'complete' | 'gaps'

const FILTER_LABEL = ru.table.filters

/**
 * Year-by-year backing data. Gaps are shown as explicit `null` rows so missing
 * data is never presented as a value.
 */
export function DataTable({ response }: { response: TrendResponse }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [expanded, setExpanded] = useState(false)

  const rows = useMemo(() => {
    if (filter === 'complete') return response.series.filter((point) => point.value !== null)
    if (filter === 'gaps') return response.series.filter((point) => point.value === null)
    return response.series
  }, [response.series, filter])

  const visible = expanded ? rows : rows.slice(0, 8)

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="group" aria-label={ru.table.filterLabel} className="inline-flex flex-wrap rounded border border-line bg-base p-0.5">
          {(Object.keys(FILTER_LABEL) as Filter[]).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={filter === option}
              onClick={() => setFilter(option)}
              className={cn(
                'rounded px-2.5 py-1 font-mono text-[11px] uppercase tracking-[0.1em] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus',
                filter === option ? 'bg-raised text-ink' : 'text-muted hover:text-ink',
              )}
            >
              {FILTER_LABEL[option]}
            </button>
          ))}
        </div>
      </div>

      <div tabIndex={0} role="region" aria-label={ru.table.caption} className="max-h-64 overflow-auto rounded border border-line">
        <table className="w-full border-collapse text-left font-mono text-xs tabular-nums">
          <caption className="sr-only">
            {ru.table.caption}
          </caption>
          <thead className="sticky top-0 z-10 bg-card text-[10px] uppercase tracking-[0.12em] text-muted">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">{ru.table.year}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{ru.table.value} ({unitLabel(response.unit)})</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{ru.table.days}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{ru.table.fit}</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">{ru.table.status}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.06]">
            {visible.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-muted">
                  {ru.table.none}
                </td>
              </tr>
            ) : (
              visible.map((point) => {
                const complete = point.valid_days === point.expected_days && point.value !== null
                return (
                  <tr key={point.year} className="text-secondary odd:bg-panel">
                    <th scope="row" className="px-3 py-1.5 text-left font-normal text-ink">{point.year}</th>
                    <td className={cn('px-3 py-1.5 text-right', point.value === null && 'text-amber-300/80')}>
                      {formatValue(point.value, '')}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {point.valid_days}/{point.expected_days}
                    </td>
                    <td className="px-3 py-1.5 text-right text-muted">
                      {point.fitted === null ? '—' : formatValue(point.fitted, '')}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      <span className={complete ? 'text-ink' : 'text-warning'}>
                        {complete ? ru.table.complete : ru.table.partial}
                      </span>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {rows.length > 8 ? (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="self-start rounded font-mono text-[11px] uppercase tracking-[0.12em] text-ink underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          {expanded ? ru.table.showFirst : ru.table.showAll(rows.length)}
        </button>
      ) : null}
    </div>
  )
}
