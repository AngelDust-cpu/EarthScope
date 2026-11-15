import { ApiError, type ApiErrorBody, type CompareResponse, type Coverage, type TrendResponse, type Variable } from './types'
import { apiUrl } from './apiBase'

export interface TrendQuery {
  latitude: number
  longitude: number
  variable: Variable
  startYear: number
  endYear: number
}

export interface RequestOptions {
  signal: AbortSignal
}

async function readError(response: Response): Promise<ApiError> {
  let message = `Request failed (HTTP ${response.status}).`
  let code = 'HTTP_ERROR'
  let detail: string | undefined
  try {
    const body = (await response.clone().json()) as Partial<ApiErrorBody>
    if (body?.error?.message) {
      message = body.error.message
      code = body.error.code ?? code
    }
  } catch {
    try {
      detail = (await response.text()).slice(0, 400) || undefined
    } catch {
      detail = undefined
    }
  }
  return new ApiError(response.status, code, message, detail)
}

export function trendUrl(query: TrendQuery): string {
  const params = new URLSearchParams({
    latitude: String(query.latitude),
    longitude: String(query.longitude),
    variable: query.variable,
    start_year: String(query.startYear),
    end_year: String(query.endYear),
  })
  return apiUrl(`/api/trend?${params.toString()}`)
}

export async function fetchCoverage(options: RequestOptions): Promise<Coverage> {
  const response = await fetch(apiUrl('/api/coverage'), {
    headers: { Accept: 'application/json' },
    signal: options.signal,
  })
  if (!response.ok) throw await readError(response)
  const data = (await response.json()) as Partial<Coverage>
  if (
    typeof data.min_year !== 'number' ||
    typeof data.max_year !== 'number' ||
    typeof data.min_years !== 'number'
  ) {
    throw new ApiError(502, 'INVALID_COVERAGE', 'Coverage response is missing required fields.')
  }
  return { min_year: data.min_year, max_year: data.max_year, min_years: data.min_years }
}

export async function fetchTrend(query: TrendQuery, options: RequestOptions): Promise<TrendResponse> {
  const response = await fetch(trendUrl(query), {
    headers: { Accept: 'application/json' },
    signal: options.signal,
  })
  if (!response.ok) throw await readError(response)
  const data = (await response.json()) as TrendResponse
  if (!Array.isArray(data?.series) || typeof data?.variable !== 'string') {
    throw new ApiError(502, 'INVALID_RESPONSE', 'Trend response is missing the series or variable.')
  }
  return data
}

export interface CompareQuery {
  point_a: { latitude: number; longitude: number }
  point_b: { latitude: number; longitude: number }
  variable: Variable
  start_year: number
  end_year: number
}

export async function fetchCompare(query: CompareQuery, options: RequestOptions): Promise<CompareResponse> {
  const response = await fetch(apiUrl('/api/compare'), {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(query),
    signal: options.signal,
  })
  if (!response.ok) throw await readError(response)
  const data = (await response.json()) as CompareResponse
  if (!Array.isArray(data?.point_a?.series) || !Array.isArray(data?.point_b?.series) ||
      data?.comparison?.method !== 'paired_annual_difference_descriptive' ||
      data.comparison.inferential !== false || data.point_a.unit !== data.point_b.unit) {
    throw new ApiError(502, 'INVALID_RESPONSE', 'Comparison response is missing paired series or descriptive metadata.')
  }
  return data
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
