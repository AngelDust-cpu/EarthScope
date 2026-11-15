import { useEffect, useId, useState, type FormEvent } from 'react'
import { BISHKEK, REYKJAVIK, type Coordinates } from '../hooks/useCoordinates'
import { formatCoordinate } from '../lib/format'
import { ru } from '../lib/ru'
import { VARIABLES, type Coverage, type Variable } from '../lib/types'
import { Button } from './ui/button'
import { Field, Select, TextInput } from './ui/field'
import { Alert, Spinner } from './ui/feedback'

const c = ru.controls

interface ControlPanelProps {
  stage?: 'coordinates' | 'period'
  onContinue?: () => void
  submitDisabled?: boolean
  coverage: Coverage | null
  coverageError: Error | null
  coordinates: Coordinates
  onCoordinatesCommit: (point: Coordinates) => void
  variable: Variable
  onVariableChange: (variable: Variable) => void
  startYear: number
  endYear: number
  onRangeChange: (start: number, end: number) => void
  onPreset: (point: Coordinates) => void
  pointLabel?: string
  loading: boolean
  onSubmit: (point: Coordinates) => void
}
function parseDecimal(text: string): number {
  const normalized = text.trim().replace('−', '-').replace(',', '.')
  return /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized) ? Number(normalized) : NaN
}

function parseYear(text: string): number {
  return /^\d{4}$/.test(text.trim()) ? Number(text) : NaN
}

export function ControlPanel(p: ControlPanelProps) {
  const id = useId()
  const [lat, setLat] = useState(formatCoordinate(p.coordinates.latitude))
  const [lon, setLon] = useState(formatCoordinate(p.coordinates.longitude))
  const [start, setStart] = useState(String(p.startYear))
  const [end, setEnd] = useState(String(p.endYear))
  useEffect(() => {
    setLat(formatCoordinate(p.coordinates.latitude))
    setLon(formatCoordinate(p.coordinates.longitude))
  }, [p.coordinates.latitude, p.coordinates.longitude, p.pointLabel])
  const latitude = parseDecimal(lat), longitude = parseDecimal(lon)
  const validPoint = Number.isFinite(latitude) && Number.isFinite(longitude) && Math.abs(latitude) <= 90 && Math.abs(longitude) <= 180
  const first = parseYear(start), last = parseYear(end)
  const min = p.coverage?.min_year ?? 1981, max = p.coverage?.max_year ?? 2025, count = p.coverage?.min_years ?? 5
  const validRange = Number.isInteger(first) && Number.isInteger(last) && first >= min && last <= max && last - first + 1 >= count
  function submit(event: FormEvent) {
    event.preventDefault()
    if (p.stage === 'coordinates') {
      if (validPoint) { p.onCoordinatesCommit({ latitude, longitude }); p.onContinue?.() }
      return
    }
    if (!validPoint || !validRange || !p.coverage || p.loading || p.submitDisabled) return
    p.onSubmit({ latitude, longitude })
  }
  function commitPoint() {
    if (validPoint) p.onCoordinatesCommit({ latitude, longitude })
  }
  function range(nextStart: string, nextEnd: string) {
    setStart(nextStart); setEnd(nextEnd)
    const a = parseYear(nextStart), b = parseYear(nextEnd)
    if (Number.isInteger(a) && Number.isInteger(b) && a >= min && b <= max && b - a + 1 >= count) p.onRangeChange(a, b)
  }
  return (
    <form onSubmit={submit} noValidate className="flex min-w-0 flex-col gap-5">
      <h2 className="text-sm font-semibold">{c.title}</h2>
      <fieldset hidden={p.stage === 'period'} className="min-w-0 space-y-3">
        <legend className="mb-2 text-sm font-medium">{p.pointLabel ?? c.point}</legend>
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2">
          <Field htmlFor={`${id}-lat`} label={c.latitude}>
            <TextInput id={`${id}-lat`} value={lat} inputMode="decimal" autoComplete="off" aria-invalid={!validPoint}
              aria-describedby={`${id}-point-hint`} onChange={e => setLat(e.target.value)} onBlur={commitPoint} />
          </Field>
          <Field htmlFor={`${id}-lon`} label={c.longitude}>
            <TextInput id={`${id}-lon`} value={lon} inputMode="decimal" autoComplete="off" aria-invalid={!validPoint}
              aria-describedby={`${id}-point-hint`} onChange={e => setLon(e.target.value)} onBlur={commitPoint} />
          </Field>
        </div>
        <p id={`${id}-point-hint`} className="text-xs text-muted">{validPoint ? c.pointHint : c.coordinateError}</p>
        <div className="flex flex-wrap gap-2">
          {[{ point: BISHKEK, label: c.preset }, { point: REYKJAVIK, label: 'Рейкьявик' }].map(({ point, label }) =>
            <Button key={label} type="button" variant="outline" size="sm" onClick={() => {
              setLat(formatCoordinate(point.latitude))
              setLon(formatCoordinate(point.longitude))
              p.onPreset(point)
            }}>{label}</Button>)}
        </div>
      </fieldset>
      <div hidden={!!p.stage}><Field htmlFor={`${id}-variable`} label={c.variable} hint={p.variable === 'temperature' ? c.temperatureHint : c.precipitationHint}>
        <Select id={`${id}-variable`} value={p.variable} onChange={e => p.onVariableChange(e.target.value as Variable)}>
          {VARIABLES.map(value => <option key={value} value={value}>{c[value]}</option>)}
        </Select>
      </Field></div>
      <fieldset hidden={p.stage === 'coordinates'} className="min-w-0 space-y-3">
        <legend className="mb-2 text-sm font-medium">{c.period}</legend>
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-2">
          <Field htmlFor={`${id}-start`} label={c.start}>
            <TextInput id={`${id}-start`} inputMode="numeric" value={start} aria-invalid={!validRange} aria-describedby={`${id}-range-hint`} onChange={e => range(e.target.value, end)} />
          </Field>
          <Field htmlFor={`${id}-end`} label={c.end}>
            <TextInput id={`${id}-end`} inputMode="numeric" value={end} aria-invalid={!validRange} aria-describedby={`${id}-range-hint`} onChange={e => range(start, e.target.value)} />
          </Field>
        </div>
        <p id={`${id}-range-hint`} className="text-xs text-muted">{validRange ? c.periodHint(count) : c.rangeError(min, max, count)}</p>
      </fieldset>
      {p.coverageError && <Alert tone="error" title={c.coverageError}>{c.coverageHint}</Alert>}
      <Button type="submit" disabled={p.stage === 'coordinates' ? !validPoint : p.loading || !p.coverage || !validPoint || !validRange || p.submitDisabled} className="w-full">
        {p.loading && <Spinner className="size-4 animate-spin" />}{p.stage === 'coordinates' ? 'Далее' : p.loading ? c.loading : c.submit}
      </Button>
      <p className="text-xs text-muted">{c.draft}</p>
    </form>
  )
}
