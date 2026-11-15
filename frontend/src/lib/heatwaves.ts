import { apiUrl } from './apiBase'
import { ApiError } from './types'

export interface HeatwavesQuery {
  latitude: number; longitude: number; startYear: number; endYear: number
  threshold: number; minDuration: number
}
export interface HeatwaveYear {
  year: number; count: number | null; total_days: number | null; max_duration: number | null
  mean_duration: number | null; max_tmax: number | null; valid_days: number; expected_days: number
}
export interface HeatwaveEpisode {
  start: string; end: string; duration: number; max_tmax: number; mean_tmax: number; max_excess: number
}
export interface HeatwavesResponse {
  latitude: number; longitude: number; start_year: number; end_year: number; threshold: number; min_duration: number
  source: Record<string, unknown>; series: HeatwaveYear[]; episodes: HeatwaveEpisode[]
  trends: Record<'count' | 'total_days' | 'max_duration', { per_decade: number; years_used: number; residual_lag1: number | null } | null>
  warnings: string[]
}
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const nullable = (v: unknown) => v === null || finite(v)
export function validHeatwavesQuery(q: HeatwavesQuery) {
  return finite(q.latitude) && Math.abs(q.latitude) <= 90 && finite(q.longitude) && Math.abs(q.longitude) <= 180 &&
    Number.isInteger(q.startYear) && Number.isInteger(q.endYear) && q.startYear >= 1981 && q.endYear <= 2025 && q.startYear <= q.endYear &&
    finite(q.threshold) && Number.isSafeInteger(q.minDuration) && q.minDuration >= 1
}
export function heatwavesUrl(q: HeatwavesQuery) {
  return apiUrl(`/api/heatwaves?${new URLSearchParams({ latitude: String(q.latitude), longitude: String(q.longitude),
    start_year: String(q.startYear), end_year: String(q.endYear), threshold: String(q.threshold), min_duration: String(q.minDuration) })}`)
}
export function heatwavesResultQuery(d: HeatwavesResponse): HeatwavesQuery {
  return { latitude: d.latitude, longitude: d.longitude, startYear: d.start_year, endYear: d.end_year, threshold: d.threshold, minDuration: d.min_duration }
}
export async function fetchHeatwaves(q: HeatwavesQuery, { signal }: { signal?: AbortSignal } = {}): Promise<HeatwavesResponse> {
  if (!validHeatwavesQuery(q)) throw new ApiError(422, 'INVALID_QUERY', 'Проверьте координаты, годы, порог и длительность.')
  const response = await fetch(heatwavesUrl(q), { headers: { Accept: 'application/json' }, signal })
  if (!response.ok) {
    let message = `Ошибка запроса: HTTP ${response.status}`, code = 'HTTP_ERROR'
    try { const body = await response.json(); message = body?.error?.message ?? message; code = body?.error?.code ?? code } catch { /* Non-JSON error. */ }
    throw new ApiError(response.status, code, message)
  }
  let d: HeatwavesResponse
  try { d = await response.json() } catch { throw new ApiError(502, 'INVALID_RESPONSE', 'Некорректный JSON ответа.') }
  if (!d || d.latitude !== q.latitude || d.longitude !== q.longitude || d.start_year !== q.startYear || d.end_year !== q.endYear ||
    d.threshold !== q.threshold || d.min_duration !== q.minDuration || !d.source || typeof d.source !== 'object' || Array.isArray(d.source) ||
    !Array.isArray(d.warnings) || !d.warnings.every(w => typeof w === 'string') ||
    !Array.isArray(d.series) || !d.series.every(p => p && Number.isInteger(p.year) &&
      [p.count, p.total_days, p.max_duration, p.mean_duration, p.max_tmax].every(nullable) &&
      Number.isInteger(p.valid_days) && Number.isInteger(p.expected_days) && p.valid_days >= 0 && p.valid_days <= p.expected_days &&
      (p.valid_days < p.expected_days ? [p.count, p.total_days, p.max_duration, p.mean_duration, p.max_tmax].every(v => v === null) :
        p.count !== null && p.total_days !== null && p.max_duration !== null &&
        (p.count === 0 ? p.total_days === 0 && p.max_duration === 0 && p.mean_duration === null && p.max_tmax === null : p.count > 0 && p.mean_duration !== null && p.max_tmax !== null))) ||
    !Array.isArray(d.episodes) || !d.episodes.every(p => p && /^\d{4}-\d{2}-\d{2}$/.test(p.start) && /^\d{4}-\d{2}-\d{2}$/.test(p.end) && p.end >= p.start &&
      Number.isInteger(p.duration) && p.duration >= q.minDuration && [p.max_tmax, p.mean_tmax, p.max_excess].every(finite)) ||
    !d.trends || !(['count', 'total_days', 'max_duration'] as const).every(k => {
      const t = d.trends[k]
      return t === null || (t && finite(t.per_decade) && Number.isInteger(t.years_used) && t.years_used >= 2 && nullable(t.residual_lag1))
    })) throw new ApiError(502, 'INVALID_RESPONSE', 'Ответ волн жары не соответствует контракту.')
  return d
}

/** Empty cells retain null; quoted string metadata is protected against spreadsheet formulas. */
export function heatwavesCsv(d: HeatwavesResponse, kind: 'annual' | 'episodes') {
  const columns = kind === 'annual' ? ['year', 'count', 'total_days', 'max_duration', 'mean_duration', 'max_tmax', 'valid_days', 'expected_days'] :
    ['start', 'end', 'duration', 'max_tmax', 'mean_tmax', 'max_excess']
  const meta = { latitude: d.latitude, longitude: d.longitude, start_year: d.start_year, end_year: d.end_year,
    threshold_C: d.threshold, min_duration_days: d.min_duration, time_standard: 'UTC', temperature_unit: 'degC', duration_unit: 'days',
    attribution: 'Entire episode assigned to start year; outer window may censor episodes',
    method: 'Descriptive OLS only; lag1 diagnostic; no significance inference', source: JSON.stringify(d.source),
    trends: JSON.stringify(d.trends), warnings: d.warnings.join(' | ') }
  const rows = kind === 'annual' ? d.series : d.episodes
  const escape = (v: unknown) => `"${String(typeof v === 'string' && /^[=+\-@\t\r\n]/.test(v) ? `'${v}` : v ?? '').replace(/"/g, '""')}"`
  return '\uFEFF' + [[...columns, ...Object.keys(meta)], ...rows.map(p => [...columns.map(k => (p as unknown as Record<string, unknown>)[k]), ...Object.values(meta)])]
    .map(row => row.map(escape).join(',')).join('\r\n')
}
