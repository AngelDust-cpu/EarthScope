import { useEffect, useRef, useState } from 'react'
import { CalendarDays, CloudRain, GitCompareArrows, Thermometer, Waves } from 'lucide-react'
import { BISHKEK, REYKJAVIK, useCoordinates, type Coordinates } from '../hooks/useCoordinates'
import type { AsyncState } from '../hooks/useAsyncData'
import { fetchCompare, fetchTrend } from '../lib/api'
import { fetchAnomalies, fetchSeasonal } from '../lib/climate'
import { fetchExtremes } from '../lib/extremes'
import { fetchHeatwaves, type HeatwavesResponse } from '../lib/heatwaves'
import { HeatwavesResult } from './HeatwavesResult'
import type { ExtremesResponse } from '../lib/extremes'
import { interpretComparison, interpretTrend } from '../lib/detective'
import { formatCoordinate, formatValue } from '../lib/format'
import { displayError, variableLabel } from '../lib/ru'
import type { CompareResponse, Coverage, TrendResponse, Variable } from '../lib/types'
import { ControlPanel } from './ControlPanel'
import { ComparisonBody } from './ComparisonBody'
import { AnomalyResult, SeasonalResult } from './ClimateResults'
import { ExtremesResult } from './ExtremesResult'
import { DashboardBody } from './DashboardBody'
import { LocationMap } from './LocationMap'
import { AnalysisControlsPortal } from './AnalysisControlsPortal'
import { Button } from './ui/button'
import { Field, Select, TextInput } from './ui/field'
import { Alert } from './ui/feedback'
import { DetectiveReportContext } from './PdfDownloadButton'
import { ProfileMode } from './ProfileMode'

type Question = 'temperature' | 'precipitation' | 'compare' | 'anomalies' | 'seasonal' | 'extremes' | 'heatwaves' | 'profile'
type ClimateResult = Awaited<ReturnType<typeof fetchAnomalies>> | Awaited<ReturnType<typeof fetchSeasonal>> | Awaited<ReturnType<typeof fetchExtremes>> | HeatwavesResponse
const steps = ['Вопрос', 'Координаты', 'Период', 'Результаты']
const questions = [
  { value: 'profile', title: 'Как менялся климат моего региона?', description: 'Единый профиль: температура, осадки, аномалии, сезоны и пороговые события.', icon: CalendarDays },
  { value: 'temperature', title: 'Изменение температуры', description: 'Узнайте, как менялась температура в выбранной точке.', icon: Thermometer },
  { value: 'precipitation', title: 'Изменение осадков', description: 'Исследуйте, как менялось количество осадков в выбранной точке.', icon: CloudRain },
  { value: 'compare', title: 'Сравнение регионов', description: 'Сравните изменения температуры или осадков в точках A и B.', icon: GitCompareArrows },
  { value: 'anomalies', title: 'Какие годы были необычно тёплыми или холодными?', description: 'Сравните среднюю температуру каждого года с базовым периодом.', icon: Waves },
  { value: 'seasonal', title: 'Какой сезон меняется сильнее всего?', description: 'Сопоставьте оценки трендов четырёх календарных сезонов.', icon: CalendarDays },
  { value: 'extremes', title: 'Стало ли больше экстремально жарких дней?', description: 'Посчитайте дни, когда суточный максимум выше выбранного порога.', icon: Thermometer },
  { value: 'heatwaves', title: 'Стали ли волны жары чаще и продолжительнее?', description: 'Найдите серии жарких суток и сравните число и длительность эпизодов по годам начала.', icon: Thermometer },
] as const

function AnomalyAnswer({ data }: { data: Awaited<ReturnType<typeof fetchAnomalies>> }) {
  const observed = data.series.flatMap(({ year, anomaly }) => anomaly === null ? [] : [{ year, anomaly }])
  const high = Math.max(0, ...observed.map(row => row.anomaly))
  const low = Math.min(0, ...observed.map(row => row.anomaly))
  const list = (value: number) => observed.filter(row => row.anomaly === value).map(row => row.year).join(', ')
  return <section className="rounded border border-line bg-card p-4 text-sm">
    <h3 className="mb-2 font-semibold">Что удалось выяснить</h3>
    {observed.length ? <>
      <p>{high > 0 ? `Самое большое отклонение в тёплую сторону: ${list(high)} · +${formatValue(high, data.unit)}.` : 'Нет полных лет теплее базы.'}</p>
      <p>{low < 0 ? `Самое большое отклонение в холодную сторону: ${list(low)} · ${formatValue(low, data.unit)}.` : 'Нет полных лет холоднее базы.'}</p>
      <p className="mt-2 text-muted">Это самые большие отклонения среди доступных лет. Они не показывают, насколько редким было такое значение.</p>
    </> : <p>Недостаточно полных данных для сравнения с базовым периодом.</p>}
  </section>
}

function HeatAnswer({ data }: { data: ExtremesResponse }) {
  const trend = data.trend
  let answer = 'Недостаточно полных лет для оценки изменения частоты.'
  if (trend) {
    const direction = trend.per_decade > 0 ? 'частота выросла' : trend.per_decade < 0 ? 'частота снизилась' : 'наклон равен нулю'
    const evidence = trend.significant === null ? 'Статистический вывод недоступен.'
      : trend.significant ? 'Основной метод указывает на ненулевой тренд.'
        : 'Основной метод не подтверждает изменение при пороге 0,05. Это не доказывает, что частота не менялась.'
    answer = `По оценке модели ${direction}. ${evidence}`
  }
  return <section className="rounded border border-line bg-card p-4 text-sm">
    <h3 className="mb-2 font-semibold">Что удалось выяснить</h3>
    <p>{answer} Вывод относится к выбранному порогу {formatValue(data.threshold, '°C')} и этому периоду.</p>
  </section>
}

export function DetectiveMode({ active, coverage }: { active: boolean; coverage: AsyncState<Coverage> & { retry: () => void } }) {
  const a = useCoordinates(BISHKEK), b = useCoordinates(REYKJAVIK)
  const [question, setQuestion] = useState<Question | null>(null)
  const [variable, setVariable] = useState<Variable>('temperature')
  const [point, setPoint] = useState<'A' | 'B'>('A')
  const [step, setStep] = useState(0)
  const [start, setStart] = useState(1990), [end, setEnd] = useState(2025)
  const [heatLimit, setHeatLimit] = useState('30')
  const [minDuration, setMinDuration] = useState('3')
  const [baselineStart, setBaselineStart] = useState('1991')
  const [baselineEnd, setBaselineEnd] = useState('2020')
  const [focus, setFocus] = useState(0)
  const [cancelled, setCancelled] = useState(false)
  const [trend, setTrend] = useState<AsyncState<TrendResponse>>({ data: null, error: null, loading: false })
  const [compare, setCompare] = useState<AsyncState<CompareResponse>>({ data: null, error: null, loading: false })
  const [climate, setClimate] = useState<AsyncState<ClimateResult>>({ data: null, error: null, loading: false })
  const [saved, setSaved] = useState<Partial<Record<Question, { key: string; data: TrendResponse | CompareResponse | ClimateResult }>>>({})
  const request = useRef<AbortController | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const parameters = useRef<HTMLElement>(null)
  const previousStep = useRef(step)
  const isCompare = question === 'compare'
  const isClimate = question === 'anomalies' || question === 'seasonal' || question === 'extremes' || question === 'heatwaves'
  const threshold = /^[+\-−]?(?:\d+([.,]\d*)?|[.,]\d+)$/.test(heatLimit.trim())
    ? Number(heatLimit.replace(',', '.').replace('−', '-')) : NaN
  const thresholdValid = Number.isFinite(threshold) && (question === 'heatwaves' || Math.abs(threshold) <= 100)
  const durationValid = /^\d+$/.test(minDuration) && Number.isSafeInteger(Number(minDuration)) && Number(minDuration) >= 1
  const selectedVariable: Variable = isCompare || question === 'seasonal' ? variable : question === 'precipitation' ? question : 'temperature'
  const baselineValid = /^\d{4}$/.test(baselineStart) && /^\d{4}$/.test(baselineEnd) &&
    Number(baselineStart) >= (coverage.data?.min_year ?? 1981) &&
    Number(baselineEnd) <= (coverage.data?.max_year ?? 2025) && baselineStart <= baselineEnd
  const selected = isCompare && point === 'B' ? b : a
  const key = JSON.stringify([question, selectedVariable, a.coordinates, isCompare ? b.coordinates : null, start, end,
    ...(question === 'anomalies' ? [baselineStart, baselineEnd] : []),
    ...(question === 'extremes' || question === 'heatwaves' ? [heatLimit] : []),
    ...(question === 'heatwaves' ? [minDuration] : [])])
  const state = isClimate ? climate : isCompare ? compare : trend
  const loading = trend.loading || compare.loading || climate.loading
  const result = question ? saved[question] : undefined
  const current = result?.key === key && !loading && !state.error && !cancelled ? result.data : null

  function cancel() {
    const pending = request.current
    request.current = null
    pending?.abort()
    setCancelled(true)
    setTrend(s => ({ ...s, loading: false }))
    setCompare(s => ({ ...s, loading: false }))
    setClimate(s => ({ ...s, loading: false }))
  }
  function newInvestigation() {
    // Invalidate synchronously before aborting: late responses cannot restore results.
    const pending = request.current
    request.current = null
    pending?.abort()
    setSaved({})
    setTrend({ data: null, error: null, loading: false })
    setCompare({ data: null, error: null, loading: false })
    setClimate({ data: null, error: null, loading: false })
    setCancelled(false)
    setQuestion(null)
    setVariable('temperature')
    setPoint('A')
    a.applyPreset(BISHKEK)
    b.applyPreset(REYKJAVIK)
    setStart(1990)
    setEnd(2025)
    setHeatLimit('30')
    setMinDuration('3')
    setBaselineStart('1991')
    setBaselineEnd('2020')
    setFocus(0)
    setStep(0)
  }
  useEffect(() => {
    if (!active && request.current) cancel()
  }, [active])
  useEffect(() => () => { request.current?.abort(); request.current = null }, [])
  useEffect(() => {
    if (active && previousStep.current !== step) (parameters.current ?? heading.current)?.focus()
    previousStep.current = step
  }, [step, active])
  useEffect(() => { setCancelled(false) }, [key])

  async function run() {
    if (request.current || !coverage.data || !question || (question === 'anomalies' && !baselineValid) ||
      ((question === 'extremes' || question === 'heatwaves') && !thresholdValid) ||
      (question === 'heatwaves' && !durationValid)) return
    const controller = new AbortController()
    request.current = controller
    setStep(3)
    setCancelled(false)
    setTrend({ data: null, error: null, loading: !isCompare && !isClimate })
    setCompare({ data: null, error: null, loading: isCompare })
    setClimate({ data: null, error: null, loading: isClimate })
    try {
      if (isClimate) {
        const query = { ...a.coordinates, variable: selectedVariable, startYear: start, endYear: end }
        const data = question === 'anomalies'
          ? await fetchAnomalies({ ...query, baselineStartYear: Number(baselineStart), baselineEndYear: Number(baselineEnd) }, { signal: controller.signal })
            : question === 'heatwaves'
              ? await fetchHeatwaves({ ...query, threshold, minDuration: Number(minDuration) }, { signal: controller.signal })
            : question === 'seasonal'
            ? await fetchSeasonal({ ...query, period: 'all' }, { signal: controller.signal })
            : await fetchExtremes({ ...query, event: 'hot_days', threshold }, { signal: controller.signal })
        if (controller.signal.aborted || request.current !== controller) return
        setClimate({ data, error: null, loading: false })
        setSaved(previous => ({ ...previous, [question]: { key, data } }))
      } else if (isCompare) {
        const data = await fetchCompare({ point_a: a.coordinates, point_b: b.coordinates,
          variable: selectedVariable, start_year: start, end_year: end }, { signal: controller.signal })
        if (controller.signal.aborted || request.current !== controller) return
        setCompare({ data, error: null, loading: false })
        setSaved(previous => ({ ...previous, [question]: { key, data } }))
      } else {
        const data = await fetchTrend({ ...a.coordinates, variable: selectedVariable, startYear: start, endYear: end }, { signal: controller.signal })
        if (controller.signal.aborted || request.current !== controller) return
        setTrend({ data, error: null, loading: false })
        setSaved(previous => ({ ...previous, [question]: { key, data } }))
      }
    } catch (error) {
      if (controller.signal.aborted || request.current !== controller) return
      const failure = error instanceof Error ? error : new Error(String(error))
      if (isClimate) setClimate({ data: null, error: failure, loading: false })
      else if (isCompare) setCompare({ data: null, error: failure, loading: false })
      else setTrend({ data: null, error: failure, loading: false })
    } finally {
      if (request.current === controller) request.current = null
    }
  }
  function commit(p: Coordinates) { selected.setNumeric(p.latitude, p.longitude) }
  const currentComparison = current && 'point_a' in current ? current : null
  const currentTrend = current && 'trend' in current && !('event' in current) ? current : null
  const explanations = currentComparison ? interpretComparison(currentComparison)
    : currentTrend ? interpretTrend(currentTrend) : []

  if (question === 'profile') return <DetectiveReportContext.Provider value={true}><ProfileMode active={active} coverage={coverage} detective onExit={newInvestigation} /></DetectiveReportContext.Provider>

  return <main id="detective-workspace" tabIndex={-1} hidden={!active} className="research-detective space-y-5">
    <div>
      <h2 ref={heading} tabIndex={-1} className="text-xl font-semibold">Детектив · {steps[step]}</h2>
      <p className="mt-2 text-sm text-muted">Выберите вопрос, место и период — получите ответ по данным NASA POWER с графиком и ограничениями. Это не прогноз.</p>
      <ol aria-label="Шаги исследования" className="research-step-list mt-3 text-sm">
        {steps.map((label, i) => <li key={label} aria-current={step === i ? 'step' : undefined} className={step === i ? 'font-semibold text-ink' : 'text-muted'}>{i + 1}. {label}</li>)}
      </ol>
      {step > 0 && <Button type="button" variant="outline" className="mt-3" onClick={newInvestigation}>Новое исследование</Button>}
    </div>
    <section hidden={step !== 0} className="space-y-4">
      <fieldset className="research-questions min-w-0">
        <legend className="mb-3 font-semibold">Что будем исследовать?</legend>
        {questions.map(({ value, title, description, icon: Icon }) =>
          <Button key={value} type="button" variant="outline"
            aria-labelledby={`detective-question-${value}`}
            aria-describedby={`detective-description-${value}`}
            className="h-auto min-h-20 w-full justify-start whitespace-normal p-4 text-left active:border-focus active:bg-panel motion-reduce:transition-none sm:h-auto"
            onClick={() => { setQuestion(value); setStep(1) }}>
            <Icon aria-hidden="true" className="size-5" />
            <span className="min-w-0">
              <span id={`detective-question-${value}`} className="block font-semibold">{title}</span>
              <span id={`detective-description-${value}`} className="mt-1 block text-sm font-normal text-muted">{description}</span>
            </span>
          </Button>)}
      </fieldset>
    </section>
    {active && (step === 1 || step === 2) && <>
      <AnalysisControlsPortal>
        <section ref={parameters} tabIndex={-1} className="point-controls space-y-4" aria-label="Параметры исследования детектива">
          <p className="text-sm">{variableLabel(selectedVariable)} · {start}–{end} UTC</p>
          {(isCompare || question === 'seasonal') && step === 1 && <Field htmlFor="detective-variable" label={isCompare ? 'Переменная сравнения' : 'Показатель'}>
            <Select id="detective-variable" value={variable} onChange={e => setVariable(e.target.value as Variable)}>
              <option value="temperature">Температура</option><option value="precipitation">Осадки</option>
            </Select>
          </Field>}
          {isCompare && <fieldset hidden={step !== 1} className="space-y-2">
            <legend className="mb-2 font-semibold">Активная точка: карта, ввод и пресеты</legend>
            {(['A', 'B'] as const).map(value => <label key={value} className="flex min-h-11 items-center gap-2">
              <input type="radio" name="detective-point" checked={point === value} onChange={() => setPoint(value)} />
              {value}: {formatCoordinate((value === 'A' ? a : b).coordinates.latitude)}, {formatCoordinate((value === 'A' ? a : b).coordinates.longitude)}°
            </label>)}
          </fieldset>}
          {question === 'anomalies' && step === 2 && <fieldset className="space-y-2">
            <legend className="font-medium">Базовый период</legend>
            <Button type="button" variant="outline" onClick={() => { setBaselineStart('1991'); setBaselineEnd('2020') }}>1991–2020</Button>
            <Field htmlFor="detective-baseline-start" label="Начало базы"><TextInput id="detective-baseline-start" inputMode="numeric" value={baselineStart} onChange={e => setBaselineStart(e.target.value)} /></Field>
            <Field htmlFor="detective-baseline-end" label="Конец базы"><TextInput id="detective-baseline-end" inputMode="numeric" value={baselineEnd} onChange={e => setBaselineEnd(e.target.value)} /></Field>
            {!baselineValid && <p role="alert" className="text-sm text-warning">Проверьте границы базового периода.</p>}
          </fieldset>}
          {(question === 'extremes' || question === 'heatwaves') && step === 2 && <Field htmlFor="detective-heat-limit" label="Порог жары, °C" hint={question === 'heatwaves' ? 'Суточная Tmax строго выше порога; конечное число.' : 'Учитываются только дни строго выше порога. Допустимо от −100 до +100 °C.'}>
            <TextInput id="detective-heat-limit" inputMode="decimal" value={heatLimit} aria-invalid={!thresholdValid} onChange={e => setHeatLimit(e.target.value)} />
          </Field>}
          {question === 'heatwaves' && step === 2 && <Field htmlFor="detective-heat-duration" label="Минимум суток подряд" hint="Целое число ≥1. Поиск через 31 декабря; статистика — по году начала. Внешние границы окна могут обрезать эпизоды.">
            <TextInput id="detective-heat-duration" inputMode="numeric" value={minDuration} aria-invalid={!durationValid} onChange={e => setMinDuration(e.target.value)} />
          </Field>}
          <ControlPanel stage={step === 1 ? 'coordinates' : 'period'} onContinue={() => setStep(2)}
            coverage={coverage.data} coverageError={coverage.error} coordinates={selected.coordinates}
            pointLabel={`Точка ${isCompare ? point : 'A'} · WGS84, градусы`}
            onCoordinatesCommit={commit} variable={selectedVariable} onVariableChange={setVariable}
            startYear={start} endYear={end} onRangeChange={(first, last) => { setStart(first); setEnd(last) }}
            onPreset={p => { selected.applyPreset(p); setFocus(n => n + 1) }}
            loading={loading} submitDisabled={(question === 'anomalies' && !baselineValid) ||
              ((question === 'extremes' || question === 'heatwaves') && !thresholdValid) ||
              (question === 'heatwaves' && !durationValid)} onSubmit={() => void run()} />
          {step === 2 && current && <Button variant="outline" onClick={() => setStep(3)}>Открыть сохранённый ответ</Button>}
          {coverage.loading && <p role="status" className="text-sm text-muted">Проверяем доступный период…</p>}
          {coverage.error && <Button variant="outline" onClick={coverage.retry}>Повторить проверку периода</Button>}
        </section>
      </AnalysisControlsPortal>
        <section hidden={step !== 1} aria-label="Карта выбора координат" className="min-w-0">
          {active && step === 1 && <LocationMap selection={selected.coordinates} onSelect={p => selected.setFromMap(p.latitude, p.longitude)}
            focusRequest={focus} selectionLabel={isCompare ? point : 'A'}
            otherSelection={isCompare ? { ...(point === 'A' ? b : a).coordinates, label: point === 'A' ? 'B' : 'A' } : undefined}
            describedBy="detective-map-hint" className="h-[300px] w-full sm:h-[440px]" />}
          <p id="detective-map-hint" className="mt-2 text-xs text-muted">Клик меняет только активную точку. Для клавиатуры используйте поля координат или пресеты. Запрос анализа не отправляется.</p>
        </section>
    </>}
    {active && step === 3 && <DetectiveReportContext.Provider value={true}><section aria-label="Ответ детектива" className="space-y-4">
      <p className="text-sm">{variableLabel(selectedVariable)} · {start}–{end} UTC · A: {formatCoordinate(a.coordinates.latitude)}, {formatCoordinate(a.coordinates.longitude)}°
        {isCompare ? ` · B: ${formatCoordinate(b.coordinates.latitude)}, ${formatCoordinate(b.coordinates.longitude)}°` : ''}</p>
      {loading && <Button variant="outline" onClick={cancel}>Отменить</Button>}
      {cancelled && <p role="status">Анализ отменён. Текущего ответа нет.</p>}
      {!current && !loading && !state.error && !cancelled && <p role="status">Параметры изменены или анализ ещё не выполнен. Запустите анализ для текущего вопроса.</p>}
      {explanations.length > 0 && <div className="rounded border border-line bg-card p-4">
        <h3 className="mb-2 font-semibold">Что удалось выяснить</h3>
        <ul className="list-disc space-y-2 pl-5 text-sm">{explanations.map((text, i) => <li key={i}>{text}</li>)}</ul>
      </div>}
      {coverage.error && <div role="alert" className="space-y-2 text-sm">
        <p>Не удалось проверить доступный период. Для повторного анализа сначала восстановите проверку периода.</p>
        <Button variant="outline" onClick={coverage.retry}>Повторить проверку периода</Button>
      </div>}
      {isClimate ? <>
        {loading && <p role="status">Загружаем данные NASA POWER…</p>}
        {climate.error && <Alert tone="error" title="Исследование не выполнено"
          action={<Button variant="outline" onClick={coverage.error ? coverage.retry : () => void run()}>Повторить запрос</Button>}>{displayError(climate.error)}</Alert>}
        {current && 'baseline_mean' in current && <><AnomalyAnswer data={current} /><AnomalyResult data={current} /></>}
        {current && 'periods' in current && <SeasonalResult data={current} />}
        {current && 'event' in current && <><HeatAnswer data={current} /><ExtremesResult data={current} /></>}
        {current && 'episodes' in current && <>
          <section className="rounded border border-line bg-card p-4 text-sm">
            <h3 className="mb-2 font-semibold">Что удалось выяснить</h3>
            <ul className="mb-2 list-disc pl-5">
              {([
                ['count', 'Число эпизодов', 'эпизодов'],
                ['total_days', 'Суммарная длительность', 'дней'],
                ['max_duration', 'Максимальная длительность', 'дней'],
              ] as const).map(([metric, label, unit]) => {
                const slope = current.trends[metric]?.per_decade
                return <li key={metric}>{label} · {slope === undefined ? 'оценка недоступна'
                  : `${slope.toLocaleString('ru-RU', { maximumFractionDigits: 3, signDisplay: 'exceptZero' })} ${unit}/десятилетие`}</li>
              })}
            </ul>
            <p>Это описательные наклоны. Статистическая значимость не оценивалась.</p>
            <p>Порог {current.threshold} °C, минимум {current.min_duration} суток подряд. Оцениваются годы начала эпизодов; границы окна могут обрезать эпизоды.</p>
          </section>
          <HeatwavesResult data={current} />
        </>}
      </> : isCompare ? <ComparisonBody state={{ ...compare, data: currentComparison }} onRetry={coverage.error ? coverage.retry : () => void run()} />
        : <DashboardBody coverage={coverage.data} state={{ ...trend, data: currentTrend }}
          metaDescription="NASA POWER · сеточный реанализ · полные календарные годы UTC" onRetry={coverage.error ? coverage.retry : () => void run()} />}
      {!loading && !state.error && <Button onClick={() => void run()} disabled={!coverage.data}>Повторить анализ</Button>}
    </section></DetectiveReportContext.Provider>}
    {step > 0 && <Button variant="outline" disabled={loading} onClick={() => { setCancelled(false); setStep(step - 1) }}>Назад</Button>}
  </main>
}
