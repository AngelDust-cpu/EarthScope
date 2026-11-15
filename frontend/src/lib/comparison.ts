import type { CompareResponse, SeriesPoint } from './types'

/** Each mean uses exactly the same paired complete baseline years. No imputation. */
export function comparisonBaseline(data: CompareResponse, start: number, end: number) {
  const b = new Map(data.point_b.series.map(point => [point.year, point.value]))
  const paired = data.point_a.series.filter(point =>
    point.year >= start && point.year <= end && point.value !== null && b.get(point.year) != null)
  const validRange = Number.isInteger(start) && Number.isInteger(end) &&
    start >= data.point_a.start_year && end <= data.point_a.end_year && start <= end
  const years = validRange ? paired.map(point => point.year) : []
  const meanA = years.length ? paired.reduce((sum, point) => sum + point.value!, 0) / years.length : null
  const meanB = years.length ? paired.reduce((sum, point) => sum + b.get(point.year)!, 0) / years.length : null
  return { years, meanA, meanB, validRange }
}

export function anomalySeries(series: SeriesPoint[], mean: number | null): SeriesPoint[] {
  return series.map(point => ({
    ...point,
    value: mean === null || point.value === null ? null : point.value - mean,
    fitted: mean === null || point.fitted === null ? null : point.fitted - mean,
  }))
}

/** Same scale for both side-by-side charts, including their fitted series. */
export function commonRange(a: SeriesPoint[], b: SeriesPoint[]): [number, number] | undefined {
  const values = [...a, ...b].flatMap(point => [point.value, point.fitted])
    .filter((value): value is number => value !== null && Number.isFinite(value))
  if (!values.length) return undefined
  const min = Math.min(...values), max = Math.max(...values)
  const padding = (max - min) * 0.08 || 1
  return [min - padding, max + padding]
}

/** Long-form export retains raw values, gaps, fit, anomaly and per-point provenance. */
export function comparisonCsv(data: CompareResponse, baselineStart: number, baselineEnd: number): string {
  const baseline = comparisonBaseline(data, baselineStart, baselineEnd)
  const cell = (value: unknown) => {
    const text = value == null ? '' : String(value)
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }
  const rows: unknown[][] = [[
    'point', 'year', 'value', 'unit', 'aggregation', 'valid_days', 'expected_days', 'fitted',
    'anomaly', 'anomaly_fitted', 'baseline_start', 'baseline_end', 'baseline_complete_years', 'baseline_mean',
    'latitude', 'longitude', 'variable', 'trend_per_decade', 'trend_years_used',
    'paired_slope_difference_A_minus_B_per_decade', 'paired_years_used', 'comparison_inferential',
    'source', 'dataset', 'parameter', 'daily_unit', 'time_standard', 'request_url', 'retrieved_at', 'cached', 'warnings',
  ]]
  for (const [label, response, mean] of [
    ['A', data.point_a, baseline.meanA], ['B', data.point_b, baseline.meanB],
  ] as const) {
    const anomalies = anomalySeries(response.series, mean)
    response.series.forEach((point, index) => rows.push([
      label, point.year, point.value, response.unit, response.aggregation, point.valid_days, point.expected_days, point.fitted,
      anomalies[index].value, anomalies[index].fitted, baselineStart, baselineEnd, baseline.years.join(';'), mean,
      response.latitude, response.longitude, response.variable, response.trend?.per_decade, response.trend?.years_used,
      data.comparison.slope_difference_per_decade, data.comparison.years_used, false,
      response.source.name, response.source.dataset, response.source.parameter, response.source.daily_unit,
      response.source.time_standard, response.source.request_url, response.source.retrieved_at, response.source.cached,
      response.warnings.join('; '),
    ]))
  }
  return `\uFEFF${rows.map(row => row.map(cell).join(',')).join('\r\n')}\r\n`
}
