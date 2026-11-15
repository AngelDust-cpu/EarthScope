/**
 * Typed mirror of docs/API.md. The backend owns these shapes; this file only
 * restates the published contract for the client.
 */

export const VARIABLES = ['temperature', 'precipitation'] as const
export type Variable = (typeof VARIABLES)[number]

export const VARIABLE_LABEL: Record<Variable, string> = {
  temperature: 'Temperature',
  precipitation: 'Precipitation',
}

export const VARIABLE_PARAMETER: Record<Variable, 'T2M' | 'PRECTOTCORR'> = {
  temperature: 'T2M',
  precipitation: 'PRECTOTCORR',
}

export const VARIABLE_UNIT: Record<Variable, string> = {
  temperature: '°C',
  precipitation: 'mm',
}

export const VARIABLE_AGGREGATION: Record<Variable, 'mean' | 'sum'> = {
  temperature: 'mean',
  precipitation: 'sum',
}

export interface Coverage {
  min_year: number
  max_year: number
  min_years: number
}

export interface SeriesPoint {
  year: number
  value: number | null
  valid_days: number
  expected_days: number
  fitted: number | null
}

export interface Trend {
  years_used: number
  per_decade: number
  ci95_low: number
  ci95_high: number
  p_value: number
  significant: boolean
  r_squared: number
}

export interface TrendSource {
  name: string
  parameter: 'T2M' | 'PRECTOTCORR'
  daily_unit: 'C' | 'mm/day'
  dataset: string
  time_standard: 'UTC'
  request_url: string
  retrieved_at: string
  cached: boolean
}

export interface TrendResponse {
  latitude: number
  longitude: number
  variable: Variable
  start_year: number
  end_year: number
  unit: string
  aggregation: 'mean' | 'sum'
  series: SeriesPoint[]
  trend: Trend | null
  source: TrendSource
  warnings: string[]
}

export interface CompareResponse {
  point_a: TrendResponse
  point_b: TrendResponse
  comparison: {
    method: 'paired_annual_difference_descriptive'
    years_used: number
    slope_difference_per_decade: number | null
    ci95_low: number | null
    ci95_high: number | null
    p_value: number | null
    inferential: false
    note: string
  }
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
  }
}

/** Thrown for any non-successful /api response, carrying the backend's error envelope. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail?: string

  constructor(status: number, code: string, message: string, detail?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.detail = detail
  }
}
