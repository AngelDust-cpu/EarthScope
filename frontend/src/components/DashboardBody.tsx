import { useMemo } from 'react'
import { analysisPdfRequest } from '../lib/pdf'
import { PdfDownloadButton } from './PdfDownloadButton'
import { interpretTrend } from '../lib/detective'

import type { AsyncState } from '../hooks/useAsyncData'
import { downloadCsv } from '../lib/api'
import {
  csvFilename,
  formatCount,
  formatCoordinate,
  formatP,
  formatR2,
  formatSigned,
  formatUtcTimestamp,
  formatValue,
  toCsv,
} from '../lib/format'
import { ru, variableLabel, unitLabel, displayError } from '../lib/ru'
import { type Coverage, type TrendResponse } from '../lib/types'
import { DataTable } from './DataTable'
import { KpiCard } from './KpiCard'
import { TrendChart } from './TrendChart'
import { Button } from './ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'
import { Alert, EmptyState, Skeleton, Spinner } from './ui/feedback'

const r = ru.results

export interface DashboardBodyProps {
  coverage: Coverage | null
  state: AsyncState<TrendResponse>
  metaDescription: string
  showInterpretation?: boolean
  onRetry: () => void
}

function KpiSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {[0, 1, 2, 3].map((index) => (
        <div key={index} className="rounded border border-line bg-card p-4">
          <Skeleton className="h-2.5 w-20" />
          <Skeleton className="mt-3 h-7 w-24" />
          <Skeleton className="mt-3 h-2 w-full" />
        </div>
      ))}
    </div>
  )
}

function ChartSkeleton({ label = r.loading }: { label?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={label}
      className="flex h-full min-h-[300px] flex-col justify-between gap-4 p-5"
    >
      <div className="flex items-center gap-2 text-xs text-muted">
        <Spinner className="h-4 w-4 animate-spin text-ink" />
        {label}
      </div>
      <div className="grid flex-1 place-items-center rounded border border-dashed border-line bg-base" aria-hidden="true">
        <Spinner className="size-5 animate-spin text-muted" />
      </div>
      <div className="flex flex-col gap-2" aria-hidden="true">
        <Skeleton className="h-2 w-1/2" />
        <Skeleton className="h-2 w-1/3" />
      </div>
    </div>
  )
}

function KpiRow({ response }: { response: TrendResponse }) {
  const { trend, series, unit, variable } = response

  const complete = series.filter((point) => point.value !== null).length
  const mean = useMemo(() => {
    const values = series
      .map((point) => point.value)
      .filter((value): value is number => value !== null)
    if (values.length === 0) return null
    return values.reduce((sum, value) => sum + value, 0) / values.length
  }, [series])

  return (
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <KpiCard
        label={r.trend}
        value={trend ? formatSigned(trend.per_decade, '') : r.noFit}
        unit={trend ? `${unitLabel(unit)} ${r.decade}` : undefined}
        caption={
          trend
            ? r.fitHint(trend.years_used)
            : r.noFitHint
        }
        featured
      />
      <KpiCard
        label={r.interval}
          value={trend ? `${formatCoordinate(trend.ci95_low, 2)} … ${formatCoordinate(trend.ci95_high, 2)}` : '—'}
          unit={trend ? `${unitLabel(unit)} ${r.decade}` : undefined}
        caption={
          trend
            ? r.intervalHint
            : r.unavailable
        }
      />
      <KpiCard
        label={r.significance}
        value={trend ? (trend.significant ? r.significant : r.inconclusive) : '—'}
        caption={
          trend
            ? `p ${formatP(trend.p_value)} · α 0,05 · R² ${formatR2(trend.r_squared)}.`
            : r.noTest
        }
      />
      <KpiCard
        label={r.mean}
        value={mean === null ? r.noData : formatValue(mean, unit)}
        caption={`${r.complete}: ${formatCount(complete)}/${formatCount(series.length)} · ${
          variable === 'precipitation' ? r.precipitationMethod : r.temperatureMethod
        }.`}
      />
    </div>
  )
}

function SourceDetails({ response }: { response: TrendResponse }) {
  const rows: [string, string][] = [
    [r.dataset, response.source.dataset],
    [r.parameter, response.source.parameter],
    [r.dailyUnit, unitLabel(response.source.daily_unit)],
    [r.time, response.source.time_standard],
    [r.retrieved, formatUtcTimestamp(response.source.retrieved_at)],
    [r.cache, response.source.cached ? r.cached : r.fetched],
    [r.point, `${formatCoordinate(response.latitude, 3)}, ${formatCoordinate(response.longitude, 3)}`],
    [r.url, response.source.request_url],
  ]

  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2.5 text-xs sm:grid-cols-2 xl:grid-cols-1">
      {rows.map(([label, value]) => (
        <div key={label} className="flex min-w-0 flex-col gap-0.5">
          <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted">{label}</dt>
          <dd className="break-words font-mono text-[11px] leading-snug text-secondary" title={value}>
            {value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

export function DashboardBody({ coverage, state, metaDescription, showInterpretation = false, onRetry }: DashboardBodyProps) {
  const { data, loading, error } = state
  const coverageLabel = coverage ? `${coverage.min_year}–${coverage.max_year}` : '—'

  if (error && !data) {
    return (
      <Card>
        <CardContent className="flex flex-col gap-4 pt-5">
          <Alert
            tone="error"
            title={r.error}
            action={
              <Button type="button" variant="outline" size="sm" onClick={onRetry}>
                {r.retry}
              </Button>
            }
          >
            {displayError(error)}
          </Alert>
        </CardContent>
      </Card>
    )
  }

  if (!data) {
    if (!loading) return <EmptyState title={r.empty}>{r.emptyHint}</EmptyState>
    return (
      <div className="analysis-output flex flex-col gap-4">
        <KpiSkeleton />
        <Card className="overflow-hidden">
          <CardContent className="p-0">
            <ChartSkeleton />
          </CardContent>
        </Card>
      </div>
    )
  }

  const gapCount = data.series.filter((point) => point.value === null).length
  const filename = csvFilename(data)
  const [conclusion, ...methodNotes] = showInterpretation ? interpretTrend(data) : []

  return (
    <div className="analysis-output flex flex-col gap-4">
      {error && (
        <Alert tone="error" title={r.error} action={<Button type="button" variant="outline" size="sm" onClick={onRetry} disabled={loading}>{r.retry}</Button>}>
          {displayError(error)}
        </Alert>
      )}
      <div className="analysis-metrics"><KpiRow response={data} /></div>

      {data.warnings.length > 0 ? (
        <Alert tone="warning" title={r.dataCoverage}>
          {r.warnings}
          {gapCount > 0 && <p className="mt-1">{r.gapYears(data.series.filter(point => point.value === null).map(point => point.year))}</p>}
        </Alert>
      ) : null}

      {data.trend === null ? (
        <Alert tone="info" title={r.noTrend}>
          {r.noTrendHint}
        </Alert>
      ) : null}

      <Card className="analysis-chart">
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-3 space-y-0">
          <div className="min-w-0">
            <CardTitle>
              {variableLabel(data.variable)} · {data.start_year}–{data.end_year}
            </CardTitle>
            <CardDescription className="mt-1 max-w-2xl">{metaDescription}</CardDescription>
          </div>
          <div className="flex min-w-0 flex-wrap items-start gap-2">
            <PdfDownloadButton request={analysisPdfRequest(data)} />
            <span role="status" className="inline-flex items-center gap-1.5 rounded border border-line px-2 py-1 font-mono text-xs text-secondary">
              {loading ? (
                <Spinner className="h-3 w-3 animate-spin" />
              ) : (
                <span aria-hidden="true" className="size-1.5 bg-muted" />
              )}
               {data.source.cached ? r.cached : r.fetched}
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => downloadCsv(filename, toCsv(data))}
            >
              {ru.csv.export}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="h-[285px] w-full sm:h-[360px]">
            <TrendChart series={data.series} variable={data.variable} unit={data.unit} />
          </div>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2 font-mono text-[10px] uppercase tracking-[0.12em] text-muted">
            <span>
              {r.years}: {formatCount(data.series.length)} · {r.gaps}: {formatCount(gapCount)}
            </span>
            <span>{r.coverage}: {coverageLabel} · {r.calendar}</span>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle>{r.annual}</CardTitle>
            <CardDescription>
              {r.annualHint}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <DataTable response={data} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle>{r.source}</CardTitle>
            <CardDescription>{r.sourceHint}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <SourceDetails response={data} />
            <div className="h-px bg-raised" />
            <div className="flex flex-col gap-2 text-[11px] leading-relaxed text-muted">
              <p className="text-sm font-medium text-ink">{r.interpretation}</p>
              {conclusion && <p className="text-sm text-secondary">{conclusion}</p>}
              {methodNotes.length > 0 && <details>
                <summary className="cursor-pointer py-1 text-sm text-secondary">Неопределённость и допущения модели</summary>
                <ul className="space-y-2 py-2">{methodNotes.map(note => <li key={note}>{note}</li>)}</ul>
              </details>}
              <p>
                {r.method}
              </p>
              <p>
                {r.limitations}
              </p>
              <p>
                {data.variable === 'temperature' ? r.temperatureMethod : r.precipitationMethod} · {unitLabel(data.unit)}. {r.calendar}.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
