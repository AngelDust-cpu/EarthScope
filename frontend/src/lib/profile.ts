import { apiUrl } from './apiBase'
import type { RequestOptions } from './api'
import type { AnomalyResponse, SeasonalResponse } from './climate'
import type { ExtremesResponse } from './extremes'
import { ApiError, type Coverage, type TrendResponse } from './types'

export interface ProfileQuery {
  latitude: number; longitude: number; start_year: number; end_year: number
  baseline_start_year: number; baseline_end_year: number
  hot_threshold: number; cold_threshold: number; rain_threshold: number
}
export interface ProfileSection<T> { status: 'ok' | 'error' | 'unavailable'; data: T | null; message: string | null }
export interface ProfileResponse {
  latitude: number; longitude: number; start_year: number; end_year: number
  baseline_start_year: number; baseline_end_year: number
  thresholds: { hot_days: number; cold_nights: number; heavy_rain: number }
  sections: {
    temperature: ProfileSection<TrendResponse>; precipitation: ProfileSection<TrendResponse>
    anomalies: ProfileSection<AnomalyResponse>; seasonal: ProfileSection<SeasonalResponse>
    hot_days: ProfileSection<ExtremesResponse>; cold_nights: ProfileSection<ExtremesResponse>
    heavy_rain: ProfileSection<ExtremesResponse>; heatwaves: ProfileSection<null>
  }
  summary: string[]; limitations: string[]
}
export const profileLabels = {
  temperature: 'Температура', precipitation: 'Осадки', anomalies: 'Аномалии температуры',
  seasonal: 'Сезоны · температура', hot_days: 'Жаркие дни', cold_nights: 'Холодные ночи',
  heavy_rain: 'Сильные осадки', heatwaves: 'Волны жары',
} as const
export const profileKeys = Object.keys(profileLabels) as (keyof ProfileResponse['sections'])[]
export function profileUrl(query: ProfileQuery, csv = false): string {
  const params = new URLSearchParams(Object.entries(query).map(([key, value]) => [key, String(value)]))
  return apiUrl(`/api/climate-profile${csv ? '.csv' : ''}?${params}`)
}
export function profileQuery(data: ProfileResponse): ProfileQuery {
  return { latitude: data.latitude, longitude: data.longitude, start_year: data.start_year, end_year: data.end_year,
    baseline_start_year: data.baseline_start_year, baseline_end_year: data.baseline_end_year,
    hot_threshold: data.thresholds.hot_days, cold_threshold: data.thresholds.cold_nights, rain_threshold: data.thresholds.heavy_rain }
}
export function validProfileQuery(q: ProfileQuery, coverage: Coverage): boolean {
  return Object.values(q).every(Number.isFinite) && Math.abs(q.latitude) <= 90 && Math.abs(q.longitude) <= 180 &&
    [q.start_year, q.end_year, q.baseline_start_year, q.baseline_end_year].every(Number.isInteger) &&
    q.start_year >= coverage.min_year && q.end_year <= coverage.max_year && q.end_year - q.start_year + 1 >= coverage.min_years &&
    q.baseline_start_year >= coverage.min_year && q.baseline_end_year <= coverage.max_year && q.baseline_start_year <= q.baseline_end_year &&
    Math.abs(q.hot_threshold) <= 100 && Math.abs(q.cold_threshold) <= 100 && q.rain_threshold >= 0 && q.rain_threshold <= 2000
}
export function completeAnnualMean(data: TrendResponse): { mean: number | null; count: number } {
  const rows = data.series.filter(p => p.value !== null && Number.isFinite(p.value) && p.expected_days > 0 && p.valid_days === p.expected_days)
  return { mean: rows.length ? rows.reduce((sum, row) => sum + row.value!, 0) / rows.length : null, count: rows.length }
}
async function checked(response: Response): Promise<Response> {
  if (!response.ok) {
    let code = 'HTTP_ERROR', message = `Ошибка запроса: HTTP ${response.status}`
    try { const body = await response.json(); code = body?.error?.code ?? code; message = body?.error?.message ?? message } catch { /* Safe HTTP fallback. */ }
    throw new ApiError(response.status, code, message)
  }
  return response
}
export async function fetchProfile(query: ProfileQuery, { signal }: RequestOptions): Promise<ProfileResponse> {
  const response = await checked(await fetch(profileUrl(query), { headers: { Accept: 'application/json' }, signal }))
  let data: ProfileResponse
  try { data = await response.json() } catch { throw new ApiError(502, 'INVALID_RESPONSE', 'Некорректный JSON профиля.') }
  if (!data || !data.thresholds || !data.sections || !Array.isArray(data.summary) || !Array.isArray(data.limitations) ||
    ![...data.summary, ...data.limitations].every(s => typeof s === 'string') ||
    Object.entries(query).some(([key, value]) => profileQuery(data)[key as keyof ProfileQuery] !== value) ||
    !profileKeys.every(key => {
      const s = data.sections[key]
      if (!s || !['ok', 'error', 'unavailable'].includes(s.status) || !(s.message === null || typeof s.message === 'string')) return false
      if (s.status !== 'ok') return s.data === null
      if (key === 'heatwaves') return false // No heatwave DTO/module exists in this contract.
      if (!s.data || !s.data.source || !Array.isArray(s.data.warnings)) return false
      return key === 'seasonal' ? 'periods' in s.data && Array.isArray(s.data.periods)
        : 'series' in s.data && Array.isArray(s.data.series)
    })) throw new ApiError(502, 'INVALID_RESPONSE', 'Ответ профиля не соответствует контракту или параметрам запроса.')
  return data
}
export async function fetchProfileCsv(query: ProfileQuery, signal: AbortSignal): Promise<string> {
  const response = await checked(await fetch(profileUrl(query, true), { headers: { Accept: 'text/csv' }, signal }))
  if (response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'text/csv')
    throw new ApiError(502, 'INVALID_RESPONSE', 'Сервер вернул не CSV.')
  const csv = await response.text()
  if (!csv.trim()) throw new ApiError(502, 'INVALID_RESPONSE', 'Сервер вернул пустой CSV.')
  return csv
}
