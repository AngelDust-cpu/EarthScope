import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { GeoJSON, MapContainer, Pane, Polygon, ZoomControl, useMap } from 'react-leaflet'
import { boundaryRasterPath, createRegionalGrid, rasterPoint, renderRegionalRaster, type CountryBoundary, type RasterMode, type RegionalGrid } from '../lib/regionalRaster'
import L from 'leaflet'
import { LocateFixed } from 'lucide-react'
import type { FeatureCollection } from 'geojson'
import 'leaflet/dist/leaflet.css'
import { signed, trendUnit, type RegionalCell, type RegionalMapResponse } from '../lib/regionalMap'
import { renderWorld, WORLD_LATITUDE, worldCellAt, type WorldLayer } from '../lib/globalMap'

const WORLD_MAP = `${import.meta.env.BASE_URL}world.geojson`
const WORLD_STYLE = { color: '#636d78', weight: 0.8, fillColor: '#303740', fillOpacity: 1 }
const BORDER_STYLE = { color: '#222a32', weight: 1, opacity: 0.46, fill: false }
const REGION_BOUNDS = L.latLngBounds([39, 69], [44, 81])
const WORLD_BOUNDS = L.latLngBounds([-WORLD_LATITUDE, -180], [WORLD_LATITUDE, 180])

function RegionalMapControls({ bounds, region }: { bounds: L.LatLngBounds | null; region?: string }) {
  const map = useMap()
  const fittedRegion = useRef<string | undefined>(undefined)
  const [visible, setVisible] = useState(false)
  const focusButton = useRef<HTMLButtonElement>(null)
  const focusRegion = useCallback(() => {
    map.stop()
    if (region === 'world') {
      map.setView(WORLD_BOUNDS.getCenter(), map.getMinZoom(), { animate: false })
    } else {
      map.fitBounds(bounds ?? REGION_BOUNDS, { padding: [22, 24], animate: false })
    }
  }, [map, bounds, region])
  useEffect(() => {
    if (focusButton.current) L.DomEvent.disableClickPropagation(focusButton.current)
    const observer = new ResizeObserver(() => {
      const container = map.getContainer()
      const hasSize = container.clientWidth > 0 && container.clientHeight > 0
      setVisible(hasSize)
      if (!hasSize) return
      // Leaflet must preserve the geographic center when this initially
      // hidden, persistently mounted map becomes visible or changes size.
      map.invalidateSize({ animate: false, debounceMoveend: true })
      if (region === 'world') {
        map.setMinZoom(map.getBoundsZoom(WORLD_BOUNDS, true))
        map.panInsideBounds(WORLD_BOUNDS, { animate: false })
      }
    })
    observer.observe(map.getContainer())
    return () => observer.disconnect()
  }, [map, region])
  useEffect(() => {
    if (!visible || !bounds || !region || fittedRegion.current === region || !map.getContainer().clientWidth) return
    fittedRegion.current = region
    focusRegion()
  }, [visible, bounds, region, map, focusRegion])
  return <button ref={focusButton} type="button" className="rtm-map-focus" onClick={focusRegion}>
    <LocateFixed size={16} aria-hidden="true" /> {region === 'world' ? 'Весь мир' : 'К региону'}
  </button>
}

function RegionalRasterOverlay({ grid, mode, limit, unit, boundary, onSelect }: {
  grid: RegionalGrid; mode: RasterMode; limit: number; unit: RegionalMapResponse['metadata']['unit']
  boundary: CountryBoundary | null
  onSelect: (cell: RegionalCell) => void
}) {
  const map = useMap()
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const mask = useRef<Path2D | null>(null)
  const rasters = useMemo(() => ({ grid, limit, frames: new Map<RasterMode, ImageData>() }), [grid, limit])

  useEffect(() => {
    const surface = L.DomUtil.create('canvas', 'leaflet-layer rtm-raster', map.getPane('regional-cells')!)
    surface.setAttribute('aria-hidden', 'true')
    canvas.current = surface
    const position = () => {
      const nw = map.latLngToLayerPoint([grid.north, grid.west])
      const se = map.latLngToLayerPoint([grid.south, grid.east])
      L.DomUtil.setPosition(surface, nw)
      surface.style.width = `${se.x - nw.x}px`
      surface.style.height = `${se.y - nw.y}px`
    }
    position()
    map.on('zoom viewreset', position)

    const tooltip = L.tooltip({ direction: 'top', offset: [0, -10], className: 'rtm-cell-tooltip' })
    let hovered: RegionalCell | undefined
    const close = () => { tooltip.remove(); hovered = undefined }
    const cellAt = ({ lng, lat }: L.LatLng) => {
      if (mask.current) {
        const [x, y] = rasterPoint(grid, lng, lat, surface.width, surface.height)
        if (!surface.getContext('2d')?.isPointInPath(mask.current, x, y, 'evenodd')) return
      }
      return grid.cellAt(lng, lat)
    }
    const move = ({ latlng }: L.LeafletMouseEvent) => {
      const cell = cellAt(latlng)
      if (!cell) { close(); return }
      if (cell !== hovered) {
        const { latitude, longitude, trend } = cell.properties
        const text = document.createElement('div')
        const coordinates = document.createElement('strong')
        coordinates.textContent = `${latitude.toFixed(3)}° с. ш., ${longitude.toFixed(3)}° в. д.`
        const value = document.createElement('div')
        value.textContent = trend ? `${signed(trend.per_decade)} ${trendUnit(unit)}` : 'Недостаточно данных'
        text.append(coordinates, value)
        tooltip.setContent(text)
        hovered = cell
      }
      tooltip.setLatLng(latlng)
      if (!map.hasLayer(tooltip)) tooltip.addTo(map)
    }
    const select = ({ latlng }: L.LeafletMouseEvent) => {
      const cell = cellAt(latlng)
      if (cell) onSelect(cell)
    }
    map.on('mousemove', move)
    map.on('mouseout movestart', close)
    map.on('click', select)
    return () => {
      map.off('zoom viewreset', position)
      map.off('mousemove', move)
      map.off('mouseout movestart', close)
      map.off('click', select)
      close()
      surface.remove()
      surface.width = surface.height = 0
      canvas.current = null
    }
  }, [grid, map, onSelect, unit])

  useEffect(() => {
    const surface = canvas.current
    if (!surface) return
    let frame = rasters.frames.get(mode)
    if (!frame) {
      frame = renderRegionalRaster(rasters.grid, mode, rasters.limit)
      rasters.frames.set(mode, frame)
    }
    surface.width = frame.width
    surface.height = frame.height
    const context = surface.getContext('2d')
    if (!context) return
    mask.current = boundary ? boundaryRasterPath(boundary, grid, frame.width, frame.height) : null
    if (!mask.current) {
      context.putImageData(frame, 0, 0)
      return
    }
    const buffer = document.createElement('canvas')
    buffer.width = frame.width
    buffer.height = frame.height
    buffer.getContext('2d')?.putImageData(frame, 0, 0)
    context.save()
    context.clip(mask.current, 'evenodd')
    context.drawImage(buffer, 0, 0)
    context.restore()
    buffer.width = buffer.height = 0
  }, [boundary, grid, mode, rasters])
  return null
}

function WorldRaster({ layer, mode, onSelect }: {
  layer: WorldLayer; mode: RasterMode; onSelect: (cell: ReturnType<typeof worldCellAt>) => void
}) {
  const map = useMap()
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const currentLayer = useRef(layer)
  useEffect(() => { currentLayer.current = layer }, [layer])
  const frames = useMemo(() => new Map<RasterMode, ImageData>(), [layer])
  useEffect(() => {
    const pane = map.getPane('regional-cells')!
    const surface = L.DomUtil.create('canvas', 'leaflet-layer rtm-raster', pane)
    surface.setAttribute('aria-hidden', 'true')
    canvas.current = surface
    const position = () => {
      const nw = map.latLngToLayerPoint(WORLD_BOUNDS.getNorthWest())
      const se = map.latLngToLayerPoint(WORLD_BOUNDS.getSouthEast())
      L.DomUtil.setPosition(surface, nw)
      surface.style.width = `${se.x - nw.x}px`
      surface.style.height = `${se.y - nw.y}px`
    }
    const select = ({ latlng }: L.LeafletMouseEvent) => {
      if (WORLD_BOUNDS.contains(latlng)) onSelect(worldCellAt(latlng.lat, latlng.lng))
    }
    const tooltip = L.tooltip({ direction: 'top', offset: [0, -10], className: 'rtm-cell-tooltip' })
    const content = document.createElement('span')
    tooltip.setContent(content)
    const hideTooltip = () => { tooltip.remove() }
    const showTooltip = ({ latlng }: L.LeafletMouseEvent) => {
      if (!WORLD_BOUNDS.contains(latlng)) { hideTooltip(); return }
      const cell = worldCellAt(latlng.lat, latlng.lng)
      const visibleLayer = currentLayer.current
      const slope = visibleLayer.slopes[cell.index]
      const value = Number.isFinite(slope) ? `${signed(slope)} ${trendUnit(visibleLayer.variable === 'temperature' ? '°C' : 'mm')}` : 'Недостаточно данных'
      content.textContent = `${cell.latitude.toFixed(3)}°, ${cell.longitude.toFixed(3)}° · ${value}`
      tooltip.setLatLng(latlng)
      if (!map.hasLayer(tooltip)) tooltip.addTo(map)
    }
    position()
    map.on('zoom viewreset', position)
    map.on('click', select)
    map.on('mousemove', showTooltip)
    map.on('mouseout movestart', hideTooltip)
    return () => {
      map.off('zoom viewreset', position)
      map.off('click', select)
      map.off('mousemove', showTooltip)
      map.off('mouseout movestart', hideTooltip)
      hideTooltip()
      surface.remove()
      surface.width = surface.height = 0
      canvas.current = null
    }
  }, [map, onSelect])
  useEffect(() => {
    if (!canvas.current) return
    canvas.current.style.imageRendering = mode.endsWith('native') ? 'pixelated' : 'auto'
    let frame = frames.get(mode)
    if (!frame) {
      frame = renderWorld(layer, mode)
      frames.set(mode, frame)
    }
    canvas.current.width = frame.width
    canvas.current.height = frame.height
    canvas.current.getContext('2d')?.putImageData(frame, 0, 0)
  }, [frames, layer, mode])
  return null
}

export const RegionalTrendGrid = memo(function RegionalTrendGrid({ data, mode, limit, boundary, selected, onSelect, globalLayer, onSelectWorld, worldSelected }: {
  data: RegionalMapResponse | null; mode: RasterMode; limit: number
  boundary: CountryBoundary | null
  selected: RegionalCell | null; onSelect: (cell: RegionalCell) => void
  globalLayer?: WorldLayer | null; onSelectWorld: (cell: ReturnType<typeof worldCellAt>) => void
  worldSelected?: ReturnType<typeof worldCellAt> | null
}) {
  const outlineRenderer = useMemo(() => L.svg({ pane: 'markerPane' }), [])
  const grid = useMemo(() => data ? createRegionalGrid(data) : null, [data])
  const bounds = useMemo(() => grid ? L.latLngBounds([grid.south, grid.west], [grid.north, grid.east]) : null, [grid])
  const [world, setWorld] = useState<FeatureCollection | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    fetch(WORLD_MAP, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json() as Promise<FeatureCollection>
      })
      .then(setWorld)
      .catch(() => { if (!controller.signal.aborted) setWorld(null) })
    return () => controller.abort()
  }, [])
  return <MapContainer center={REGION_BOUNDS.getCenter()} zoom={5} minZoom={0} maxZoom={10} zoomSnap={0.1}
    maxBounds={WORLD_BOUNDS} maxBoundsViscosity={1} bounceAtZoomLimits={false}
    preferCanvas zoomControl={false} scrollWheelZoom={false} inertia={false}
    zoomAnimation={false} markerZoomAnimation={false} fadeAnimation={false} trackResize={false}
    className="rtm-map-canvas"
    aria-label="Сетка региональных трендов; выбор с клавиатуры доступен в списке ячеек">
    <ZoomControl zoomInTitle="Увеличить" zoomOutTitle="Уменьшить" />
    <RegionalMapControls bounds={globalLayer ? WORLD_BOUNDS : bounds} region={globalLayer ? 'world' : data?.metadata.region_id} />
    {world && <GeoJSON data={world} interactive={false}
      attribution={'<a href="https://www.naturalearthdata.com/">Natural Earth</a>'}
      style={WORLD_STYLE} />}
    <Pane name="regional-cells" style={{ zIndex: 410 }}>
      {data && grid && <RegionalRasterOverlay grid={grid} mode={mode} limit={limit} boundary={boundary} unit={data.metadata.unit} onSelect={onSelect} />}
      {globalLayer && <WorldRaster layer={globalLayer} mode={mode} onSelect={onSelectWorld} />}
    </Pane>
    {(data || globalLayer) && (boundary || world) && <Pane name="regional-borders" style={{ zIndex: 420, pointerEvents: 'none' }}>
      {boundary ? <GeoJSON key="country" data={boundary} interactive={false} style={BORDER_STYLE} />
        : world && <GeoJSON key="world" data={world} interactive={false} style={BORDER_STYLE} />}
    </Pane>}
    {selected && <Polygon positions={selected.geometry.coordinates.map(ring => ring.map(([lng, lat]) => [lat, lng] as L.LatLngTuple))}
      renderer={outlineRenderer} interactive={false} pathOptions={{ color: '#fff', weight: 2, fill: false, className: 'rtm-selected-outline' }} />}
    {globalLayer && worldSelected && <Polygon positions={[
      [worldSelected.latitude - .25, worldSelected.longitude - .3125],
      [worldSelected.latitude - .25, worldSelected.longitude + .3125],
      [worldSelected.latitude + .25, worldSelected.longitude + .3125],
      [worldSelected.latitude + .25, worldSelected.longitude - .3125],
    ]} renderer={outlineRenderer} interactive={false} pathOptions={{ color: '#fff', weight: 2, fill: false, className: 'rtm-selected-outline' }} />}
  </MapContainer>
})
