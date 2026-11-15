import { cn } from '../lib/cn'

export interface KpiCardProps {
  label: string
  value: string
  unit?: string
  caption: string
  featured?: boolean
}

/**
 * One computed KPI. The caption names the computation and its data dependency so
 * the card never reads as an unsupported claim.
 */
export function KpiCard({ label, value, unit, caption, featured = false }: KpiCardProps) {
  return (
    <div
      className={cn(
        'kpi-card flex min-w-0 flex-col gap-2 rounded-lg border border-line bg-panel p-4',
        featured && 'kpi-card-featured border-t-focus bg-card',
      )}
    >
      <span className="text-xs font-medium text-muted">{label}</span>
      <p data-kpi-value className={cn('flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xl font-semibold leading-tight tabular-nums tracking-tight text-ink', featured && 'text-3xl')}>
        {value}
        {unit ? <span className="text-xs font-normal text-muted">{unit}</span> : null}
      </p>
      <p className="text-xs leading-snug text-muted">{caption}</p>
    </div>
  )
}
