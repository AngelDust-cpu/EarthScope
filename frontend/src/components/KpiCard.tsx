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
        'flex min-w-0 flex-col gap-2 rounded border border-line bg-card p-4',
        featured && 'border-t-focus',
      )}
    >
      <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-muted">{label}</span>
      <p className={cn('flex flex-wrap items-baseline gap-x-2 gap-y-1 font-mono text-lg font-medium leading-tight tabular-nums tracking-tight text-ink', featured && 'text-2xl')}>
        {value}
        {unit ? <span className="text-xs font-normal text-muted">{unit}</span> : null}
      </p>
      <p className="text-xs leading-snug text-muted">{caption}</p>
    </div>
  )
}
