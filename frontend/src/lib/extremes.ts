import { apiUrl } from './apiBase'
import { ApiError } from './types'

export type ExtremeEvent = 'hot_days' | 'cold_nights' | 'heavy_rain'
export interface ExtremesQuery {
  latitude: number; longitude: number; startYear: number; endYear: number
  event: ExtremeEvent; threshold: number
}
export interface ExtremesResponse {
  latitude: number; longitude: number; start_year: number; end_year: number
  event: ExtremeEvent; threshold: number; threshold_unit: '°C' | 'mm/day'; operator: '>' | '<'
  source: {
    name: string; parameter: string; daily_unit: string; dataset: string
    time_standard: string; request_url: string; retrieved_at: string; cached: boolean
  }
  warnings: string[]
  series: {
    year: number; events: number | null; fraction: number | null
    valid_days: number; expected_days: number; excluded_days: number; fitted: number | null
  }[]
  trend: {
    years_used: number; per_decade: number; count_per_decade: number
    ci95_low: number | null; ci95_high: number | null; p_value: number | null
    significant: boolean | null; residual_lag1: number | null
    sensitivity: { method: 'ols' | 'hac'; lags: number | null; ci95_low: number; ci95_high: number; p_value: number }[]
  } | null
}
export const extremeEvents = {
  hot_days: { label: 'Жаркие дни', parameter: 'T2M_MAX', operator: '>', unit: '°C', threshold: 30 },
  cold_nights: { label: 'Холодные ночи', parameter: 'T2M_MIN', operator: '<', unit: '°C', threshold: 0 },
  heavy_rain: { label: 'Сильные осадки', parameter: 'PRECTOTCORR', operator: '>', unit: 'мм/сут', threshold: 20 },
} as const

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const nullable = (v: unknown) => v === null || finite(v)
export function validExtremesQuery(q: ExtremesQuery): boolean {
  return finite(q.latitude) && Math.abs(q.latitude) <= 90 && finite(q.longitude) && Math.abs(q.longitude) <= 180 &&
    Number.isInteger(q.startYear) && Number.isInteger(q.endYear) && q.startYear >= 1981 && q.endYear <= 2025 &&
    q.startYear <= q.endYear && Object.hasOwn(extremeEvents, q.event) && finite(q.threshold) &&
    q.threshold >= (q.event === 'heavy_rain' ? 0 : -100) && q.threshold <= (q.event === 'heavy_rain' ? 2000 : 100)
}

export async function fetchExtremes(query: ExtremesQuery, { signal }: { signal?: AbortSignal } = {}): Promise<ExtremesResponse> {
  if (!validExtremesQuery(query)) throw new ApiError(422, 'INVALID_QUERY', 'Проверьте координаты, годы и порог.')
  const search = new URLSearchParams({
    latitude: String(query.latitude), longitude: String(query.longitude),
    start_year: String(query.startYear), end_year: String(query.endYear),
    event: query.event, threshold: String(query.threshold),
  })
  const response = await fetch(apiUrl(`/api/extremes?${search}`), { headers: { Accept: 'application/json' }, signal })
  if (!response.ok) {
    let code = 'HTTP_ERROR', message = `Ошибка запроса: HTTP ${response.status}`
    try {
      const body = await response.json()
      if (typeof body?.error?.code === 'string') code = body.error.code
      if (typeof body?.error?.message === 'string') message = body.error.message
    } catch { /* Non-JSON HTTP error. */ }
    throw new ApiError(response.status, code, message)
  }
  let data: ExtremesResponse
  try { data = await response.json() as ExtremesResponse }
  catch { throw new ApiError(502, 'INVALID_RESPONSE', 'Некорректный JSON ответа.') }
  const t = data?.trend
  if (!data || data.event !== query.event || data.threshold !== query.threshold ||
    data.latitude !== query.latitude || data.longitude !== query.longitude ||
    data.start_year !== query.startYear || data.end_year !== query.endYear ||
    data.operator !== extremeEvents[query.event].operator ||
    data.threshold_unit !== (query.event === 'heavy_rain' ? 'mm/day' : '°C') ||
    !data.source || !['name', 'parameter', 'daily_unit', 'dataset', 'time_standard', 'request_url', 'retrieved_at'].every(k => typeof data.source[k as keyof typeof data.source] === 'string') ||
    !Array.isArray(data.warnings) || !data.warnings.every(w => typeof w === 'string') ||
    !Array.isArray(data.series) || !data.series.every(p => p && Number.isInteger(p.year) &&
      nullable(p.events) && nullable(p.fraction) && nullable(p.fitted) &&
      [p.valid_days, p.expected_days, p.excluded_days].every(n => Number.isInteger(n) && n >= 0) &&
      (p.events === null ? p.fraction === null : p.valid_days === p.expected_days && p.events >= 0 && p.events <= p.valid_days && p.fraction !== null && p.fraction >= 0 && p.fraction <= 1)) ||
    !(t === null || (t && Number.isInteger(t.years_used) && finite(t.per_decade) && finite(t.count_per_decade) &&
      [t.ci95_low, t.ci95_high, t.p_value, t.residual_lag1].every(nullable) &&
      (t.significant === null || typeof t.significant === 'boolean') &&
      Array.isArray(t.sensitivity) && t.sensitivity.every(s => ['ols', 'hac'].includes(s.method) &&
        (s.lags === null || Number.isInteger(s.lags)) && [s.ci95_low, s.ci95_high, s.p_value].every(finite))))) {
    throw new ApiError(502, 'INVALID_RESPONSE', 'Ответ экстремумов не соответствует контракту.')
  }
  return data
}

export function extremesCsv(data: ExtremesResponse): string {
  const t = data.trend
  const meta = {
    latitude: data.latitude, longitude: data.longitude, start_year: data.start_year, end_year: data.end_year,
    event: data.event, threshold: data.threshold, threshold_unit: data.threshold_unit, operator: data.operator,
    fitted_unit: 'event days per 365 days', slope_unit: 'event days per 365 days per decade',
    slope_per_decade: t?.per_decade, count_slope_per_decade: t?.count_per_decade, years_used: t?.years_used,
    ci95_low: t?.ci95_low, ci95_high: t?.ci95_high, p_value: t?.p_value, significant: t?.significant,
    residual_lag1: t?.residual_lag1, sensitivity: JSON.stringify(t?.sensitivity ?? []),
    ...Object.fromEntries(Object.entries(data.source).map(([k, v]) => [`source_${k}`, v])),
    warnings: data.warnings.join(' | '),
  }
  const columns = ['year', 'events', 'fraction', 'valid_days', 'expected_days', 'excluded_days', 'fitted'] as const
  // Prevent spreadsheet formula evaluation in string metadata; preserve numeric negatives.
  const escape = (v: unknown) => `"${String(typeof v === 'string' && /^[=+\-@\t\r]/.test(v) ? `'${v}` : v ?? '').replace(/"/g, '""')}"`
  return '\uFEFF' + [[...columns, ...Object.keys(meta)], ...data.series.map(p => [...columns.map(k => p[k]), ...Object.values(meta)])]
    .map(row => row.map(escape).join(',')).join('\r\n')
}
