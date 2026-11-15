import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { GitCompare, Layers, MapPin, Satellite, Search, Thermometer } from 'lucide-react'
import { ControlPanel } from './components/ControlPanel'
import { DashboardBody } from './components/DashboardBody'
import { ComparisonBody } from './components/ComparisonBody'
import { DetectiveMode } from './components/DetectiveMode'
import { ClimateMode, type ClimateModeName } from './components/ClimateMode'
import { ExtremesMode } from './components/ExtremesMode'
import RegionalTrendMapMode from './components/RegionalTrendMapMode'
import { LocationMap } from './components/LocationMap'
import { Button } from './components/ui/button'
import { useAsyncData, type AsyncState } from './hooks/useAsyncData'
import { BISHKEK, REYKJAVIK, roundCoordinate, useCoordinates, type Coordinates } from './hooks/useCoordinates'
import { fetchCompare, fetchCoverage, fetchTrend } from './lib/api'
import { formatCoordinate } from './lib/format'
import { ru, variableLabel, unitLabel } from './lib/ru'
import type { CompareResponse, Coverage, TrendResponse, Variable } from './lib/types'

const { app, results } = ru
const analysisModes = [
  { value: 'single', label: app.modes.single, Icon: MapPin },
  { value: 'compare', label: app.modes.compare, Icon: GitCompare },
  { value: 'detective', label: app.modes.detective, Icon: Search },
  { value: 'anomalies', label: app.modes.anomalies, Icon: Satellite },
  { value: 'seasonal', label: app.modes.seasonal, Icon: Satellite },
  { value: 'extremes', label: 'Погодные экстремумы', Icon: Thermometer },
  { value: 'regional-map', label: 'Карта климатических трендов', Icon: Layers },
] as const

type Mode = 'single' | 'compare'
interface Query extends Coordinates { mode: Mode; pointB: Coordinates; variable: Variable; startYear: number; endYear: number }
const queryKey = (q: Query) => [q.mode, q.variable, q.startYear, q.endYear, q.latitude, q.longitude,
  ...(q.mode === 'compare' ? [q.pointB.latitude, q.pointB.longitude] : [])].join('|')
const queryLabel = (q: Query) =>
  `${variableLabel(q.variable)} · ${q.startYear}–${q.endYear} UTC · ${q.mode === 'compare' ? 'A: ' : ''}${formatCoordinate(q.latitude)}, ${formatCoordinate(q.longitude)}${q.mode === 'compare' ? ` · B: ${formatCoordinate(q.pointB.latitude)}, ${formatCoordinate(q.pointB.longitude)}` : ''}`

export default function App() {
  const pointA = useCoordinates(BISHKEK)
  const pointB = useCoordinates(REYKJAVIK)
  const [mode, setMode] = useState<Mode>('single')
  const [detective, setDetective] = useState(false)
  const [extremes, setExtremes] = useState(false)
  const [regionalMap, setRegionalMap] = useState(false)
  const [climate, setClimate] = useState<ClimateModeName | null>(null)
  const [climateMode, setClimateMode] = useState<ClimateModeName>('anomalies')
  const [activePoint, setActivePoint] = useState<'A' | 'B'>('A')
  const active = mode === 'compare' && activePoint === 'B' ? pointB : pointA
  const { coordinates, setFromMap, setNumeric, applyPreset } = active
  const [variable, setVariable] = useState<Variable>('temperature')
  const [startYear, setStartYear] = useState(1990)
  const [endYear, setEndYear] = useState(2025)
  const [focusRequest, setFocusRequest] = useState(0)
  const coverageState = useAsyncData<Coverage>('coverage', signal => fetchCoverage({ signal }))
  const [submittedByMode, setSubmittedByMode] = useState<Partial<Record<Mode, Query>>>({})
  const [resultByMode, setResultByMode] = useState<Partial<Record<Mode, Query>>>({})
  const submitted = submittedByMode[mode]
  const resultQuery = resultByMode[mode]
  const [cancelled, setCancelled] = useState(false)
  const [trendState, setTrendState] = useState<AsyncState<TrendResponse>>({ data: null, error: null, loading: false })
  const [compareState, setCompareState] = useState<AsyncState<CompareResponse>>({ data: null, error: null, loading: false })
  const currentState = mode === 'compare' ? compareState : trendState
  const loading = trendState.loading || compareState.loading
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => { request.current?.abort() }, [])
  const staged = useMemo<Query>(() => ({
    mode, pointB: pointB.coordinates, variable, startYear, endYear,
    latitude: roundCoordinate(pointA.coordinates.latitude), longitude: roundCoordinate(pointA.coordinates.longitude),
  }), [mode, variable, startYear, endYear, pointA.coordinates, pointB.coordinates])
  const dirty = resultQuery !== undefined && queryKey(staged) !== queryKey(resultQuery)

  const executeAnalysis = useCallback(async (query: Query) => {
    // The ref closes the pre-render double-click window as well as disabling the button.
    if (request.current || !coverageState.data) return
    const controller = new AbortController()
    request.current = controller
    setSubmittedByMode(previous => ({ ...previous, [query.mode]: query }))
    setCancelled(false)
    if (query.mode === 'compare') setCompareState(previous => ({ ...previous, loading: true, error: null }))
    else setTrendState(previous => ({ ...previous, loading: true, error: null }))
    try {
      if (query.mode === 'compare') {
        const data = await fetchCompare({
          point_a: { latitude: query.latitude, longitude: query.longitude },
          point_b: query.pointB, variable: query.variable, start_year: query.startYear, end_year: query.endYear,
        }, { signal: controller.signal })
        if (controller.signal.aborted) return
        setCompareState({ data, loading: false, error: null })
      } else {
        const data = await fetchTrend(query, { signal: controller.signal })
        if (controller.signal.aborted) return
        setTrendState({ data, loading: false, error: null })
      }
      setResultByMode(previous => ({ ...previous, [query.mode]: query }))
    } catch (error) {
      if (controller.signal.aborted) return
      const failure = error instanceof Error ? error : new Error(String(error))
      if (query.mode === 'compare') setCompareState(previous => ({ ...previous, loading: false, error: failure }))
      else setTrendState(previous => ({ ...previous, loading: false, error: failure }))
    } finally {
      if (request.current === controller) request.current = null
    }
  }, [coverageState.data])
  const runAnalysis = useCallback((point: Coordinates) => {
    setNumeric(point.latitude, point.longitude)
    const rounded = { latitude: roundCoordinate(point.latitude), longitude: roundCoordinate(point.longitude) }
    void executeAnalysis(mode === 'compare' && activePoint === 'B' ? { ...staged, pointB: rounded } : { ...staged, ...rounded })
  }, [staged, setNumeric, executeAnalysis, mode, activePoint])
  const cancelAnalysis = useCallback(() => {
    request.current?.abort()
    request.current = null
    setCancelled(true)
    setTrendState(previous => ({ ...previous, loading: false, error: null }))
    setCompareState(previous => ({ ...previous, loading: false, error: null }))
  }, [])
  const handleMapSelect = useCallback(({ latitude, longitude }: Coordinates) => {
    setCancelled(false)
    setFromMap(latitude, longitude)
  }, [setFromMap])
  const handleCoordinatesCommit = useCallback(({ latitude, longitude }: Coordinates) => {
    setCancelled(false)
    setNumeric(latitude, longitude)
  }, [setNumeric])
  const handlePreset = useCallback((point: Coordinates) => {
    setCancelled(false)
    applyPreset(point)
    setFocusRequest(value => value + 1)
  }, [applyPreset])
  const data = trendState.data
  const metaDescription = data
    ? `${data.variable === 'temperature' ? results.temperatureMethod : results.precipitationMethod} · ${unitLabel(data.unit)} · ${data.start_year}–${data.end_year} UTC · ${formatCoordinate(data.latitude)}, ${formatCoordinate(data.longitude)} · NASA POWER ${data.source.dataset}`
    : ''

  return (
    <div className="min-h-screen bg-base text-ink">
      <a href={regionalMap ? '#regional-map-title' : extremes ? '#extremes-workspace' : detective ? '#detective-workspace' : climate ? '#climate-workspace' : '#workspace'} className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[1000] focus:bg-card focus:p-3">{app.skip}</a>
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-2 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-3">
            <Satellite className="size-5" aria-hidden="true" />
            <div><h1 className="text-lg font-semibold">{app.brand}</h1><p className="text-xs text-muted">{app.subtitle}</p></div>
          </div>
          <span className="font-mono text-xs text-muted">NASA POWER / MERRA-2</span>
        </div>
      </header>
      <div className="mx-auto max-w-[1600px] px-4 py-3 sm:px-6">
        <fieldset className="min-w-0" disabled={loading}>
          <legend className="mb-2 text-xs font-medium text-muted">{app.analysisMode}</legend>
          <div className="grid w-full grid-cols-2 gap-1 rounded-md border border-line bg-panel p-1 sm:w-fit sm:grid-cols-3 xl:grid-cols-7">
            {analysisModes.map(({ value, label, Icon }) =>
              <button key={value} type="button"
                  className="flex min-h-12 min-w-0 items-center justify-center gap-1.5 rounded-sm border border-transparent px-2 py-2 text-center text-xs font-medium text-secondary transition-colors hover:bg-card focus-visible:outline-2 focus-visible:outline-focus disabled:opacity-60 aria-pressed:border-action aria-pressed:bg-raised aria-pressed:text-ink sm:min-h-10 sm:px-4 sm:text-sm"
                  aria-pressed={value === 'regional-map' ? regionalMap : value === 'extremes' ? extremes : value === 'detective' ? detective : value === 'anomalies' || value === 'seasonal' ? climate === value : !regionalMap && !extremes && !detective && !climate && mode === value}
                  onClick={() => {
                    setDetective(value === 'detective')
                    setExtremes(value === 'extremes')
                    setRegionalMap(value === 'regional-map')
                    if (value === 'anomalies' || value === 'seasonal') { setClimate(value); setClimateMode(value) }
                    else { setClimate(null); if (value !== 'detective' && value !== 'extremes' && value !== 'regional-map') setMode(value) }
                    setCancelled(false)
                  }}>
                <Icon className="hidden size-4 shrink-0 sm:block" aria-hidden="true" />
                <span>{label}</span>
              </button>)}
          </div>
        </fieldset>
      </div>
      <DetectiveMode active={detective} coverage={coverageState} />
      <ClimateMode active={climate !== null} mode={climateMode} coverage={coverageState} />
      <ExtremesMode active={extremes} />
      <RegionalTrendMapMode hidden={!regionalMap} />
      <div hidden={regionalMap || extremes || detective || climate !== null}>
      <div className="mx-auto grid max-w-[1600px] grid-cols-[minmax(0,1fr)] items-start lg:grid-cols-[20rem_minmax(0,1fr)]">
        <section aria-labelledby="map-heading" className="min-w-0 border-b border-line p-4 sm:p-6 lg:col-start-2 lg:row-start-1">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 id="map-heading" className="font-semibold">{app.map}</h2>
            <span className="font-mono text-xs">{formatCoordinate(coordinates.latitude)} / {formatCoordinate(coordinates.longitude)} · WGS84</span>
          </div>
          <LocationMap selection={coordinates} onSelect={handleMapSelect} focusRequest={focusRequest} describedBy="map-hint"
            selectionLabel={mode === 'compare' ? activePoint : undefined}
            otherSelection={mode === 'compare' ? { ...(activePoint === 'A' ? pointB.coordinates : pointA.coordinates), label: activePoint === 'A' ? 'B' : 'A' } : undefined}
            className="h-[300px] w-full sm:h-[390px] lg:h-[440px]" />
          <p id="map-hint" className="mt-2 text-xs text-muted">{mode === 'compare' ? `Клик меняет только точку ${activePoint}. Координаты можно ввести с клавиатуры. ` : ''}{app.mapHint}</p>
        </section>
        <aside aria-label={app.controls} className="min-w-0 border-b border-line bg-panel p-4 sm:p-5 lg:col-start-1 lg:row-start-1 lg:row-span-2 lg:border-r">
          {mode === 'compare' && <fieldset className="mb-5 space-y-2">
            <legend className="mb-2 text-sm font-semibold">Активная точка: карта, ввод и пресеты</legend>
            {(['A', 'B'] as const).map(value => {
              const point = value === 'A' ? pointA.coordinates : pointB.coordinates
              return <label key={value} className="flex min-h-10 items-center gap-2 text-xs">
                <input type="radio" name="active-point" checked={activePoint === value} onChange={() => setActivePoint(value)} />
                {value}: {formatCoordinate(point.latitude)}, {formatCoordinate(point.longitude)}°
              </label>
            })}
            <p className="text-xs text-muted">Одна переменная и общий период для обеих точек. Запрос — только по кнопке.</p>
          </fieldset>}
          <ControlPanel coverage={coverageState.data} coverageError={coverageState.error} coordinates={coordinates}
            pointLabel={mode === 'compare' ? `Точка ${activePoint} · WGS84, градусы` : undefined}
            onCoordinatesCommit={handleCoordinatesCommit} variable={variable}
            onVariableChange={next => { setCancelled(false); setVariable(next) }}
            startYear={startYear} endYear={endYear} onRangeChange={(start, end) => {
              setCancelled(false)
              setStartYear(start)
              setEndYear(end)
            }}
            onPreset={handlePreset} loading={loading} onSubmit={runAnalysis} />
          {coverageState.loading && <p role="status" className="mt-3 text-xs text-muted">{app.checking}</p>}
        </aside>
        <main id="workspace" tabIndex={-1} className="min-w-0 p-4 sm:p-6 lg:col-start-2 lg:row-start-2">
          <h2 className="mb-3 text-xl font-semibold">{app.results}</h2>
          <div className="mb-4 space-y-2 text-sm text-secondary">
            <div role="status" className="space-y-1">
              {currentState.loading ? <><p>{app.active}</p><p>{submitted && queryLabel(submitted)}</p>{currentState.data && <p>{app.previous}</p>}</>
                : cancelled ? <p>{app.cancelled}</p> : dirty ? <p>{app.draft}</p> : null}
            </div>
            {loading && <Button variant="outline" size="sm" onClick={cancelAnalysis}>{app.cancel}</Button>}
          </div>
          {mode === 'compare' ? <ComparisonBody
            state={coverageState.error ? { ...compareState, error: coverageState.error, loading: false } : compareState}
            onRetry={coverageState.error ? coverageState.retry : () => { if (submitted) void executeAnalysis(submitted) }} />
          : <DashboardBody coverage={coverageState.data}
            state={coverageState.error ? { ...trendState, error: coverageState.error, loading: false } : trendState}
            onRetry={coverageState.error ? coverageState.retry : () => { if (submitted) void executeAnalysis(submitted) }}
            metaDescription={metaDescription} />}
        </main>
      </div>
      </div>
    </div>
  )
}
