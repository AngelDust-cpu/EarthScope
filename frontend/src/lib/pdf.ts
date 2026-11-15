import { apiUrl } from './apiBase'
import type { TrendResponse, CompareResponse } from './types'
import type { AnomalyResponse, SeasonalResponse } from './climate'
import type { ExtremesResponse } from './extremes'

export interface PdfRequest { url: string; body?: string }
type PointResult = TrendResponse | AnomalyResponse | SeasonalResponse | ExtremesResponse

/** Reconstruct only the analyzed inputs echoed by a successful result. */
export function analysisPdfRequest(data: PointResult | CompareResponse): PdfRequest {
  if ('point_a' in data) {
    const a = data.point_a, b = data.point_b
    return { url: apiUrl('/api/compare'), body: JSON.stringify({
      point_a: { latitude: a.latitude, longitude: a.longitude },
      point_b: { latitude: b.latitude, longitude: b.longitude },
      variable: a.variable, start_year: a.start_year, end_year: a.end_year,
    }) }
  }
  const params = new URLSearchParams({ latitude: String(data.latitude), longitude: String(data.longitude),
    start_year: String(data.start_year), end_year: String(data.end_year) })
  let route = 'trend'
  if ('event' in data) {
    route = 'extremes'
    params.set('event', data.event)
    params.set('threshold', String(data.threshold))
  } else {
    params.set('variable', data.variable)
    if ('baseline_start_year' in data) {
      route = 'anomalies'
      params.set('baseline_start_year', String(data.baseline_start_year))
      params.set('baseline_end_year', String(data.baseline_end_year))
    } else if ('period' in data) {
      route = 'seasonal'
      params.set('period', data.period)
    }
  }
  return { url: apiUrl(`/api/${route}?${params}`) }
}

export function safePdfFilename(disposition: string | null): string {
  let name = disposition?.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (name) { try { name = decodeURIComponent(name) } catch { name = undefined } }
  name ??= disposition?.match(/filename="([^"]+)"/i)?.[1] ?? disposition?.match(/filename=([^;]+)/i)?.[1]
  const safe = (name ?? 'EarthScope_report').replace(/\.pdf$/i, '').replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 100)
  return `EarthScope_${safe || 'report'}.pdf`
}

export async function fetchPdf(request: PdfRequest, signal: AbortSignal, detective = false) {
  const headers: Record<string, string> = { Accept: 'application/pdf' }
  if (request.body !== undefined) headers['Content-Type'] = 'application/json'
  if (detective) headers['X-EarthScope-Report'] = 'detective'
  const response = await fetch(request.url, { method: request.body === undefined ? 'GET' : 'POST',
    headers, body: request.body, signal })
  if (!response.ok) {
    let message = `Не удалось скачать PDF (HTTP ${response.status}).`
    try {
      const body = await response.json()
      if (typeof body?.error?.message === 'string') message = body.error.message
    } catch { /* Keep a safe error for non-JSON responses. */ }
    throw new Error(message)
  }
  if (response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/pdf') {
    throw new Error('Сервер вернул не PDF. Повторите загрузку.')
  }
  const blob = await response.blob()
  if (!blob.size) throw new Error('Сервер вернул пустой PDF. Повторите загрузку.')
  return { blob, filename: safePdfFilename(response.headers.get('Content-Disposition')) }
}
