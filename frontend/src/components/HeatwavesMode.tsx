import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { BISHKEK, REYKJAVIK } from '../hooks/useCoordinates'
import { fetchHeatwaves, validHeatwavesQuery, type HeatwavesResponse } from '../lib/heatwaves'
import { displayError, ru } from '../lib/ru'
import { HeatwavesResult } from './HeatwavesResult'
import { LocationMap } from './LocationMap'
import { Button } from './ui/button'
import { Field, TextInput } from './ui/field'

const decimal = (v: string) => v.trim() === '' ? NaN : Number(v.replace(',', '.').replace('−', '-'))
export function HeatwavesMode({ active }: { active: boolean }) {
  const id = useId()
  const [latitude, setLatitude] = useState(String(BISHKEK.latitude)), [longitude, setLongitude] = useState(String(BISHKEK.longitude))
  const [start, setStart] = useState('1990'), [end, setEnd] = useState('2025')
  const [threshold, setThreshold] = useState('30'), [duration, setDuration] = useState('3')
  const [focus, setFocus] = useState(0)
  const [saved, setSaved] = useState<{ data: HeatwavesResponse; key: string } | null>(null)
  const [loading, setLoading] = useState(false), [error, setError] = useState<Error | null>(null), [cancelled, setCancelled] = useState(false)
  const request = useRef<AbortController | null>(null)
  const query = { latitude: decimal(latitude), longitude: decimal(longitude), startYear: decimal(start), endYear: decimal(end), threshold: decimal(threshold), minDuration: decimal(duration) }
  const valid = validHeatwavesQuery(query)
  const key = JSON.stringify(query)
  const validPoint = Number.isFinite(query.latitude) && Math.abs(query.latitude) <= 90 && Number.isFinite(query.longitude) && Math.abs(query.longitude) <= 180
  function setPoint(p: { latitude: number; longitude: number }) { setLatitude(String(p.latitude)); setLongitude(String(p.longitude)) }
  function cancel() { request.current?.abort(); request.current = null; setLoading(false); setCancelled(true) }
  useEffect(() => {
    if (!active) { request.current?.abort(); request.current = null; setLoading(false) }
    return () => { request.current?.abort(); request.current = null }
  }, [active])
  async function run(e?: FormEvent) {
    e?.preventDefault()
    if (!active || !valid || request.current) return
    const pending = new AbortController(); request.current = pending
    setLoading(true); setError(null); setCancelled(false)
    try {
      const data = await fetchHeatwaves(query, { signal: pending.signal })
      if (!pending.signal.aborted && request.current === pending) setSaved({ data, key })
    } catch (reason) {
      if (!pending.signal.aborted && request.current === pending) setError(reason instanceof Error ? reason : new Error(String(reason)))
    } finally { if (request.current === pending) { request.current = null; setLoading(false) } }
  }
  if (!active) return null
  return <div className="point-workbench">
    <aside className="point-controls" aria-label="Параметры волн жары">
      <form onSubmit={run} noValidate className="space-y-4">
        <h2 className="font-semibold">Волны жары</h2>
        <p className="text-xs text-muted">Стали ли волны жары чаще и продолжительнее? Найдите серии жарких суток, а не отдельные жаркие дни. Запрос — только по кнопке.</p>
        <fieldset className="grid min-w-0 grid-cols-2 gap-2"><legend className="mb-2 text-sm">Точка · WGS84, градусы</legend>
          <Field htmlFor={`${id}-lat`} label="Широта (−90…90)"><TextInput id={`${id}-lat`} inputMode="decimal" value={latitude} aria-invalid={!Number.isFinite(query.latitude) || Math.abs(query.latitude) > 90} onChange={e => setLatitude(e.target.value)} /></Field>
          <Field htmlFor={`${id}-lon`} label="Долгота (−180…180)"><TextInput id={`${id}-lon`} inputMode="decimal" value={longitude} aria-invalid={!Number.isFinite(query.longitude) || Math.abs(query.longitude) > 180} onChange={e => setLongitude(e.target.value)} /></Field>
        </fieldset>
        <div className="flex flex-wrap gap-2">{[{ p: BISHKEK, title: 'Бишкек' }, { p: REYKJAVIK, title: 'Рейкьявик' }].map(({ p, title }) => <Button type="button" variant="outline" size="sm" key={title} onClick={() => { setPoint(p); setFocus(n => n + 1) }}>{title}</Button>)}</div>
        <fieldset className="grid min-w-0 grid-cols-2 gap-2"><legend className="mb-2 text-sm">Годы UTC · 1981–2025</legend>
          <Field htmlFor={`${id}-start`} label="Первый год"><TextInput id={`${id}-start`} inputMode="numeric" value={start} aria-invalid={!Number.isInteger(query.startYear) || query.startYear < 1981 || query.startYear > query.endYear} onChange={e => setStart(e.target.value)} /></Field>
          <Field htmlFor={`${id}-end`} label="Последний год"><TextInput id={`${id}-end`} inputMode="numeric" value={end} aria-invalid={!Number.isInteger(query.endYear) || query.endYear > 2025 || query.endYear < query.startYear} onChange={e => setEnd(e.target.value)} /></Field>
        </fieldset>
        <Field htmlFor={`${id}-threshold`} label="Порог Tmax, °C" hint="Строго выше порога; конечное число."><TextInput id={`${id}-threshold`} inputMode="decimal" value={threshold} aria-invalid={!Number.isFinite(query.threshold)} onChange={e => setThreshold(e.target.value)} /></Field>
        <Field htmlFor={`${id}-duration`} label="Минимум суток подряд" hint="Целое число ≥1; по умолчанию 3."><TextInput id={`${id}-duration`} inputMode="numeric" value={duration} aria-invalid={!Number.isSafeInteger(query.minDuration) || query.minDuration < 1} onChange={e => setDuration(e.target.value)} /></Field>
        {!valid && <p role="status" className="text-xs">Проверьте координаты, годы, порог и целую длительность. Пустые поля недопустимы.</p>}
        <Button type="submit" className="w-full" disabled={!valid || loading}>{loading ? 'Загрузка…' : 'Рассчитать волны жары'}</Button>
        {loading && <Button type="button" variant="outline" onClick={cancel}>Отменить запрос</Button>}
      </form>
    </aside>
    <section className="point-map" aria-label="Точка волн жары">
      <h2 className="point-map-header font-semibold">Точка исследования · WGS84</h2>
      <LocationMap selection={validPoint ? query : BISHKEK} onSelect={setPoint} focusRequest={focus} describedBy={`${id}-map-hint`} className="point-map-viewport" />
      <p id={`${id}-map-hint`} className="point-map-hint">{ru.app.mapHint} Для клавиатуры — поля координат или пресеты. {!validPoint && 'Ввод некорректен: карта временно показывает Бишкек.'}</p>
    </section>
    <main id="extremes-workspace" tabIndex={-1} aria-busy={loading} className="point-results space-y-4">
      <h2 className="text-xl font-semibold">Результаты · Волны жары</h2>
      <div role="status" className="text-sm">
        {loading && <p>Загружаем суточные данные NASA POWER. {saved && 'Ниже — предыдущий результат.'}</p>}
        {cancelled && <p>Запрос отменён. {saved && 'Ниже — предыдущий результат.'}</p>}
        {saved && key !== saved.key && <p>{ru.app.draft}</p>}
        {!saved && !loading && !error && <p>Выберите точку, период и критерии, затем нажмите «Рассчитать волны жары».</p>}
      </div>
      {error && <div role="alert" className="space-y-2 rounded border border-line p-4"><p>{displayError(error)}</p>{saved && <p>Ниже — предыдущий результат.</p>}<Button type="button" variant="outline" disabled={!valid || loading} onClick={() => void run()}>Повторить с текущими параметрами</Button></div>}
      {saved && <HeatwavesResult data={saved.data} />}
    </main>
  </div>
}
