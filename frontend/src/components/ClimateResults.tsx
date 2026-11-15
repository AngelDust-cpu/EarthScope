import { useId } from 'react'
import { analysisPdfRequest } from '../lib/pdf'
import { PdfDownloadButton } from './PdfDownloadButton'
import { downloadCsv } from '../lib/api'
import type { AnomalyResponse, SeasonalResponse } from '../lib/climate'
import { formatCoordinate, formatValue } from '../lib/format'
import { ru, unitLabel, variableLabel } from '../lib/ru'
import type { Trend } from '../lib/types'
import { Button } from './ui/button'

const c = ru.climate
const periodLabel = (period: string) => c.periods[period as keyof typeof c.periods] ?? period
const number = (value: number | null) => value === null ? '—' : formatCoordinate(value, 2)
const panel = 'min-w-0 space-y-3 rounded-lg border border-line bg-panel p-4 sm:p-5'
type Common = AnomalyResponse | SeasonalResponse

function csv(data: Common, headings: string[], rows: (string | number | null)[][]): string {
  const meta = [
    'latitude', 'longitude', 'variable', 'unit', 'aggregation', 'start_year', 'end_year',
    'source_name', 'dataset', 'parameter', 'daily_unit', 'time_standard', 'retrieved_at', 'cached', 'request_url', 'warnings',
  ]
  const values = [
    data.latitude, data.longitude, data.variable, data.unit, data.aggregation, data.start_year, data.end_year,
    data.source.name, data.source.dataset, data.source.parameter, data.source.daily_unit, data.source.time_standard,
    data.source.retrieved_at, String(data.source.cached), data.source.request_url, data.warnings.join(' | '),
  ]
  const escape = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  return '\uFEFF' + [[...headings, ...meta], ...rows.map(row => [...row, ...values])]
    .map(row => row.map(escape).join(',')).join('\r\n')
}

function Source({ data }: { data: Common }) {
  return <section className={panel} aria-label="Источник результата">
    <h3 className="font-semibold">{ru.results.source}</h3>
    <p className="text-sm">{variableLabel(data.variable)} · {data.start_year}–{data.end_year} UTC · {unitLabel(data.unit)} · {data.aggregation === 'mean' ? 'Среднее' : 'Сумма'}</p>
    <p className="font-mono text-xs">WGS84: {formatCoordinate(data.latitude)}, {formatCoordinate(data.longitude)}</p>
    <dl className="grid gap-3 text-xs sm:grid-cols-2">
      {[
        ['Источник', `${data.source.name} · ${data.source.dataset}`],
        ['Суточные данные', `${data.source.parameter} · ${unitLabel(data.source.daily_unit)} · ${data.source.time_standard}`],
        ['Получено', data.source.retrieved_at],
        ['Кэш', data.source.cached ? 'Локальный кэш' : 'Получено от источника'],
        ['URL источника', data.source.request_url],
      ].map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-muted">{label}</dt><dd className="break-words">{value}</dd></div>)}
    </dl>
    <p className="text-xs text-muted">NASA POWER — сеточный продукт, не измерение метеостанции. Показаны рассчитанные агрегаты суточного ряда, а не прогноз. {ru.results.limitations}</p>
    {data.warnings.length > 0 && <div role="note" className="space-y-1 border-t border-line pt-3 text-xs">
      <p className="font-medium">Предупреждения источника (как получены):</p>
      {data.warnings.map((warning, i) => <p className="break-words" key={i}>{warning}</p>)}
    </div>}
  </section>
}

/** Gaps have no bars; the zero reference always remains in the scale. */
function ValueChart({ points, unit, label }: { points: { year: number; value: number | null }[]; unit: string; label: string }) {
  const id = useId()
  const values = points.flatMap(p => p.value === null ? [] : [p.value])
  const low = Math.min(0, ...values), high = Math.max(0, ...values)
  const span = high - low || 1
  const y = (value: number) => 220 - (value - low) / span * 180
  const width = 640 / Math.max(1, points.length)
  return <div className="min-w-0">
    <svg viewBox="0 0 740 270" role="img" aria-labelledby={id} className="science-plot w-full">
      <title id={id}>{label}. {unitLabel(unit)}. Нулевая линия отмечена пунктиром; точные значения и пропуски в таблице ниже.</title>
      <text x="5" y="20" fill="currentColor" fontSize="13">{unitLabel(unit)}</text>
      <text x="5" y="43" fill="currentColor" fontSize="12">{number(high)}</text>
      <text x="5" y="222" fill="currentColor" fontSize="12">{number(low)}</text>
      <line x1="70" x2="710" y1={y(0)} y2={y(0)} stroke="currentColor" strokeDasharray="5 4" />
      <text x="715" y={y(0) + 4} fill="currentColor" fontSize="12">0</text>
      {points.map((p, i) => p.value === null ? null : <rect key={p.year}
        x={70 + width * i + width * .15} width={width * .7}
        y={Math.min(y(0), y(p.value))} height={Math.max(1, Math.abs(y(p.value) - y(0)))}
        fill={p.value < 0 ? 'var(--chart-negative)' : 'var(--chart-positive)'}>
        <title>{p.year}: {number(p.value)} {unitLabel(unit)}</title>
      </rect>)}
      <text x="70" y="250" fill="currentColor" fontSize="13">{points[0]?.year}</text>
      <text x="390" y="265" textAnchor="middle" fill="currentColor" fontSize="13">Год (UTC)</text>
      <text x="710" y="250" textAnchor="end" fill="currentColor" fontSize="13">{points.at(-1)?.year}</text>
    </svg>
    {!values.length && <p className="text-sm text-muted">Нет полных значений для графика. Пропуски сохранены в таблице.</p>}
  </div>
}

export function AnomalyResult({ data }: { data: AnomalyResponse }) {
  return <div className="space-y-4">
    <section className={panel}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold">Годовые аномалии · {variableLabel(data.variable)} · {data.start_year}–{data.end_year}</h3>
        <PdfDownloadButton request={analysisPdfRequest(data)} />
        <Button type="button" variant="outline" onClick={() => downloadCsv('EarthScope_anomalies.csv', csv(data,
          ['year', 'value', 'anomaly', 'valid_days', 'expected_days', 'baseline_start_year', 'baseline_end_year', 'baseline_mean', 'baseline_complete_years'],
          data.series.map(p => [p.year, p.value, p.anomaly, p.valid_days, p.expected_days, data.baseline_start_year, data.baseline_end_year, data.baseline_mean, data.baseline_complete_years])))}>Скачать CSV</Button>
      </div>
      <div className="rounded border border-line bg-panel p-3 text-sm">
        <h4 className="font-semibold">{c.baseline}: {data.baseline_start_year}–{data.baseline_end_year}</h4>
        <p>Среднее базы: {formatValue(data.baseline_mean, data.unit)} · Полных лет: {data.baseline_complete_years}/{data.baseline_end_year - data.baseline_start_year + 1}</p>
        <p className="mt-2">{c.baselineHint}</p>
        {data.baseline_end_year - data.baseline_start_year + 1 < 30 && <p>{c.reference}</p>}
        <p className="mt-2 text-xs text-muted">{c.strict}</p>
        {data.baseline_mean === null && <p role="status" className="mt-2 font-medium">База неполная: аномалии не рассчитаны. Годовые значения доступны отдельно.</p>}
      </div>
      <ValueChart points={data.series.map(p => ({ year: p.year, value: p.anomaly }))} unit={data.unit} label="Аномалии относительно базы" />
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <caption className="mb-2 text-left text-muted">Годовые значения и отклонения от базы; «—» означает отсутствие значения, не ноль.</caption>
          <thead><tr>{['Год', `Значение, ${unitLabel(data.unit)}`, `Аномалия, ${unitLabel(data.unit)}`, 'Дни: есть / нужно'].map(h => <th scope="col" className="p-2" key={h}>{h}</th>)}</tr></thead>
          <tbody>{data.series.map(p => <tr key={p.year} className="border-t border-line">
            <th scope="row" className="p-2">{p.year}</th><td className="p-2">{number(p.value)}</td><td className="p-2">{number(p.anomaly)}</td><td className="p-2">{p.valid_days} / {p.expected_days}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </section>
    <Source data={data} />
  </div>
}

function Fit({ trend, unit }: { trend: Trend | null; unit: string }) {
  if (!trend) return <p className="text-sm">Тренд не рассчитан: нужно не менее 5 полных лет.</p>
  return <p className="text-sm">Наклон МНК: <strong>{number(trend.per_decade)} {unitLabel(unit)} за десятилетие</strong> · 95% ДИ [{number(trend.ci95_low)}; {number(trend.ci95_high)}] · p = {String(trend.p_value).replace('.', ',')} · Полных лет: {trend.years_used}. ДИ и p относятся к этому ряду, не к различию сезонов. {trend.years_used < 10 && 'Ряд короткий: вывод особенно чувствителен к предположениям модели.'}</p>
}

export function SeasonalResult({ data }: { data: SeasonalResponse }) {
  const fits = data.periods.filter(p => p.trend !== null)
  const yearKey = (series: typeof fits[number]['series']) => series.filter(p => p.value !== null).map(p => p.year).sort((a, b) => a - b).join(',')
  const fair = data.periods.length === 4 && fits.length === 4 && fits.every(p => yearKey(p.series) === yearKey(fits[0].series))
  const largest = fair ? Math.max(...fits.map(p => Math.abs(p.trend!.per_decade))) : null
  const leaders = largest === null ? [] : fits.filter(p => Math.abs(p.trend!.per_decade) === largest)
  return <div className="space-y-4">
    <section className={panel}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold">{periodLabel(data.period)} · {variableLabel(data.variable)} · {data.start_year}–{data.end_year}</h3>
        <PdfDownloadButton request={analysisPdfRequest(data)} />
        <Button type="button" variant="outline" onClick={() => downloadCsv('EarthScope_seasonal.csv', csv(data,
          ['period', 'year', 'value', 'valid_days', 'expected_days', 'fitted', 'slope_per_decade', 'ci95_low', 'ci95_high', 'p_value', 'years_used'],
          data.periods.flatMap(p => p.series.map(row => [p.period, row.year, row.value, row.valid_days, row.expected_days, row.fitted,
            p.trend?.per_decade ?? null, p.trend?.ci95_low ?? null, p.trend?.ci95_high ?? null, p.trend?.p_value ?? null, p.trend?.years_used ?? null]))))}>Скачать CSV</Button>
      </div>
      <p className="text-sm">{c.seasonalHint}</p><p className="text-xs text-muted">{c.winter}</p>
      {data.periods.length > 1 && <div className="rounded border border-line bg-panel p-3 text-sm">
        <h4 className="font-semibold">Сравнение сезонов</h4>
        {fair ? <p>Наибольший абсолютный наклон API: {leaders.map(p => periodLabel(p.period)).join(', ')} ({number(largest)} {unitLabel(data.unit)} за десятилетие). Набор полных лет одинаков для всех сезонов.</p>
          : <p>Ранжирование не выполнено: для сравнения нужны четыре тренда на одинаковом наборе полных лет.</p>}
        <p className="mt-2">{c.comparison}</p>
      </div>}
    </section>
    <div className="grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-2">
      {data.periods.map(p => <section key={p.period} className={panel}>
        <h3 className="font-semibold">{periodLabel(p.period)}</h3>
        <Fit trend={p.trend} unit={data.unit} />
        <ValueChart points={p.series} unit={data.unit} label={periodLabel(p.period)} />
        <details>
          <summary className="cursor-pointer rounded p-2 text-sm focus-visible:outline-2 focus-visible:outline-focus">Значения и полнота по годам ({p.series.length})</summary>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="p-2 text-left">{periodLabel(p.period)}: «—» — пропуск, не ноль.</caption>
              <thead><tr>{['Год', unitLabel(data.unit), 'Дни: есть / нужно', 'По тренду'].map(h => <th scope="col" key={h} className="p-2">{h}</th>)}</tr></thead>
              <tbody>{p.series.map(row => <tr key={row.year} className="border-t border-line">
                <th scope="row" className="p-2">{row.year}</th><td className="p-2">{number(row.value)}</td><td className="p-2">{row.valid_days} / {row.expected_days}</td><td className="p-2">{number(row.fitted)}</td>
              </tr>)}</tbody>
            </table>
          </div>
        </details>
      </section>)}
    </div>
    <Source data={data} />
  </div>
}
