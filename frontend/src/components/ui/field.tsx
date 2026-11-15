import * as React from 'react'

import { cn } from '../../lib/cn'

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label
      className={cn(
        'text-xs font-medium text-secondary',
        className,
      )}
      {...props}
    />
  )
}

export function Field({
  label,
  htmlFor,
  hint,
  children,
  className,
}: {
  label: string
  htmlFor: string
  hint?: string
  children: React.ReactNode
  className?: string
}) {
  const linkedChild = hint && React.isValidElement<{ 'aria-describedby'?: string }>(children)
    ? React.cloneElement(children, {
      'aria-describedby': [children.props['aria-describedby'], `${htmlFor}-hint`].filter(Boolean).join(' '),
    })
    : children
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {linkedChild}
      {hint ? (
        <p id={`${htmlFor}-hint`} className="text-xs text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  )
}

export const inputClassName =
  'h-11 w-full rounded-md border border-control bg-base px-3 font-mono text-base text-ink tabular-nums outline-none transition-colors placeholder:text-muted hover:border-muted focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus aria-invalid:border-rose-300/80 disabled:opacity-50 sm:h-10 sm:text-sm'

export const TextInput = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn(inputClassName, className)} {...props} />
  ),
)
TextInput.displayName = 'TextInput'

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select
        className={cn(
          inputClassName,
          'cursor-pointer appearance-none bg-base pr-9 font-sans [&>option]:bg-base [&>option]:text-ink',
          className,
        )}
        {...props}
      >
        {children}
      </select>
      <svg
        aria-hidden="true"
        viewBox="0 0 20 20"
        className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      >
        <path d="M5 7.5 10 12.5 15 7.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  )
}
