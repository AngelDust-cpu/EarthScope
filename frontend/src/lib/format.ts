import { ru, unitLabel, variableLabel } from './ru'
import type { TrendResponse } from './types'

export function formatDecimal(value: number, digits: number): string {
  return value.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: false })
}

const magnitudeDigits = (v: number): string =>
  formatDecimal(v, Math.abs(v) < 1 && v !== 0 ? 3 : Math.abs(v) < 10 ? 2 : 1)

export function formatValue(value: number | null, unit: string): string {
  if (value === null || !Number.isFinite(value)) return ru.number.missing
  return unit ? `${magnitudeDigits(value)} ${unitLabel(unit)}` : magnitudeDigits(value)
}

export function formatSigned(value: number, unit: string): string {
  if (!Number.isFinite(value)) return '—'
  const sign = value > 0 ? '+' : value < 0 ? '−' : ''
  const suffix = unit ? ` ${unitLabel(unit)} ${ru.number.perDecade}` : ''
  return `${sign}${magnitudeDigits(Math.abs(value))}${suffix}`
}

export function formatInterval(low: number, high: number, unit: string): string {
  return `${magnitudeDigits(low)} … ${magnitudeDigits(high)} ${unitLabel(unit)} ${ru.number.perDecade}`
}

/** Metric trend, e.g. +0.42 °C/decade or −11.3 mm/decade. */
export function formatTrend(value: number, unit: string): string {
  return formatSigned(value, unit)
}

/** Metric endpoint bounds, e.g. −0.10 to 0.31 °C/decade. */
export function formatMetricRange(low: number, high: number, unit: string): string {
  return formatInterval(low, high, unit)
}

export function formatP(p: number): string {
  if (!Number.isFinite(p)) return '—'
  if (p < 0.001) return '< 0,001'
  return formatDecimal(p, 3)
}

export function formatR2(r2: number): string {
  return Number.isFinite(r2) ? formatDecimal(r2, 3) : ru.number.missing
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat('ru-RU').format(value)
}

export function formatCoordinate(value: number, digits = 3): string {
  return formatDecimal(value, digits)
}

export function formatUtcTimestamp(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return `${new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(date)} UTC`
}

export function formatPercent(part: number, whole: number): string {
  if (!Number.isFinite(part) || !Number.isFinite(whole) || whole <= 0) return '0%'
  return `${Math.round((part / whole) * 100)}%`
}

export function csvFilename(response: TrendResponse): string {
  return `EarthScope_${variableLabel(response.variable).replaceAll(' ', '_')}_${response.latitude.toFixed(3)}_${response.longitude.toFixed(3)}_${response.start_year}-${response.end_year}.csv`
}

/** Russian headings and locale-independent numeric cells for data interchange. */
export function toCsv(response: TrendResponse): string {
  const escape = (v: string | number): string => {
    const s = String(v)
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [ru.csv.columns.join(',')]
  for (const point of response.series) {
    lines.push(
      [
        point.year,
        point.value === null ? '' : point.value,
        unitLabel(response.unit),
        response.aggregation === 'mean' ? ru.chart.mean : ru.chart.total,
        point.valid_days,
        point.expected_days,
        point.fitted === null ? '' : point.fitted,
        response.latitude,
        response.longitude,
        variableLabel(response.variable),
      ]
        .map(escape)
        .join(','),
    )
  }
  return `\uFEFF${lines.join('\r\n')}\r\n`
}
