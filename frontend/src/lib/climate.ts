import { apiUrl } from './apiBase'
import type { RequestOptions, TrendQuery } from './api'
import { ApiError, type SeriesPoint, type Trend, type TrendResponse } from './types'

export type { TrendQuery } from './api'
export type Season = 'winter' | 'spring' | 'summer' | 'autumn'
export type Month = 'm01' | 'm02' | 'm03' | 'm04' | 'm05' | 'm06' | 'm07' | 'm08' | 'm09' | 'm10' | 'm11' | 'm12'
export type Period = Season | Month | 'all'
export interface AnomalyQuery extends TrendQuery { baselineStartYear: number; baselineEndYear: number }
export interface SeasonalQuery extends TrendQuery { period: Period }
type Common = Omit<TrendResponse, 'series' | 'trend'>
export interface AnomalyResponse extends Common {
  baseline_start_year: number
  baseline_end_year: number
  baseline_mean: number | null
  baseline_complete_years: number
  series: (Omit<SeriesPoint, 'fitted'> & { anomaly: number | null })[]
}
export interface SeasonalResponse extends Common {
  period: Period
  periods: { period: string; series: SeriesPoint[]; trend: Trend | null }[]
}

function params(query: TrendQuery): URLSearchParams {
  return new URLSearchParams({
    latitude: String(query.latitude), longitude: String(query.longitude),
    variable: query.variable, start_year: String(query.startYear), end_year: String(query.endYear),
  })
}

async function request<T>(route: string, search: URLSearchParams, options: RequestOptions): Promise<T> {
  const response = await fetch(apiUrl(`/api/${route}?${search}`), {
    headers: { Accept: 'application/json' }, signal: options.signal,
  })
  if (!response.ok) {
    let code = 'HTTP_ERROR', message = `HTTP ${response.status}`
    try {
      const body = await response.json()
      code = body?.error?.code ?? code
      message = body?.error?.message ?? message
    } catch { /* Preserve HTTP status for non-JSON errors. */ }
    throw new ApiError(response.status, code, message)
  }
  try { return await response.json() as T }
  catch { throw new ApiError(502, 'INVALID_RESPONSE', 'Invalid JSON') }
}

function validCommon(data: Common): boolean {
  return !!data && ['temperature', 'precipitation'].includes(data.variable) &&
    typeof data.unit === 'string' && !!data.source &&
    typeof data.source.request_url === 'string' && Array.isArray(data.warnings)
}
const nullableNumber = (value: unknown) => value === null || (typeof value === 'number' && Number.isFinite(value))
const validPoint = (p: Omit<SeriesPoint, 'fitted'>) => !!p && Number.isInteger(p.year) &&
  nullableNumber(p.value) && Number.isInteger(p.valid_days) && Number.isInteger(p.expected_days)

export async function fetchAnomalies(query: AnomalyQuery, options: RequestOptions): Promise<AnomalyResponse> {
  const search = params(query)
  search.set('baseline_start_year', String(query.baselineStartYear))
  search.set('baseline_end_year', String(query.baselineEndYear))
  const data = await request<AnomalyResponse>('anomalies', search, options)
  if (!validCommon(data) || !Array.isArray(data.series) ||
    !data.series.every(p => validPoint(p) && nullableNumber(p.anomaly)) ||
    !nullableNumber(data.baseline_mean) || !Number.isInteger(data.baseline_complete_years) ||
    !Number.isInteger(data.baseline_start_year) || !Number.isInteger(data.baseline_end_year)) {
    throw new ApiError(502, 'INVALID_RESPONSE', 'Invalid anomaly response')
  }
  return data
}

export async function fetchSeasonal(query: SeasonalQuery, options: RequestOptions): Promise<SeasonalResponse> {
  const search = params(query)
  search.set('period', query.period)
  const data = await request<SeasonalResponse>('seasonal', search, options)
  if (!validCommon(data) || !Array.isArray(data.periods) || !data.periods.length ||
    !data.periods.every(p => typeof p.period === 'string' && Array.isArray(p.series) &&
      p.series.every(row => validPoint(row) && nullableNumber(row.fitted)) &&
      (p.trend === null || (p.trend && [p.trend.per_decade, p.trend.ci95_low, p.trend.ci95_high,
        p.trend.p_value, p.trend.years_used].every(Number.isFinite))))) {
    throw new ApiError(502, 'INVALID_RESPONSE', 'Invalid seasonal response')
  }
  return data
}
