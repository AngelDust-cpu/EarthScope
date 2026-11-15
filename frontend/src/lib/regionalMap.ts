import { apiUrl } from './apiBase'

export type RegionalVariable = 'temperature' | 'precipitation'
export type RegionalLayer = 'trend' | 'significance'
export interface RegionalQuery {
  variable: RegionalVariable
  start_year: number
  end_year: number
}
export interface RegionalCell {
  type: 'Feature'
  id: string
  geometry: { type: 'Polygon'; coordinates: number[][][] }
  properties: {
    cell_id: string; latitude: number; longitude: number; elevation_m: number | null
    quality: 'complete' | 'incomplete' | 'insufficient_years'
    valid_years: number; expected_years: number; max_gap_years: number
    annual: Array<{ year: number; value: number | null; valid_months: number }>
    trend: null | {
      per_decade: number; ci95_low: number | null; ci95_high: number | null
      p_value: number | null; q_value_by: number | null; significant_by: boolean | null
      hac_sensitivity: Array<{ lags: 1 | 3 | 5; p_value: number }>
    }
  }
}
export interface RegionalMapResponse {
  type: 'FeatureCollection'
  metadata: {
    region_id: 'kg-surroundings'; region_label: string
    bounds: { south: number; north: number; west: number; east: number }
    variable: RegionalVariable; parameter: 'T2M' | 'PRECTOTCORR'; unit: '°C' | 'mm'
    aggregation: 'annual_mean' | 'sum_monthly_daily_rate_times_days'
    start_year: number; end_year: number
    resolution: { latitude_degrees: number; longitude_degrees: number }
    source: {
      name: 'NASA POWER'; dataset: string; api_version: string; time_standard: 'LST'
      community: 'AG'; request_urls: string[]; retrieved_at: string
      cached: boolean; analysis_cached: boolean
    }
    inference: { method: string; hac_lags: 3; fdr: 'BY'; fdr_q: 0.1; tested_cells: number }
    summary: { total_cells: number; trend_cells: number; fdr_cells: number }
  }
  features: RegionalCell[]
}

function isRenderableCell(cell: RegionalCell): boolean {
  const ring = cell?.geometry?.coordinates?.[0]
  const p = cell?.properties
  return cell?.type === 'Feature' && typeof cell.id === 'string' &&
    cell.geometry?.type === 'Polygon' && Array.isArray(ring) && ring.length >= 4 &&
    ring.every(point => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite)) &&
    !!p && Number.isFinite(p.latitude) && Number.isFinite(p.longitude) &&
    Array.isArray(p.annual) && p.annual.every(row => row && Number.isInteger(row.year) &&
      (row.value === null || Number.isFinite(row.value))) &&
    (p.trend === null || (!!p.trend && Number.isFinite(p.trend.per_decade) && Array.isArray(p.trend.hac_sensitivity)))
}

export async function fetchRegionalMap(query: RegionalQuery, signal: AbortSignal): Promise<RegionalMapResponse> {
  const params = new URLSearchParams({ region: 'kg-surroundings', variable: query.variable,
    start_year: String(query.start_year), end_year: String(query.end_year) })
  const response = await fetch(apiUrl(`/api/maps/trends?${params}`), { signal, headers: { Accept: 'application/json' } })
    .catch(error => {
      if (signal.aborted) throw error
      throw new Error('Не удалось связаться с сервером EarthScope.')
    })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    if (body?.error?.code === 'POWER_UNAVAILABLE') {
      throw new Error(`Данные NASA POWER временно недоступны (HTTP ${response.status}).`)
    }
    throw new Error(`Ошибка сервера EarthScope (HTTP ${response.status}).`)
  }
  const data = await response.json().catch(() => null) as RegionalMapResponse | null
  if (data?.type !== 'FeatureCollection' || !Array.isArray(data.features) || !data.metadata?.source ||
      data.metadata.region_id !== 'kg-surroundings' || data.metadata.variable !== query.variable ||
      data.metadata.start_year !== query.start_year || data.metadata.end_year !== query.end_year ||
      !data.metadata.resolution || !data.metadata.inference || !Array.isArray(data.metadata.source.request_urls) ||
      data.metadata.summary?.total_cells !== data.features.length || !data.features.every(isRenderableCell)) {
    throw new Error('Сервер вернул некорректный слой для выбранных параметров.')
  }
  return data
}

export const signed = (value: number) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${Math.abs(value).toLocaleString('ru-RU', { maximumFractionDigits: 3 })}`
export const trendUnit = (unit: RegionalMapResponse['metadata']['unit']) => `${unit === 'mm' ? 'мм' : unit}/десятилетие`
export const REGIONAL_COLORS = {
  negative: '#256bc4', zero: '#8e999e', positive: '#d4484c',
  significant: '#b8c0c9', notSignificant: '#606a75',
} as const

export function colorLimit(cells: RegionalCell[]): number {
  return Math.max(0.001, ...cells.map(c => Math.abs(c.properties.trend?.per_decade ?? 0)))
}

function csv(rows: unknown[][]): string {
  // Quoting alone does not protect spreadsheet formula evaluation.
  return '\uFEFF' + rows.map(row => row.map(value => {
    let text = value == null ? '' : String(value)
    if (typeof value === 'string' && /^[=+\-@\t\r]/.test(text)) text = "'" + text
    return `"${text.replaceAll('"', '""')}"`
  }).join(',')).join('\r\n')
}
export function regionalCsv(data: RegionalMapResponse, layer: RegionalLayer): string {
  const m = data.metadata
  return csv([
    ['cell_id', 'latitude', 'longitude', 'start_year', 'end_year', 'variable', 'unit_per_decade', 'layer',
      'slope', 'ci95_low', 'ci95_high', 'p', 'q_BY', 'significant_BY', 'quality', 'valid_years',
      'expected_years', 'source', 'dataset', 'api_version', 'time_standard', 'retrieved_at'],
    ...data.features.map(({ properties: p }) => [p.cell_id, p.latitude, p.longitude, m.start_year,
      m.end_year, m.variable, `${m.unit}/10 years`, layer, p.trend?.per_decade, p.trend?.ci95_low,
      p.trend?.ci95_high, p.trend?.p_value, p.trend?.q_value_by, p.trend?.significant_by,
      p.quality, p.valid_years, p.expected_years, m.source.name, m.source.dataset, m.source.api_version,
      m.source.time_standard, m.source.retrieved_at]),
  ])
}
export function regionalSeriesCsv(data: RegionalMapResponse, cell: RegionalCell): string {
  const m = data.metadata, p = cell.properties
  return csv([['cell_id', 'latitude', 'longitude', 'start_year', 'end_year', 'year', 'value', 'unit',
    'valid_months', 'source', 'dataset', 'api_version', 'time_standard', 'aggregation', 'retrieved_at'],
  ...p.annual.map(a => [p.cell_id, p.latitude, p.longitude, m.start_year, m.end_year, a.year, a.value,
    m.unit, a.valid_months, m.source.name, m.source.dataset, m.source.api_version,
    m.source.time_standard, m.aggregation, m.source.retrieved_at])])
}
