import { useCallback, useEffect, useState } from 'react'
import { REGIONAL_COLORS, signed, trendUnit, type RegionalLayer, type RegionalVariable } from '../lib/regionalMap'
import { RegionalTrendGrid } from './RegionalTrendGrid'
import { PdfDownloadButton } from './PdfDownloadButton'
import { AnalysisControlsPortal } from './AnalysisControlsPortal'
import { apiUrl } from '../lib/apiBase'
import { fetchWorld, fetchWorldCell, WORLD_LATITUDE, WORLD_SOURCE, WORLD_YEARS, type WorldLayer, type WorldCellDetails, worldCellAt } from '../lib/globalMap'
import type { RasterMode } from '../lib/regionalRaster'
import './RegionalTrendMapMode.css'

export default function RegionalTrendMapMode({ hidden = false }: { hidden?: boolean }) {
  const [world, setWorld] = useState<WorldLayer | null>(null)
  const [worldSelection, setWorldSelection] = useState<ReturnType<typeof worldCellAt> | null>(null)
  const [cellResult, setCellResult] = useState<{ url: string; details?: WorldCellDetails; error?: string } | null>(null)
  const [latitude, setLatitude] = useState('42.875')
  const [longitude, setLongitude] = useState('74.6')
  const [variable, setVariable] = useState<RegionalVariable>('temperature')
  const [layer, setLayer] = useState<RegionalLayer>('trend')
  const [surface, setSurface] = useState<'smooth' | 'native'>('smooth')
  const mode: RasterMode = layer === 'trend' ? surface
    : surface === 'smooth' ? 'significance' : 'significance-native'
  const globalLayer = world
  const [loading, setLoading] = useState(false), [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  const cellRequest = globalLayer && worldSelection
    ? apiUrl(`/api/maps/world/cell?variable=${globalLayer.variable}&row=${worldSelection.row}&col=${worldSelection.col}`)
    : null
  const currentCell = cellResult?.url === cellRequest ? cellResult : null
  const worldDetails = currentCell?.details
  const selectedLatitude = Number(latitude.replace(',', '.'))
  const selectedLongitude = Number(longitude.replace(',', '.'))
  const coordinatesValid = Boolean(latitude.trim() && longitude.trim()) &&
    Math.abs(selectedLatitude) <= WORLD_LATITUDE && Math.abs(selectedLongitude) <= 180
  useEffect(() => {
    setError('')
    if (hidden || world?.variable === variable) { setLoading(false); return }
    const request = new AbortController()
    setLoading(true)
    fetchWorld(variable, request.signal)
      .then(result => { if (!request.signal.aborted) setWorld(result) })
      .catch(reason => { if (!request.signal.aborted) setError(reason.message) })
      .finally(() => { if (!request.signal.aborted) setLoading(false) })
    return () => request.abort()
  }, [hidden, variable, world, retry])
  useEffect(() => {
    if (hidden || !globalLayer || !worldSelection || !cellRequest) return
    const request = new AbortController()
    fetchWorldCell(globalLayer.variable, worldSelection.row, worldSelection.col, request.signal)
      .then(result => { if (!request.signal.aborted) {
        setCellResult({ url: cellRequest, details: result })
      } })
      .catch(error => { if (!request.signal.aborted) setCellResult({ url: cellRequest, error: error.message }) })
    return () => request.abort()
  }, [cellRequest, globalLayer, hidden, worldSelection])
  const limit = globalLayer?.limit ?? 1
  const ignoreRegionalSelection = useCallback(() => {}, [])
  return <section hidden={hidden} lang="ru" className="rtm" aria-labelledby="regional-map-title">
    <h2 id="regional-map-title" tabIndex={-1}>Климатические тренды мира</h2>
    {!hidden && <AnalysisControlsPortal>
      <div className="rtm rtm-parameters point-controls">
        <div className="rtm-controls">
      <label>Показатель<select aria-label="Показатель" value={variable} onChange={e => setVariable(e.target.value as RegionalVariable)}>
        <option value="temperature">Температура (°C)</option><option value="precipitation">Осадки (мм/год)</option>
      </select></label>
      <div>Период <strong>{WORLD_YEARS}</strong></div>
      <div className="rtm-layer-control">
        <span id="regional-layer-label">Слой карты</span>
        <div className="rtm-layers" role="group" aria-labelledby="regional-layer-label">
          <button type="button" aria-pressed={layer === 'trend'} onClick={() => setLayer('trend')}>Тренд</button>
          <button type="button" aria-pressed={layer === 'significance'} onClick={() => setLayer('significance')}>Значимость BY</button>
        </div>
      </div>
      <div className="rtm-layer-control">
        <span id="regional-surface-label">Поверхность карты</span>
        <div className="rtm-layers" role="group" aria-labelledby="regional-surface-label">
          <button type="button" aria-pressed={surface === 'smooth'} onClick={() => setSurface('smooth')}>Плавная карта</button>
          <button type="button" aria-pressed={surface === 'native'} onClick={() => setSurface('native')}>Исходная сетка</button>
        </div>
      </div>
        </div>
        {globalLayer && <form className="rtm-actions" aria-label="Выбор ячейки по координатам" onSubmit={event => {
          event.preventDefault()
          if (coordinatesValid) setWorldSelection(worldCellAt(selectedLatitude, selectedLongitude))
        }}>
          <label>Широта<input value={latitude} onChange={event => setLatitude(event.target.value)} inputMode="decimal" /></label>
          <label>Долгота<input value={longitude} onChange={event => setLongitude(event.target.value)} inputMode="decimal" /></label>
          <button type="submit" disabled={!coordinatesValid}>Открыть ячейку</button>
          {!coordinatesValid && <small>Широта от −85,05° до 85,05°, долгота от −180° до 180°.</small>}
        </form>}
      </div>
    </AnalysisControlsPortal>}
    <div className="rtm-status" role="status">
      {loading && 'Загружаем слой NASA POWER… '}
      {globalLayer && `На карте: ${globalLayer.variable === 'temperature' ? 'температура' : 'осадки'} · ${WORLD_YEARS} · сетка 0,5° × 0,625°`}
    </div>
    {error && <p role="alert">{error} <button type="button" onClick={() => setRetry(value => value + 1)}>Повторить загрузку</button></p>}
    <div className="rtm-workspace">
      <div className="rtm-map-column">
        {globalLayer && <div className="rtm-legend" role="group" aria-label="Легенда карты">
          {layer === 'trend' ? <>
            <div className="rtm-ramp">
              <strong>Тренд · {trendUnit(globalLayer.variable === 'temperature' ? '°C' : 'mm')}</strong>
              <div className="rtm-scale" aria-hidden="true" style={{ background: `linear-gradient(90deg, ${REGIONAL_COLORS.negative}, ${REGIONAL_COLORS.zero}, ${REGIONAL_COLORS.positive})` }} />
              <div className="rtm-ticks"><span>{globalLayer ? '≤ ' : ''}{signed(-limit)}</span><span>0</span><span>{globalLayer ? '≥ ' : ''}{signed(limit)}</span></div>
            </div>
            <span className="rtm-swatch"><i className="rtm-mask-swatch" />Недостаточно данных</span>
          </> : <>
            <strong>Значимость после поправки BY</strong>
            {surface === 'smooth' ? <div className="rtm-ramp">
              <div className="rtm-scale" aria-hidden="true" style={{ background: `linear-gradient(90deg, ${REGIONAL_COLORS.notSignificant}, ${REGIONAL_COLORS.significant})` }} />
              <div className="rtm-ticks"><span>0%</span><span>50%</span><span>100%</span></div>
            </div> : <>
              <span className="rtm-swatch"><i style={{ background: REGIONAL_COLORS.significant }} />q ≤ 0,10</span>
              <span className="rtm-swatch"><i style={{ background: REGIONAL_COLORS.notSignificant }} />q &gt; 0,10</span>
            </>}
            <span className="rtm-swatch"><i className="rtm-mask-swatch" />Недостаточно данных</span>
          </>}
          {layer === 'significance' && surface === 'smooth' && <small className="rtm-raster-note">Цвет показывает взвешенную долю соседних ячеек с q ≤ 0,10. Нажмите на карту, чтобы увидеть q исходной ячейки.</small>}
          {mode === 'smooth' && <small className="rtm-raster-note">Цветовая поверхность сглажена для визуализации. Научные значения рассчитываются по исходным ячейкам NASA.</small>}
        </div>}
        <div className="rtm-map" role="region" aria-label="Региональная карта">
          <RegionalTrendGrid data={null} mode={mode} limit={limit} boundary={null} selected={null} onSelect={ignoreRegionalSelection}
            globalLayer={globalLayer} onSelectWorld={setWorldSelection} worldSelected={worldSelection} />
          {(globalLayer && worldSelection) && <a className="rtm-mobile-details" href="#regional-cell-panel">К данным ячейки</a>}
        </div>
        {globalLayer && <details className="rtm-method"><summary>Метод и ограничения</summary>
          <p>Годовой архив NASA POWER MERRA‑2, LST. Годовая сумма осадков рассчитана из годовой интенсивности (мм/сут) с учётом дней в году. Поправка BY применяется по всему миру ({globalLayer.tested.toLocaleString('ru-RU')} тестов).</p>
          <p>Цветовая шкала тренда ограничена по 98-му процентилю модуля наклона. Глобальный вид Web Mercator отображает широты примерно до 85,05°.</p>
        </details>}
      </div>
      <aside className="rtm-inspector" id="regional-cell-panel" tabIndex={-1} aria-label="Исследование ячейки">
        <h3>Исследование ячейки</h3>
        {globalLayer ? <>
          <p><a href={WORLD_SOURCE}>NASA POWER · {globalLayer.version}</a></p>
          {worldSelection && <p>{worldSelection.latitude.toFixed(3)}°, {worldSelection.longitude.toFixed(3)}°</p>}
          {worldDetails ? <>
            {cellRequest && <PdfDownloadButton request={{ url: cellRequest }} label="Сформировать PDF-отчёт · ячейка мира" />}
            <p className="rtm-primary-value">{worldDetails.slope == null ? 'Тренд недоступен' : `${signed(worldDetails.slope)} ${trendUnit(worldDetails.unit === 'mm' ? 'mm' : '°C')}`}</p>
            <p>95% ДИ HAC(3) · {worldDetails.ci_low == null || worldDetails.ci_high == null ? 'недоступен' : `${signed(worldDetails.ci_low)} … ${signed(worldDetails.ci_high)}`}</p>
            <p>p / q BY · {worldDetails.p?.toPrecision(4) ?? '—'} / {worldDetails.q?.toPrecision(4) ?? '—'}</p>
            <p>Валидных лет · {worldDetails.series.filter(entry => entry.value != null).length} из {worldDetails.series.length}</p>
            <details><summary>Годовые значения</summary><table><tbody>{worldDetails.series.map(entry => <tr key={entry.year}><th>{entry.year}</th><td>{entry.value?.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) ?? '—'} {entry.value != null ? worldDetails.unit.replace('mm', 'мм') : ''}</td></tr>)}</tbody></table></details>
          </> : <p role="status">{currentCell?.error || (worldSelection ? 'Загружаем годовой ряд…' : 'Выберите ячейку на карте или по координатам.')}</p>}
        </> : <p>После загрузки нажмите на карту, чтобы открыть ячейку NASA.</p>}
      </aside>
    </div>
  </section>
}

export { RegionalTrendMapMode }
