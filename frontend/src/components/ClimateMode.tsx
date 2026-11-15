import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import type { AsyncState } from '../hooks/useAsyncData'
import { BISHKEK, REYKJAVIK, useCoordinates } from '../hooks/useCoordinates'
import { fetchAnomalies, fetchSeasonal, type AnomalyResponse, type SeasonalResponse, type Period } from '../lib/climate'
import { displayError, ru } from '../lib/ru'
import type { Coverage, Variable } from '../lib/types'
import { AnomalyResult, SeasonalResult } from './ClimateResults'
import { LocationMap } from './LocationMap'
import { Button } from './ui/button'
import { Field, Select, TextInput } from './ui/field'

export type ClimateModeName = 'anomalies' | 'seasonal'
interface Props {
  active: boolean
  mode: ClimateModeName
  coverage: AsyncState<Coverage> & { retry: () => void }
}
interface Saved<T> { data: T; key: string }
const decimal = (s: string) => s.trim() === '' ? NaN : Number(s.replace(',', '.').replace('−', '-'))

export function ClimateMode({ active, mode, coverage }: Props) {
  const id = useId()
  const point = useCoordinates(BISHKEK)
  const [lat, setLat] = useState(String(BISHKEK.latitude)), [lon, setLon] = useState(String(BISHKEK.longitude))
  const [variable, setVariable] = useState<Variable>('temperature')
  const [start, setStart] = useState('1990'), [end, setEnd] = useState('2025')
  const [baseStart, setBaseStart] = useState('1991'), [baseEnd, setBaseEnd] = useState('2020')
  const [period, setPeriod] = useState<Period>('all')
  const [focus, setFocus] = useState(0)
  const [anomalies, setAnomalies] = useState<Saved<AnomalyResponse> | null>(null)
  const [seasonal, setSeasonal] = useState<Saved<SeasonalResponse> | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState<Error | null>(null)
  const [cancelled, setCancelled] = useState(false)
  const request = useRef<AbortController | null>(null)
  const query = { latitude: decimal(lat), longitude: decimal(lon), variable, startYear: Number(start), endYear: Number(end) }
  const extra = mode === 'anomalies' ? { baselineStartYear: Number(baseStart), baselineEndYear: Number(baseEnd) } : { period }
  const key = JSON.stringify({ ...query, ...extra })
  const saved = mode === 'anomalies' ? anomalies : seasonal
  const min = coverage.data?.min_year ?? 1981, max = coverage.data?.max_year ?? 2025
  const validYears = (a: string, b: string) => /^\d{4}$/.test(a) && /^\d{4}$/.test(b) && Number(a) >= min && Number(b) <= max && Number(a) <= Number(b)
  const validPoint = Number.isFinite(query.latitude) && Math.abs(query.latitude) <= 90 && Number.isFinite(query.longitude) && Math.abs(query.longitude) <= 180
  const valid = validPoint && validYears(start, end) && (mode !== 'anomalies' || validYears(baseStart, baseEnd))

  function cancel() {
    request.current?.abort()
    request.current = null
    setLoading(false)
    setCancelled(true)
  }
  useEffect(() => {
    request.current?.abort()
    request.current = null
    setLoading(false)
    setError(null)
    setCancelled(false)
    return () => { request.current?.abort(); request.current = null }
  }, [active, mode])

  function select(latitude: number, longitude: number) {
    point.setNumeric(latitude, longitude)
    setLat(String(latitude)); setLon(String(longitude))
  }
  function commit() { if (validPoint) point.setNumeric(query.latitude, query.longitude) }
  async function run(event?: FormEvent) {
    event?.preventDefault()
    if (!valid || !coverage.data || request.current) return
    commit()
    const controller = new AbortController()
    request.current = controller
    setLoading(true); setError(null); setCancelled(false)
    try {
      if (mode === 'anomalies') {
        const data = await fetchAnomalies({ ...query, baselineStartYear: Number(baseStart), baselineEndYear: Number(baseEnd) }, { signal: controller.signal })
        if (!controller.signal.aborted) setAnomalies({ data, key })
      } else {
        const data = await fetchSeasonal({ ...query, period }, { signal: controller.signal })
        if (!controller.signal.aborted) setSeasonal({ data, key })
      }
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason : new Error(String(reason)))
    } finally {
      if (request.current === controller) { request.current = null; setLoading(false) }
    }
  }
  const yearField = (name: string, label: string, value: string, change: (s: string) => void, invalid: boolean) =>
    <Field htmlFor={`${id}-${name}`} label={label}>
      <TextInput id={`${id}-${name}`} inputMode="numeric" value={value} onChange={e => change(e.target.value)} aria-invalid={invalid} />
    </Field>

  if (!active) return null
  return <div>
    <div className="point-workbench">
      <section aria-labelledby={`${id}-map`} className="point-map">
        <h2 id={`${id}-map`} className="point-map-header font-semibold">1. Выберите точку</h2>
        <LocationMap selection={point.coordinates} onSelect={p => select(p.latitude, p.longitude)} focusRequest={focus}
          describedBy={`${id}-map-hint`} className="point-map-viewport" />
        <p id={`${id}-map-hint`} className="point-map-hint">{ru.app.mapHint}</p>
      </section>
      <aside aria-label="Параметры климатического анализа" className="point-controls">
        <form onSubmit={run} noValidate className="space-y-5">
          <h2 className="font-semibold">{ru.app.modes[mode]}</h2>
          <p className="text-xs text-muted">{mode === 'anomalies' ? 'Сравните каждый год с выбранной базой.' : 'Исследуйте сезонные и месячные изменения в одной точке.'} Данные загружаются только по кнопке.</p>
          <fieldset className="min-w-0 space-y-3">
            <legend className="mb-2 text-sm font-medium">Координаты WGS84, градусы</legend>
            <div className="grid grid-cols-2 gap-2">
              <Field htmlFor={`${id}-lat`} label="Широта"><TextInput id={`${id}-lat`} value={lat} inputMode="decimal" aria-invalid={!validPoint} onChange={e => setLat(e.target.value)} onBlur={commit} /></Field>
              <Field htmlFor={`${id}-lon`} label="Долгота"><TextInput id={`${id}-lon`} value={lon} inputMode="decimal" aria-invalid={!validPoint} onChange={e => setLon(e.target.value)} onBlur={commit} /></Field>
            </div>
            {!validPoint && <p className="text-xs" role="status">{ru.controls.coordinateError}</p>}
            <div className="flex flex-wrap gap-2">{[{ p: BISHKEK, title: 'Бишкек' }, { p: REYKJAVIK, title: 'Рейкьявик' }].map(({ p, title }) =>
              <Button key={title} type="button" size="sm" variant="outline" onClick={() => { select(p.latitude, p.longitude); setFocus(n => n + 1) }}>{title}</Button>)}</div>
          </fieldset>
          <Field htmlFor={`${id}-variable`} label="2. Показатель">
            <Select id={`${id}-variable`} value={variable} onChange={e => setVariable(e.target.value as Variable)}>
              <option value="temperature">Температура воздуха (°C)</option><option value="precipitation">Осадки (мм за период)</option>
            </Select>
          </Field>
          <fieldset className="min-w-0 space-y-2">
            <legend className="mb-2 text-sm font-medium">3. Период отображения (UTC)</legend>
            <div className="grid grid-cols-2 gap-2">
              {yearField('start', 'Первый год', start, setStart, !validYears(start, end))}
              {yearField('end', 'Последний год', end, setEnd, !validYears(start, end))}
            </div>
            <p className="text-xs text-muted">Целые годы {min}–{max}, включительно; конец не раньше начала. Для тренда — минимум 5 полных лет.</p>
          </fieldset>
          {mode === 'anomalies' ? <fieldset className="min-w-0 space-y-3">
            <legend className="mb-2 text-sm font-medium">4. Базовый период</legend>
            <Button type="button" variant="outline" size="sm" onClick={() => { setBaseStart('1991'); setBaseEnd('2020') }}>База 1991–2020 (30 лет)</Button>
            <div className="grid grid-cols-2 gap-2">
              {yearField('base-start', 'Начало базы', baseStart, setBaseStart, !validYears(baseStart, baseEnd))}
              {yearField('base-end', 'Конец базы', baseEnd, setBaseEnd, !validYears(baseStart, baseEnd))}
            </div>
            <p className="text-xs text-muted">База может быть вне периода отображения, но в пределах {min}–{max}. {ru.climate.reference}</p>
          </fieldset> : <fieldset className="min-w-0 space-y-3">
            <legend className="mb-2 text-sm font-medium">4. Сезон или месяц</legend>
            <div role="group" aria-label="Календарная группировка" className="grid grid-cols-2 gap-2">
              <Button type="button" variant={period.startsWith('m') ? 'outline' : 'default'} aria-pressed={!period.startsWith('m')} onClick={() => setPeriod('all')}>Сезоны</Button>
              <Button type="button" variant={period.startsWith('m') ? 'default' : 'outline'} aria-pressed={period.startsWith('m')} onClick={() => setPeriod('m01')}>Месяцы</Button>
            </div>
            <div role="group" aria-label={period.startsWith('m') ? 'Месяц' : 'Сезон'} className="grid grid-cols-2 gap-2">
              {Object.entries(ru.climate.periods).filter(([value]) => value.startsWith('m') === period.startsWith('m'))
                .map(([value, label]) => <Button key={value} type="button" size="sm"
                  className="h-auto min-h-10 whitespace-normal"
                  variant={value === period ? 'default' : 'outline'} aria-pressed={value === period}
                  onClick={() => setPeriod(value as Period)}>{label}</Button>)}
            </div>
            <p className="text-xs text-muted">Названия сезонов соответствуют календарным группам Северного полушария. {ru.climate.winter}</p>
          </fieldset>}
          {!valid && <p role="status" className="text-xs">Проверьте координаты и диапазоны лет. Все поля должны быть заполнены.</p>}
          <Button type="submit" className="w-full" disabled={!valid || !coverage.data || loading}>{loading ? 'Выполняется анализ…' : 'Провести анализ'}</Button>
          {loading && <Button type="button" variant="outline" onClick={cancel}>Отменить запрос</Button>}
          {coverage.loading && <p role="status" className="text-xs">{ru.app.checking}</p>}
          {coverage.error && <div role="alert" className="text-xs"><p>{displayError(coverage.error)}</p><Button type="button" variant="outline" onClick={coverage.retry}>Повторить проверку периода</Button></div>}
        </form>
      </aside>
      <main id="climate-workspace" tabIndex={-1} aria-busy={loading} className="point-results space-y-4">
        <h2 className="text-xl font-semibold">Результаты · {ru.app.modes[mode]}</h2>
        <div role="status" className="space-y-2 text-sm text-secondary">
          {loading && <p>Загружаем суточный ряд NASA POWER… Параметры запроса зафиксированы при запуске.{saved && ' Ниже — предыдущий результат.'}</p>}
          {cancelled && <p>Запрос отменён. {saved && 'Ниже — предыдущий результат.'}</p>}
          {saved && key !== saved.key && <p>{ru.app.draft}</p>}
          {!saved && !loading && !error && <p>Анализ ещё не запущен. Выберите точку и период, затем нажмите «Провести анализ».</p>}
        </div>
        {error && <div role="alert" className="space-y-2 rounded border border-line bg-card p-4">
          <p>{displayError(error)}</p><p className="text-xs">{saved ? 'Сохранён предыдущий результат.' : 'Результат не получен.'} Повтор отправит текущие параметры формы.</p>
          <Button type="button" variant="outline" disabled={loading || !valid} onClick={() => void run()}>Повторить с текущими параметрами</Button>
        </div>}
        {mode === 'anomalies' && anomalies && <AnomalyResult data={anomalies.data} />}
        {mode === 'seasonal' && seasonal && <SeasonalResult data={seasonal.data} />}
      </main>
    </div>
  </div>
}
