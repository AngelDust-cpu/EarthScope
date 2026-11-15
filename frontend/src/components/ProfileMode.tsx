import { useEffect, useId, useRef, useState } from 'react'
import type { AsyncState } from '../hooks/useAsyncData'
import { BISHKEK, REYKJAVIK, roundCoordinate, useCoordinates, type Coordinates } from '../hooks/useCoordinates'
import { fetchProfile, validProfileQuery, type ProfileQuery, type ProfileResponse } from '../lib/profile'
import { displayError } from '../lib/ru'
import type { Coverage } from '../lib/types'
import { LocationMap } from './LocationMap'
import { ProfileResults } from './ProfileResults'
import { Button } from './ui/button'
import { Field, TextInput } from './ui/field'
import { Alert } from './ui/feedback'

const defaults = { latitude: String(BISHKEK.latitude), longitude: String(BISHKEK.longitude), start_year: '1990', end_year: '2025', baseline_start_year: '1991', baseline_end_year: '2020', hot_threshold: '30', cold_threshold: '0', rain_threshold: '20' }
const decimal = (text: string) => {
  const value = text.trim().replace(',', '.').replace('−', '-')
  return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value) ? Number(value) : NaN
}
const steps = ['Координаты', 'Период и пороги', 'Результаты']
export function ProfileMode({ active, coverage, detective = false, onExit }: {
  active: boolean; coverage: AsyncState<Coverage> & { retry: () => void }; detective?: boolean; onExit?: () => void
}) {
  const id = useId()
  const point = useCoordinates(BISHKEK)
  const [fields, setFields] = useState(defaults)
  const [focus, setFocus] = useState(0)
  const [step, setStep] = useState(0)
  const [state, setState] = useState<AsyncState<ProfileResponse>>({ data: null, error: null, loading: false })
  const [savedKey, setSavedKey] = useState('')
  const [submitted, setSubmitted] = useState<ProfileQuery | null>(null)
  const [cancelled, setCancelled] = useState(false)
  const controller = useRef<AbortController | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const previousStep = useRef(step)
  const query = Object.fromEntries(Object.entries(fields).map(([key, text]) => [key, key.includes('year') ? (/^\d{4}$/.test(text) ? Number(text) : NaN) : decimal(text)])) as unknown as ProfileQuery
  const rawLatitude = query.latitude, rawLongitude = query.longitude
  query.latitude = roundCoordinate(query.latitude); query.longitude = roundCoordinate(query.longitude)
  const key = JSON.stringify(fields)
  const pointValid = Number.isFinite(rawLatitude) && Math.abs(rawLatitude) <= 90 && Number.isFinite(rawLongitude) && Math.abs(rawLongitude) <= 180
  const valid = pointValid && !!coverage.data && validProfileQuery(query, coverage.data)
  const current = state.data && savedKey === key && !state.loading && !state.error && !cancelled ? state.data : null
  function edit(name: keyof typeof fields, value: string) { setFields(old => ({ ...old, [name]: value })); setCancelled(false) }
  function select(p: Coordinates, preset = false) {
    point.setFromMap(p.latitude, p.longitude)
    // Same normalization as the shared coordinate owner, including wrapped map longitudes.
    const latitude = roundCoordinate(Math.max(-90, Math.min(90, p.latitude)))
    const longitude = roundCoordinate((((p.longitude + 180) % 360) + 360) % 360 - 180)
    setFields(old => ({ ...old, latitude: String(latitude), longitude: String(longitude) }))
    setCancelled(false)
    if (preset) setFocus(n => n + 1)
  }
  function commit() { if (pointValid) point.setNumeric(query.latitude, query.longitude) }
  function cancel() {
    const pending = controller.current; controller.current = null; pending?.abort()
    setState(old => ({ ...old, error: null, loading: false })); setCancelled(true)
  }
  useEffect(() => { if (!active && controller.current) cancel() }, [active])
  useEffect(() => () => { controller.current?.abort(); controller.current = null }, [])
  useEffect(() => {
    if (detective && active && previousStep.current !== step) heading.current?.focus()
    previousStep.current = step
  }, [active, detective, step])
  async function run() {
    if (controller.current || !valid) return
    commit()
    const pending = new AbortController(); controller.current = pending
    const submittedKey = key
    setSubmitted(query); setCancelled(false); setState(old => ({ ...old, error: null, loading: true }))
    if (detective) setStep(2)
    try {
      const data = await fetchProfile(query, { signal: pending.signal })
      if (pending.signal.aborted || controller.current !== pending) return
      setSavedKey(submittedKey); setState({ data, error: null, loading: false })
    } catch (error) {
      if (pending.signal.aborted || controller.current !== pending) return
      setState(old => ({ ...old, loading: false, error: error instanceof Error ? error : new Error(String(error)) }))
    } finally { if (controller.current === pending) controller.current = null }
  }
  function fieldValid(name: keyof typeof fields) {
    if (!Number.isFinite(query[name])) return false
    if (name === 'latitude') return Math.abs(rawLatitude) <= 90
    if (name === 'longitude') return Math.abs(rawLongitude) <= 180
    if (name === 'hot_threshold' || name === 'cold_threshold') return Math.abs(query[name]) <= 100
    if (name === 'rain_threshold') return query[name] >= 0 && query[name] <= 2000
    if (query[name] < (coverage.data?.min_year ?? 1981) || query[name] > (coverage.data?.max_year ?? 2025)) return false
    return name.startsWith('baseline') ? query.baseline_start_year <= query.baseline_end_year
      : query.end_year - query.start_year + 1 >= (coverage.data?.min_years ?? 5)
  }
  const input = (name: keyof typeof fields, label: string, hint?: string) => <Field htmlFor={`${id}-${name}`} label={label} hint={hint}>
    <TextInput id={`${id}-${name}`} value={fields[name]} inputMode={name.includes('year') ? 'numeric' : 'decimal'}
      aria-invalid={!fieldValid(name)} onChange={event => edit(name, event.target.value)}
      onBlur={name === 'latitude' || name === 'longitude' ? commit : undefined} />
  </Field>
  return <section hidden={!active} id={detective ? 'detective-workspace' : 'profile-workspace'} tabIndex={-1} className="research-profile space-y-5">
    {detective && <div className="space-y-2">
      <h2 ref={heading} tabIndex={-1} className="text-xl font-semibold">{detective ? `Детектив · ${steps[step]}` : 'Климатический профиль'}</h2>
      <p className="max-w-3xl text-sm text-muted">Как менялся климат моего региона? Один запуск: температура, осадки, аномалии относительно выбранной базы, сезоны и пороговые события. Это не прогноз.</p>
      <ol aria-label="Шаги исследования" className="research-step-list text-sm"><li>1. Вопрос</li>{steps.map((label, i) => <li key={label} aria-current={i === step ? 'step' : undefined}>{i + 2}. {label}</li>)}</ol>
      <Button type="button" variant="outline" onClick={() => { cancel(); onExit?.() }}>Новое исследование</Button>
    </div>}
    <div className={detective ? 'grid min-w-0 items-start gap-5' : 'point-workbench'}>
      <form hidden={detective && step === 2} noValidate onSubmit={event => { event.preventDefault(); if (detective && step === 0) { if (pointValid) { commit(); setStep(1) } } else void run() }} className="research-profile-form min-w-0 space-y-4">
        <fieldset hidden={detective && step !== 0} className="min-w-0 space-y-3">
          <legend className="mb-2 font-medium">Точка · WGS84, градусы</legend>
          <div className="grid grid-cols-2 gap-2">{input('latitude', 'Широта')}{input('longitude', 'Долгота')}</div>
          {!pointValid && <p role="alert" className="text-xs">Широта: от −90 до 90°, долгота: от −180 до 180°.</p>}
          <div className="flex flex-wrap gap-2">{[{ point: BISHKEK, label: 'Бишкек, Кыргызстан' }, { point: REYKJAVIK, label: 'Рейкьявик' }].map(p => <Button key={p.label} type="button" variant="outline" size="sm" onClick={() => select(p.point, true)}>{p.label}</Button>)}</div>
        </fieldset>
        <div hidden={detective && step !== 1} className="space-y-5">
          <fieldset className="space-y-3"><legend className="mb-2 font-medium">Период анализа · UTC</legend><div className="grid grid-cols-2 gap-2">{input('start_year', 'Первый год')}{input('end_year', 'Последний год')}</div></fieldset>
          <fieldset className="space-y-3"><legend className="mb-2 font-medium">Базовый период · температура</legend><div className="grid grid-cols-2 gap-2">{input('baseline_start_year', 'Начало базы')}{input('baseline_end_year', 'Конец базы')}</div>
            <Button type="button" variant="outline" size="sm" onClick={() => { setFields(old => ({ ...old, baseline_start_year: '1991', baseline_end_year: '2020' })); setCancelled(false) }}>База 1991–2020</Button>
            <p className="text-xs text-muted">Аномалия — отклонение от среднего базы, не оценка редкости. Неполная база может не позволить рассчитать аномалии.</p>
          </fieldset>
          <details className="profile-disclosure"><summary className="text-sm font-medium">Пороги экстремумов</summary>
          <fieldset className="space-y-3"><legend className="sr-only">Фиксированные абсолютные пороги</legend>
            {input('hot_threshold', 'Жаркие дни: Tmax > порога, °C', 'От −100 до +100 °C. Не волны жары.')}
            {input('cold_threshold', 'Холодные ночи: Tmin < порога, °C', 'От −100 до +100 °C; суточный минимум, не почасовые ночные данные.')}
            {input('rain_threshold', 'Сильные осадки: > порога, мм/сут', 'От 0 до 2000 мм/сут.')}
          </fieldset></details>
          {coverage.data && <p className="text-xs text-muted">Доступно {coverage.data.min_year}–{coverage.data.max_year}; анализ — не менее {coverage.data.min_years} лет. База в этих же границах; начало не позже конца.</p>}
          {!valid && coverage.data && <p role="alert" className="text-xs">Проверьте координаты, годы, базу и допустимые пороги. Анализ не запущен.</p>}
        </div>
        {(!detective || step < 2) && <Button type="submit" className="w-full" disabled={state.loading || (detective && step === 0 ? !pointValid : !valid)}>{detective && step === 0 ? 'Далее' : state.loading ? 'Анализ выполняется…' : 'Составить климатический профиль'}</Button>}
        {detective && step === 1 && current && <Button type="button" variant="outline" onClick={() => setStep(2)}>Открыть сохранённый ответ</Button>}
        {coverage.loading && <p role="status" className="text-sm">Проверяем доступный период…</p>}
        {coverage.error && <Alert tone="error" title="Не удалось проверить период" action={<Button type="button" variant="outline" onClick={coverage.retry}>Повторить проверку периода</Button>}>{displayError(coverage.error)}</Alert>}
        <p className="text-xs text-muted">Клик на карте и пресеты меняют только точку. Запрос отправляется только по кнопке.</p>
      </form>
        <div hidden={detective && step !== 0} className="point-map min-w-0">
          <div className="point-map-header"><h2>Точка исследования</h2><span className="font-mono text-xs">WGS84</span></div>
          {active && <LocationMap selection={point.coordinates} onSelect={p => select(p)} focusRequest={focus} describedBy={`${id}-map-hint`} className="point-map-viewport" />}
          <p id={`${id}-map-hint`} className="point-map-hint">Выберите точку на карте, введите координаты или используйте пресет.</p>
        </div>
        <div hidden={detective && step !== 2} aria-label="Результаты климатического профиля" className="point-results space-y-4">
          <div role="status" className="text-sm">
            {state.loading ? <p>Собираем профиль NASA POWER… {submitted && `${submitted.start_year}–${submitted.end_year} UTC · ${submitted.latitude}, ${submitted.longitude}°`} Можно отменить.</p>
              : cancelled ? <p>Анализ отменён. Текущего ответа нет.</p>
                : !current && !state.error ? <p>{state.data ? 'Параметры изменены. Сохранённый ответ скрыт; запустите анализ заново.' : 'Выберите точку и параметры, затем запустите анализ.'}</p> : null}
          </div>
          {state.loading && <Button type="button" variant="outline" onClick={cancel}>Отменить</Button>}
          {state.error && <Alert tone="error" title="Профиль не получен" action={<Button type="button" variant="outline" disabled={!valid} onClick={() => void run()}>Повторить запрос</Button>}>{displayError(state.error)}</Alert>}
          {current && active && (!detective || step === 2) && <ProfileResults key={savedKey} data={current} coverage={coverage.data} />}
          {detective && !state.loading && !state.error && <Button type="button" disabled={!valid} onClick={() => void run()}>Повторить анализ</Button>}
        </div>
    </div>
    {detective && <Button type="button" variant="outline" onClick={() => { if (controller.current) cancel(); if (step === 0) onExit?.(); else setStep(step - 1) }}>Назад</Button>}
  </section>
}
