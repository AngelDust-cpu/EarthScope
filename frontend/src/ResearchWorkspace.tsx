import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { AnalysisControlsTarget } from './components/AnalysisControlsPortal'
import { ControlPanel } from './components/ControlPanel'
import { DashboardBody } from './components/DashboardBody'
import { ComparisonBody } from './components/ComparisonBody'
import { DetectiveMode } from './components/DetectiveMode'
import { ClimateMode } from './components/ClimateMode'
import { ExtremesMode } from './components/ExtremesMode'
import { ProfileMode } from './components/ProfileMode'
import RegionalTrendMapMode from './components/RegionalTrendMapMode'
import { LocationMap } from './components/LocationMap'
import { SiteHeader, SiteLink } from './components/SiteNavigation'
import { Button } from './components/ui/button'
import { useAsyncData, type AsyncState } from './hooks/useAsyncData'
import { BISHKEK, REYKJAVIK, roundCoordinate, useCoordinates, type Coordinates } from './hooks/useCoordinates'
import { fetchCompare, fetchCoverage, fetchTrend } from './lib/api'
import { formatCoordinate } from './lib/format'
import { researchModes, type ResearchMode } from './lib/researchRoutes'
import { ru, variableLabel, unitLabel } from './lib/ru'
import type { CompareResponse, Coverage, TrendResponse, Variable } from './lib/types'

const { app, results } = ru

type Mode = 'single' | 'compare'
interface Query extends Coordinates { mode: Mode; pointB: Coordinates; variable: Variable; startYear: number; endYear: number }
const queryKey = (q: Query) => [q.mode, q.variable, q.startYear, q.endYear, q.latitude, q.longitude,
  ...(q.mode === 'compare' ? [q.pointB.latitude, q.pointB.longitude] : [])].join('|')
const queryLabel = (q: Query) =>
  `${variableLabel(q.variable)} · ${q.startYear}–${q.endYear} UTC · ${q.mode === 'compare' ? 'A: ' : ''}${formatCoordinate(q.latitude)}, ${formatCoordinate(q.longitude)}${q.mode === 'compare' ? ` · B: ${formatCoordinate(q.pointB.latitude)}, ${formatCoordinate(q.pointB.longitude)}` : ''}`

export default function ResearchWorkspace({ view }: { view: ResearchMode | null }) {
  const pointA = useCoordinates(BISHKEK)
  const pointB = useCoordinates(REYKJAVIK)
  const [controlsTarget, setControlsTarget] = useState<HTMLDivElement | null>(null)
  const modePickerRef = useRef<HTMLElement>(null)
  const [lastPointMode, setLastPointMode] = useState<Mode>('single')
  const pointStudy = view === 'single' || view === 'compare'
  const mode = pointStudy ? view : lastPointMode
  const detective = view === 'detective'
  const extremes = view === 'extremes'
  const regionalMap = view === 'regional-map'
  const profile = view === 'profile'
  const climate = view === 'anomalies' || view === 'seasonal' ? view : null
  const currentView = researchModes.find(entry => entry.value === view)
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
  useEffect(() => {
    modePickerRef.current?.focus({ preventScroll: true })
    if (view === 'single' || view === 'compare') setLastPointMode(view)
    setCancelled(false)
    if (request.current) {
      request.current.abort()
      request.current = null
      setTrendState(previous => ({ ...previous, loading: false }))
      setCompareState(previous => ({ ...previous, loading: false }))
    }
  }, [view])
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
    <div hidden={!view} className="min-h-screen bg-base text-ink">
      <a href={profile ? '#profile-workspace' : regionalMap ? '#regional-map-title' : extremes ? '#extremes-workspace' : detective ? '#detective-workspace' : climate ? '#climate-workspace' : '#workspace'} className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[1000] focus:bg-card focus:p-3">{app.skip}</a>
      <div className="research-shell framer-workstation" data-mode={view}>
        <SiteHeader showCatalog />
        <aside className="research-rail">
          <nav aria-label={app.analysisMode} className="research-navigation">
            {currentView && <details key={view} className="research-mode-picker">
              <summary ref={modePickerRef}>
                <currentView.Icon size={16} aria-hidden="true" />
                <span>{currentView.label}</span>
                <ChevronDown size={16} aria-hidden="true" />
              </summary>
              <div className="research-mode-options">
                {researchModes.map(({ value, label, Icon }) => (
                  <SiteLink key={value} to={value} className="research-nav-item" aria-current={view === value ? 'page' : undefined}>
                    <Icon size={16} aria-hidden="true" /><span>{label}</span>
                  </SiteLink>
                ))}
              </div>
            </details>}
          </nav>
          <section hidden={!pointStudy} aria-label={app.controls} className="point-controls station-query">
            {mode === 'compare' && <fieldset className="station-active-point">
              <legend className="mb-2 text-sm font-semibold">Активная точка: карта, ввод и пресеты</legend>
              {(['A', 'B'] as const).map(value => {
                const point = value === 'A' ? pointA.coordinates : pointB.coordinates
                return <label key={value} className="station-point-option">
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
          </section>
          <div ref={setControlsTarget} className="analysis-controls-slot min-w-0" />
          <p className="station-rail-note">NASA POWER / MERRA-2</p>
        </aside>
        <div className="research-content">
          <div className="research-heading" data-view={view}>
            <h1>{currentView?.title}</h1>
            <p>{currentView?.hint}</p>
          </div>
          <div className="research-view">
            <AnalysisControlsTarget.Provider value={controlsTarget}>
              <DetectiveMode active={detective} coverage={coverageState} />
              <ClimateMode active={climate !== null} mode={climate ?? 'anomalies'} coverage={coverageState} />
              <ExtremesMode active={extremes} />
              <ProfileMode active={profile} coverage={coverageState} />
              <RegionalTrendMapMode hidden={!regionalMap} />
            </AnalysisControlsTarget.Provider>
            <div hidden={!pointStudy}>
              <div className="point-workbench point-primary">
                <section aria-labelledby="map-heading" className="point-map">
                  <div className="point-map-header">
                    <h2 id="map-heading">Область исследования</h2>
                    <span className="font-mono text-xs">{formatCoordinate(coordinates.latitude)} / {formatCoordinate(coordinates.longitude)} · WGS84</span>
                  </div>
                  <LocationMap selection={coordinates} onSelect={handleMapSelect} focusRequest={focusRequest} describedBy="map-hint"
                    selectionLabel={mode === 'compare' ? activePoint : undefined}
                    otherSelection={mode === 'compare' ? { ...(activePoint === 'A' ? pointB.coordinates : pointA.coordinates), label: activePoint === 'A' ? 'B' : 'A' } : undefined}
                    className="point-map-viewport" />
                  <p id="map-hint" className="point-map-hint">{mode === 'compare' ? `Клик меняет только точку ${activePoint}. Координаты можно ввести с клавиатуры. ` : ''}{app.mapHint}</p>
                </section>
                <main id="workspace" tabIndex={-1} className="point-results">
                  <h2 className="mb-3 text-xl font-semibold tracking-tight">{app.results}</h2>
                  <div className="analysis-status mb-4 space-y-2 text-sm text-secondary">
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
                    metaDescription={metaDescription} showInterpretation />}
                </main>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
