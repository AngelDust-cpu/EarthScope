export function Spinner({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={className ?? 'h-4 w-4 animate-spin'}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
    >
      <circle cx="12" cy="12" r="9" className="opacity-20" />
      <path d="M21 12a9 9 0 0 0-9-9" />
    </svg>
  )
}

/** Dark skeleton block used for the initial loading state. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={className ?? 'h-4 w-full animate-pulse rounded bg-raised'}
    />
  )
}

const toneClasses = {
  error: 'border-line border-l-rose-300/70 bg-card text-ink',
  warning: 'border-line border-l-warning bg-card text-ink',
  info: 'border-line border-l-control bg-card text-ink',
} as const

export function Alert({
  tone = 'error',
  title,
  children,
  action,
}: {
  tone?: 'error' | 'warning' | 'info'
  title: string
  children?: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={`rounded border px-4 py-3 text-sm ${toneClasses[tone]}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold">{title}</p>
          {children ? (
            <div className="mt-1 text-[13px] leading-relaxed opacity-90">{children}</div>
          ) : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  )
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-[220px] flex-col items-center justify-center gap-2 rounded border border-dashed border-line bg-panel px-6 py-10 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children ? <div className="max-w-sm text-xs leading-relaxed text-muted">{children}</div> : null}
    </div>
  )
}
