import { useMemo, useState } from 'react'
import { analysisPdfRequest } from '../lib/pdf'
import { PdfDownloadButton } from './PdfDownloadButton'
import type { AsyncState } from '../hooks/useAsyncData'
import { downloadCsv } from '../lib/api'
import { anomalySeries, commonRange, comparisonBaseline, comparisonCsv } from '../lib/comparison'
import { formatCoordinate, formatSigned, formatUtcTimestamp, formatValue } from '../lib/format'
import { displayError, ru, unitLabel, variableLabel } from '../lib/ru'
import type { CompareResponse, TrendResponse } from '../lib/types'
import { TrendChart } from './TrendChart'
import { Button } from './ui/button'
import { Card, CardContent, CardHeader, CardTitle } from './ui/card'
import { Field, Select, TextInput } from './ui/field'
import { Alert, EmptyState, Spinner } from './ui/feedback'

export function ComparisonBody({ state, onRetry }: { state: AsyncState<CompareResponse>; onRetry: () => void }) {
  const { data, error, loading } = state
  return <div className="space-y-4" aria-busy={loading}>
    {loading && <p role="status" className="flex items-center gap-2 text-sm"><Spinner className="size-4 animate-spin" />Загрузка обеих точек NASA POWER… {data ? 'Ниже — предыдущий результат.' : ''}</p>}
    {error && <Alert tone="error" title="Сравнение не выполнено" action={<Button type="button" variant="outline" onClick={onRetry} disabled={loading}>{ru.results.retry}</Button>}>
      {displayError(error)} Проверьте координаты и период, затем повторите запрос. Частичный результат не используется.
    </Alert>}
    {data ? <ComparisonResult key={`${data.point_a.variable}-${data.point_a.start_year}-${data.point_a.end_year}`} data={data} />
      : !loading && !error ? <EmptyState title="Сравните две точки">Выберите A или B, задайте координаты на карте или пресетом, затем нажмите «{ru.controls.submit}». Обе точки используют одну переменную и период.</EmptyState> : null}
  </div>
}

function PointSummary({ label, response }: { label: string; response: TrendResponse }) {
  return <Card>
    <CardHeader><CardTitle>Точка {label}: {formatCoordinate(response.latitude)}, {formatCoordinate(response.longitude)}°</CardTitle></CardHeader>
    <CardContent className="space-y-2 text-sm">
      <p className="text-xl font-semibold">{response.trend ? formatSigned(response.trend.per_decade, response.unit) : 'Тренд недоступен'}</p>
      <p className="text-xs text-muted">OLS по собственным полным годам: {response.trend?.years_used ?? response.series.filter(point => point.value !== null).length}. {response.variable === 'temperature' ? 'Годовая средняя температура' : 'Годовая сумма осадков'} · {unitLabel(response.unit)}.</p>
      {response.trend && <p className="text-xs text-muted">95% интервал OLS: {formatValue(response.trend.ci95_low, '')} … {formatValue(response.trend.ci95_high, '')} {unitLabel(response.unit)} / десятилетие; без поправки на автокорреляцию.</p>}
      {response.warnings.map((warning, index) => <p key={index} className="text-xs text-secondary">Предупреждение: {warning}</p>)}
      <details className="text-xs">
        <summary className="cursor-pointer py-2">Источник и происхождение данных {label}</summary>
        <dl className="space-y-1 break-words text-muted">
          <dt>Источник / набор / параметр</dt><dd>{response.source.name} / {response.source.dataset} / {response.source.parameter}</dd>
          <dt>Суточные единицы / время</dt><dd>{response.source.daily_unit} / {response.source.time_standard}</dd>
          <dt>Получено</dt><dd>{formatUtcTimestamp(response.source.retrieved_at)} · {response.source.cached ? 'из кэша' : 'загружено'}</dd>
          <dt>Запрос источника</dt><dd>{response.source.request_url}</dd>
        </dl>
      </details>
    </CardContent>
  </Card>
}

function ComparisonResult({ data }: { data: CompareResponse }) {
  const a = data.point_a, b = data.point_b
  const [layout, setLayout] = useState('overlay')
  const [view, setView] = useState('absolute')
  const [baselineStart, setBaselineStart] = useState(String(a.start_year))
  const [baselineEnd, setBaselineEnd] = useState(String(Math.min(a.start_year + 9, a.end_year)))
  const first = /^\d{4}$/.test(baselineStart) ? Number(baselineStart) : NaN
  const last = /^\d{4}$/.test(baselineEnd) ? Number(baselineEnd) : NaN
  const baseline = useMemo(() => comparisonBaseline(data, first, last), [data, first, last])
  const isAnomaly = view === 'anomaly'
  const seriesA = useMemo(() => isAnomaly ? anomalySeries(a.series, baseline.meanA) : a.series, [a.series, baseline.meanA, isAnomaly])
  const seriesB = useMemo(() => isAnomaly ? anomalySeries(b.series, baseline.meanB) : b.series, [b.series, baseline.meanB, isAnomaly])
  const range = useMemo(() => commonRange(seriesA, seriesB), [seriesA, seriesB])
  const anomalyReady = baseline.validRange && baseline.years.length > 0
  const hasAnnualValues = [...a.series, ...b.series].some(point => point.value !== null)
  const difference = data.comparison.slope_difference_per_decade

  return <>
    <Card>
      <CardHeader><CardTitle>{variableLabel(a.variable)} · A / B · {a.start_year}–{a.end_year} UTC</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <PdfDownloadButton request={analysisPdfRequest(data)} />
        <p className="text-xs text-muted">PDF: исходный анализ A / B по сохранённому результату; настройки вида и редактируемая база аномалий не включены.</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field htmlFor="comparison-layout" label="Расположение">
            <Select id="comparison-layout" value={layout} onChange={event => setLayout(event.target.value)}>
              <option value="overlay">Наложение A / B</option><option value="side">Рядом · одинаковая шкала</option>
            </Select>
          </Field>
          <Field htmlFor="comparison-view" label="Значения">
            <Select id="comparison-view" value={view} onChange={event => setView(event.target.value)}>
              <option value="absolute">Абсолютные</option><option value="anomaly">Аномалии · общие базовые годы</option>
            </Select>
          </Field>
        </div>
        <fieldset className="space-y-2">
          <legend className="mb-2 text-sm font-medium">Общий базовый период аномалий (UTC)</legend>
          <div className="grid grid-cols-2 gap-3">
            <Field htmlFor="baseline-start" label="Начало"><TextInput id="baseline-start" inputMode="numeric" value={baselineStart} onChange={event => setBaselineStart(event.target.value)} aria-invalid={!baseline.validRange} aria-describedby="baseline-hint" /></Field>
            <Field htmlFor="baseline-end" label="Конец"><TextInput id="baseline-end" inputMode="numeric" value={baselineEnd} onChange={event => setBaselineEnd(event.target.value)} aria-invalid={!baseline.validRange} aria-describedby="baseline-hint" /></Field>
          </div>
          <p id="baseline-hint" className="text-xs text-muted">{!baseline.validRange
            ? `Введите целые годы в пределах ${a.start_year}–${a.end_year}, начало ≤ конец.`
            : `Каждая точка отклоняется от своего среднего за одни и те же общие полные годы: ${baseline.years.join(', ') || 'нет'}. A: ${formatValue(baseline.meanA, a.unit)}; B: ${formatValue(baseline.meanB, b.unit)}. Пропущенные годы не заполняются.`}</p>
          <p className="text-xs text-muted">Сдвиг на среднее не меняет наклоны. Короткий базовый период — исследовательское сравнение, не климатическая норма. Изменение вида не отправляет запрос.</p>
        </fieldset>
        {!hasAnnualValues ? <EmptyState title="Нет полных годовых значений">Для обеих точек все годы неполные. Измените период или координаты и повторите анализ; отсутствующие значения не заменяются.</EmptyState>
          : isAnomaly && !anomalyReady ? <EmptyState title="Аномалии недоступны">Выберите корректный базовый период хотя бы с одним общим полным годом либо абсолютные значения.</EmptyState>
          : <div className={layout === 'side' ? 'grid grid-cols-1 gap-4 xl:grid-cols-2' : ''}>
            <div className="h-[320px] min-w-0"><TrendChart series={seriesA} comparisonSeries={layout === 'overlay' ? seriesB : undefined} seriesLabel="A" variable={a.variable} unit={a.unit} yRange={range} anomaly={isAnomaly} /></div>
            {layout === 'side' && <div className="h-[320px] min-w-0"><TrendChart series={seriesB} seriesLabel="B" variable={b.variable} unit={b.unit} yRange={range} anomaly={isAnomaly} /></div>}
          </div>}
        <Button type="button" variant="outline" disabled={!baseline.validRange} onClick={() => downloadCsv(
          `EarthScope_compare_${a.variable}_${a.start_year}-${a.end_year}.csv`, comparisonCsv(data, first, last),
        )}>Скачать CSV · A и B</Button>
        <p className="text-xs text-muted">CSV: абсолютные значения, значения линии OLS, аномалии, базовые годы, координаты, тренды и происхождение данных. При отсутствии общих базовых лет аномалии пусты.</p>
        <details>
          <summary className="cursor-pointer py-2 text-sm">Годовые значения · доступная таблица</summary>
          <div className="overflow-x-auto">
            <table className="w-full text-left font-mono text-xs">
              <caption className="py-2 text-left">{isAnomaly ? 'Аномалии' : 'Абсолютные годовые значения'} · {unitLabel(a.unit)}; «—» — неполный год или недоступная база.</caption>
              <thead><tr><th scope="col" className="p-2">Год UTC</th><th scope="col" className="p-2">A ({unitLabel(a.unit)})</th><th scope="col" className="p-2">B ({unitLabel(b.unit)})</th></tr></thead>
              <tbody>{seriesA.map(point => <tr key={point.year} className="border-t border-line"><th scope="row" className="p-2">{point.year}</th><td className="p-2">{formatValue(point.value, '')}</td><td className="p-2">{formatValue(seriesB.find(other => other.year === point.year)?.value ?? null, '')}</td></tr>)}</tbody>
            </table>
          </div>
        </details>
      </CardContent>
    </Card>
    <p className="text-xs text-muted">NASA POWER: сеточные данные реанализа, не измерения станции в выбранной точке и не прогноз. Только полные календарные годы. OLS предполагает независимые ошибки и постоянную дисперсию; автокорреляция и пространственная зависимость ограничивают выводы. Сравнение исследовательское, причинность не устанавливается.</p>
    <div className="grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-2">
      <PointSummary label="A" response={a} /><PointSummary label="B" response={b} />
    </div>
    <Alert tone="info" title="Разность наклонов A − B · только описательная оценка">
      <p className="font-semibold">{difference === null ? 'Недостаточно общих полных лет' : formatSigned(difference, a.unit)} · общих полных лет: {data.comparison.years_used}.</p>
      <p>Наклон ряда годовых разностей A − B на общих полных годах; это не обязательно разность двух индивидуальных наклонов. Положительный знак: наклон A больше, чем B; отрицательный — меньше.</p>
      <p>Статистическая значимость различия не оценивается; доверительный интервал и p-значение для разности наклонов не рассчитываются.</p>
    </Alert>
  </>
}
