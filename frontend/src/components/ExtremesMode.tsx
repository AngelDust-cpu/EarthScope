import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { CloudRain, Snowflake, Thermometer } from 'lucide-react'
import { BISHKEK, REYKJAVIK } from '../hooks/useCoordinates'
import { extremeEvents, fetchExtremes, validExtremesQuery, type ExtremeEvent, type ExtremesResponse } from '../lib/extremes'
import { displayError, ru } from '../lib/ru'
import { AnalysisControlsPortal } from './AnalysisControlsPortal'
import { ExtremesResult } from './ExtremesResult'
import { HeatwavesMode } from './HeatwavesMode'
import { LocationMap } from './LocationMap'
import { Button } from './ui/button'
import { Field, TextInput } from './ui/field'

const decimal = (s: string) => s.trim() === '' ? NaN : Number(s.replace(',', '.').replace('−', '-'))
type Point = { latitude: string; longitude: string }
const eventIcons = { hot_days: Thermometer, cold_nights: Snowflake, heavy_rain: CloudRain }
const point = (p: { latitude: number; longitude: number }): Point => ({ latitude: String(p.latitude), longitude: String(p.longitude) })
const numeric = (p: Point) => ({ latitude: decimal(p.latitude), longitude: decimal(p.longitude) })
const validPoint = (p: Point) => Number.isFinite(decimal(p.latitude)) && Math.abs(decimal(p.latitude)) <= 90 && Number.isFinite(decimal(p.longitude)) && Math.abs(decimal(p.longitude)) <= 180

export function ExtremesMode({ active }: { active: boolean }) {
  const [submode, setSubmode] = useState<'days' | 'heatwaves'>('days')
  return <div hidden={!active}>
    {active && <AnalysisControlsPortal>
    <div className="research-segments" role="group" aria-label="Вид экстремальных событий">
      <Button type="button" variant="outline" aria-pressed={submode === 'days'} onClick={() => setSubmode('days')}>Отдельные дни</Button>
      <Button type="button" variant="outline" aria-pressed={submode === 'heatwaves'} onClick={() => setSubmode('heatwaves')}>Волны жары</Button>
    </div>
    </AnalysisControlsPortal>}
    <DailyExtremesMode active={active && submode === 'days'} />
    <HeatwavesMode active={active && submode === 'heatwaves'} />
  </div>
}

function DailyExtremesMode({ active }: { active: boolean }) {
  const id = useId()
  const [a, setA] = useState<Point>(point(BISHKEK)), [b, setB] = useState<Point>(point(REYKJAVIK))
  const [compare, setCompare] = useState(false), [selected, setSelected] = useState<'A' | 'B'>('A')
  const [event, setEvent] = useState<ExtremeEvent>('hot_days'), [threshold, setThreshold] = useState('30')
  const [start, setStart] = useState('1990'), [end, setEnd] = useState('2025')
  const [focus, setFocus] = useState(0)
  const [saved, setSaved] = useState<{ a: ExtremesResponse; b: ExtremesResponse | null; key: string } | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState<Error | null>(null)
  const [cancelled, setCancelled] = useState(false)
  const request = useRef<AbortController | null>(null)
  const shared = { startYear: decimal(start), endYear: decimal(end), event, threshold: decimal(threshold) }
  const query = { ...numeric(a), ...shared }
  const key = JSON.stringify({ ...query, b: compare ? numeric(b) : null })
  const validYears = /^\d{4}$/.test(start) && /^\d{4}$/.test(end) && query.startYear >= 1981 && query.endYear <= 2025 && query.startYear <= query.endYear
  const valid = validYears && validExtremesQuery(query) && (!compare || validPoint(b))
  const current = compare && selected === 'B' ? b : a
  const change = compare && selected === 'B' ? setB : setA
  const config = extremeEvents[event]
  const validThreshold = Number.isFinite(query.threshold) && query.threshold >= (event === 'heavy_rain' ? 0 : -100) && query.threshold <= (event === 'heavy_rain' ? 2000 : 100)
  function cancel() {
    request.current?.abort(); request.current = null; setLoading(false); setCancelled(true)
  }
  useEffect(() => {
    request.current?.abort(); request.current = null; setLoading(false); setError(null); setCancelled(false)
    return () => { request.current?.abort(); request.current = null }
  }, [active])
  async function run(e?: FormEvent) {
    e?.preventDefault()
    if (!active || !valid || request.current) return
    const controller = new AbortController()
    request.current = controller
    setLoading(true); setError(null); setCancelled(false)
    try {
      const first = fetchExtremes(query, { signal: controller.signal })
      const same = a.latitude === b.latitude && a.longitude === b.longitude ||
        (decimal(a.latitude) === decimal(b.latitude) && decimal(a.longitude) === decimal(b.longitude))
      const [resultA, resultB] = await Promise.all([first,
        compare ? same ? first : fetchExtremes({ ...numeric(b), ...shared }, { signal: controller.signal }) : Promise.resolve(null)])
      if (!controller.signal.aborted) setSaved({ a: resultA, b: resultB, key })
    } catch (reason) {
      if (!controller.signal.aborted) {
        setError(reason instanceof Error ? reason : new Error(String(reason)))
        controller.abort() // Stop the paired request too; never show a partial comparison.
      }
    } finally {
      if (request.current === controller) { request.current = null; setLoading(false) }
    }
  }
  if (!active) return null
  return <div className="point-workbench">
    <AnalysisControlsPortal>
    <aside aria-label="Параметры экстремальных событий" className="point-controls">
      <form onSubmit={run} noValidate className="space-y-5">
        <h2 className="font-semibold">Погодные экстремумы</h2>
        <p className="text-xs text-muted">Сколько дней пересекают выбранный абсолютный порог и как меняется их частота? Данные загружаются только по кнопке.</p>
        <fieldset className="min-w-0">
          <legend className="mb-2 text-sm font-medium">2. Событие</legend>
          <div className="grid auto-rows-fr grid-cols-1 gap-2">
          {(Object.keys(extremeEvents) as ExtremeEvent[]).map(value => {
            const Icon = eventIcons[value]
            return <Button key={value} type="button" variant="outline" aria-pressed={event === value}
              className="grid h-full min-h-[4.75rem] w-full grid-cols-[1.25rem_minmax(0,1fr)] content-start items-start justify-start gap-x-3 whitespace-normal text-left aria-pressed:border-action aria-pressed:bg-raised"
              onClick={() => { if (value !== event) { setEvent(value); setThreshold(String(extremeEvents[value].threshold)) } }}>
              <Icon aria-hidden="true" className="mt-0.5" />
              <span className="grid min-w-0 grid-rows-[auto_1fr] gap-1 break-words"><span className="font-medium leading-snug">{extremeEvents[value].label}</span><span className="text-xs leading-snug text-muted">{extremeEvents[value].parameter} {extremeEvents[value].operator} порог, {extremeEvents[value].unit}</span></span>
            </Button>
          })}
          </div>
        </fieldset>
        <Field htmlFor={`${id}-threshold`} label={`Порог (${config.unit}), строго ${config.operator}`}>
          <TextInput id={`${id}-threshold`} inputMode="decimal" value={threshold} aria-invalid={!validThreshold} aria-describedby={`${id}-threshold-hint`} onChange={e => setThreshold(e.target.value)} />
        </Field>
        <p id={`${id}-threshold-hint`} className="text-xs text-muted">{event === 'heavy_rain' ? 'Допустимо 0–2000 мм/сут.' : 'Допустимо −100…100 °C.'} Равенство не считается событием. {event === 'cold_nights' && 'Минимум за сутки UTC, не только за ночь.'}</p>
        <fieldset className="min-w-0 space-y-3">
          <legend className="mb-2 text-sm font-medium">3. Период (UTC, включительно)</legend>
          <div className="grid grid-cols-2 gap-2">
            <Field htmlFor={`${id}-start`} label="Первый год"><TextInput id={`${id}-start`} inputMode="numeric" value={start} aria-invalid={!validYears} onChange={e => setStart(e.target.value)} /></Field>
            <Field htmlFor={`${id}-end`} label="Последний год"><TextInput id={`${id}-end`} inputMode="numeric" value={end} aria-invalid={!validYears} onChange={e => setEnd(e.target.value)} /></Field>
          </div>
          <p className="text-xs text-muted">1981–2025. Тренд: ≥5 полных лет; ДИ и p: ≥20 последовательных полных лет и дополнительные проверки.</p>
        </fieldset>
        <label className="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" checked={compare} onChange={e => { setCompare(e.target.checked); setSelected('A') }} />Добавить точку B (описательно)</label>
        {compare && <fieldset className="space-y-2">
          <legend className="mb-2 text-sm font-medium">Точка для карты и ввода</legend>
          {(['A', 'B'] as const).map(value => <label key={value} className="flex min-h-10 items-center gap-2 text-sm"><input type="radio" name={`${id}-point`} checked={selected === value} onChange={() => setSelected(value)} />Точка {value}</label>)}
          <p className="text-xs text-muted">Единые событие, порог и годы для A и B.</p>
        </fieldset>}
        <fieldset className="min-w-0 space-y-3">
          <legend className="mb-2 text-sm font-medium">Координаты {compare ? selected : 'A'} · WGS84, градусы</legend>
          <div className="grid grid-cols-2 gap-2">
            <Field htmlFor={`${id}-lat`} label="Широта (−90…90)"><TextInput id={`${id}-lat`} inputMode="decimal" value={current.latitude} aria-invalid={!validPoint(current)} onChange={e => change({ ...current, latitude: e.target.value })} /></Field>
            <Field htmlFor={`${id}-lon`} label="Долгота (−180…180)"><TextInput id={`${id}-lon`} inputMode="decimal" value={current.longitude} aria-invalid={!validPoint(current)} onChange={e => change({ ...current, longitude: e.target.value })} /></Field>
          </div>
          <div className="flex flex-wrap gap-2">{[{ p: BISHKEK, title: 'Бишкек' }, { p: REYKJAVIK, title: 'Рейкьявик' }].map(({ p, title }) => <Button key={title} type="button" variant="outline" size="sm" onClick={() => { change(point(p)); setFocus(n => n + 1) }}>{title}</Button>)}</div>
        </fieldset>
        {!valid && <p role="status" className="text-xs">Проверьте обе координаты, диапазон лет и порог. Пустые поля недопустимы.</p>}
        <Button type="submit" className="w-full" disabled={!valid || loading}>{loading ? 'Загрузка…' : 'Рассчитать экстремумы'}</Button>
        {loading && <Button type="button" variant="outline" onClick={cancel}>Отменить запрос</Button>}
      </form>
    </aside>
    </AnalysisControlsPortal>
    <section aria-labelledby={`${id}-map`} className="point-map">
      <h2 id={`${id}-map`} className="point-map-header font-semibold">1. Точка {compare ? selected : 'A'} · WGS84</h2>
      <LocationMap selection={validPoint(current) ? numeric(current) : BISHKEK}
        onSelect={p => change(point(p))} focusRequest={focus}
        selectionLabel={compare ? selected : undefined}
        otherSelection={compare && validPoint(selected === 'A' ? b : a) ? { ...numeric(selected === 'A' ? b : a), label: selected === 'A' ? 'B' : 'A' } : undefined}
        describedBy={`${id}-map-hint`} className="point-map-viewport" />
      <p id={`${id}-map-hint`} className="point-map-hint">Клик меняет только выбранную точку. {ru.app.mapHint} Координаты можно ввести с клавиатуры. { !validPoint(current) && 'Ввод некорректен: карта временно показывает Бишкек.'}</p>
    </section>
    <main id="extremes-workspace" tabIndex={-1} aria-busy={loading} className="point-results space-y-4">
      <h2 className="text-xl font-semibold">Результаты · Погодные экстремумы</h2>
      <div role="status" className="space-y-2 text-sm text-secondary">
        {loading && <p>Загружаем суточный ряд NASA POWER. Параметры зафиксированы при запуске. {saved && 'Ниже — предыдущий результат.'}</p>}
        {cancelled && <p>Запрос отменён. {saved && 'Ниже — предыдущий результат.'}</p>}
        {saved && key !== saved.key && <p>{ru.app.draft}</p>}
        {!saved && !loading && !error && <p>Выберите событие, порог, годы и точку, затем нажмите «Рассчитать экстремумы».</p>}
      </div>
      {error && <div role="alert" className="space-y-2 rounded border border-line bg-card p-4"><p>{displayError(error)}</p><p className="text-xs">{saved ? 'Ниже сохранён предыдущий результат.' : 'Результат не получен.'} Частичное сравнение не показывается.</p><Button type="button" variant="outline" disabled={!valid || loading} onClick={() => void run()}>Повторить с текущими параметрами</Button></div>}
      {saved?.b && <section className="space-y-2 rounded border border-line bg-panel p-4 text-sm">
        <h3 className="font-semibold">A и B: независимые результаты</h3>
        <p>Сравнение только описательное. Раздельные p-значения не проверяют различие точек. Один абсолютный порог может иметь разный местный смысл.</p>
        {saved.a.trend && saved.b.trend && (saved.a.series.every((row, i) => (row.events === null) === (saved.b!.series[i]?.events === null))
          ? <p>Разность оценок наклонов A − B: {(saved.a.trend.per_decade - saved.b.trend.per_decade).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} дня на 365 дней за десятилетие. Оценки используют одни и те же полные годы.</p>
          : <p>Разность наклонов не показана: наборы полных лет различаются.</p>)}
      </section>}
      {saved && <section className="min-w-0 space-y-3" aria-label="Результат точки A"><h3 className="font-semibold">Точка A</h3><ExtremesResult data={saved.a} /></section>}
      {saved?.b && <section className="min-w-0 space-y-3" aria-label="Результат точки B"><h3 className="font-semibold">Точка B</h3><ExtremesResult data={saved.b} /></section>}
    </main>
  </div>
}
