import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { GeoJSON, ImageOverlay, MapContainer, Marker, Polyline, Tooltip, ZoomControl, useMap, useMapEvents } from 'react-leaflet'
import { LocateFixed } from 'lucide-react'
import L, { type LatLngExpression, type LeafletMouseEvent } from 'leaflet'
import type { FeatureCollection } from 'geojson'

import { cn } from '../lib/cn'
import { formatCoordinate } from '../lib/format'
import { ru } from '../lib/ru'
import { Button } from './ui/button'

const WORLD_MAP = `${import.meta.env.BASE_URL}world.geojson`
const ATTRIBUTION = '<a href="https://www.naturalearthdata.com/">Natural Earth</a>'
const BLUE_MARBLE = `${import.meta.env.BASE_URL}blue-marble-mercator.webp`
const MARBLE_CREDIT = '<a href="https://worldview.earthdata.nasa.gov/?l=BlueMarble_ShadedRelief">NASA Earth Observatory · Blue Marble</a>'
const MERCATOR_EDGE = 85.0511287798
const MARBLE_BOUNDS = L.latLngBounds([-MERCATOR_EDGE, -180], [MERCATOR_EDGE, 180])
const GRATICULE: LatLngExpression[][] = [
  ...Array.from({ length: 23 }, (_, i): LatLngExpression[] => [[-MERCATOR_EDGE, 15 * (i - 11)], [MERCATOR_EDGE, 15 * (i - 11)]]),
  ...Array.from({ length: 11 }, (_, i): LatLngExpression[] => [[15 * (i - 5), -180], [15 * (i - 5), 180]]),
]
const GRID_STYLE = { color: '#c0c0c0', opacity: 0.22, weight: 1, dashArray: '2 5' }

export interface MapSelection {
  latitude: number
  longitude: number
}

const ZOOM = 4
const MIN_ZOOM = 2
const MAX_ZOOM = 12
const WORLD_STYLE = { color: '#6d6d6d', weight: 1, fillColor: '#363636', fillOpacity: 1 }
const MARBLE_BORDER_STYLE = { color: '#cccccc', weight: 0.85, opacity: 0.55, fillOpacity: 0 }

/** High-contrast marker for the selected grid point. */
const pointIcon = L.divIcon({
  className: 'earthscope-point',
  html: '<span class="earthscope-point-ring"></span><span class="earthscope-point-core"></span>',
  iconSize: [22, 22],
  iconAnchor: [11, 11],
})
const otherIcon = L.divIcon({
  className: 'earthscope-point',
  html: '<span style="display:block;width:18px;height:18px;border:3px solid #e5a35e;border-radius:50%;background:#111418"></span>',
  iconSize: [22, 22],
  iconAnchor: [11, 11],
})

function MapControls({ selection, focusRequest }: { selection: MapSelection; focusRequest: number }) {
  const map = useMap()
  const handledRequest = useRef(focusRequest)
  const focusPoint = useCallback(() => {
    map.stop()
    map.panTo([selection.latitude, selection.longitude], { animate: false })
  }, [map, selection.latitude, selection.longitude])

  useEffect(() => {
    if (handledRequest.current === focusRequest) return
    handledRequest.current = focusRequest
    focusPoint()
  }, [focusRequest, focusPoint])

  useEffect(() => {
    const element = map.getContainer()
    let width = element.clientWidth
    let height = element.clientHeight
    const observer = new ResizeObserver(() => {
      if (width === element.clientWidth && height === element.clientHeight) return
      width = element.clientWidth
      height = element.clientHeight
      if (width && height) map.invalidateSize({ animate: false, debounceMoveend: true })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [map])

  return (
    <Button type="button" variant="outline" size="sm" onClick={focusPoint}
      ref={element => { if (element) L.DomEvent.disableClickPropagation(element) }}
      className="absolute right-2 top-2 z-[600] h-10 bg-panel">
      <LocateFixed aria-hidden="true" />
      {ru.map.center}
    </Button>
  )
}

function ClickCatcher({ onSelect }: { onSelect: (selection: MapSelection) => void }) {
  useMapEvents({
    click(event: LeafletMouseEvent) {
      const point = event.latlng.wrap()
      onSelect({ latitude: point.lat, longitude: point.lng })
    },
  })
  return null
}

export const LocationMap = memo(function LocationMap({
  selection,
  onSelect,
  className,
  focusRequest = 0,
  describedBy,
  selectionLabel,
  otherSelection,
}: {
  selection: MapSelection
  onSelect: (selection: MapSelection) => void
  className?: string
  focusRequest?: number
  describedBy?: string
  selectionLabel?: string
  otherSelection?: MapSelection & { label: string }
}) {
  const center: LatLngExpression = [selection.latitude, selection.longitude]
  const initialCenter = useRef(center)
  const [world, setWorld] = useState<FeatureCollection | null>(null)
  const [mapError, setMapError] = useState(false)
  const [marbleReady, setMarbleReady] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    fetch(WORLD_MAP, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(ru.map.unavailable)
        return response.json() as Promise<FeatureCollection>
      })
      .then(setWorld)
      .catch(() => {
        if (!controller.signal.aborted) setMapError(true)
      })
    return () => controller.abort()
  }, [])

  return (
    <div role="region" aria-label={ru.map.label} aria-describedby={describedBy} className={cn('relative isolate overflow-hidden rounded border border-line bg-base', className)}>
      <MapContainer
        center={initialCenter.current}
        zoom={ZOOM}
        minZoom={MIN_ZOOM}
        maxZoom={MAX_ZOOM}
        scrollWheelZoom={false}
        zoomControl={false}
        inertia={false}
        zoomAnimation={false}
        markerZoomAnimation={false}
        fadeAnimation={false}
        trackResize={false}
        className="location-map h-full w-full"
      >
        <ZoomControl zoomInTitle={ru.map.zoomIn} zoomOutTitle={ru.map.zoomOut} />
        <ImageOverlay url={BLUE_MARBLE} bounds={MARBLE_BOUNDS} pane="tilePane" className="location-map-imagery"
          interactive={false} alt="" attribution={marbleReady ? MARBLE_CREDIT : undefined}
          eventHandlers={{ load: () => setMarbleReady(true) }} />
        {world && (
          <GeoJSON
            data={world}
            interactive={false}
            attribution={ATTRIBUTION}
            style={marbleReady ? MARBLE_BORDER_STYLE : WORLD_STYLE}
          />
        )}
        <Polyline positions={GRATICULE} pathOptions={GRID_STYLE} interactive={false} />
        <ClickCatcher onSelect={onSelect} />
        <MapControls selection={selection} focusRequest={focusRequest} />
        <Marker position={center} icon={selectionLabel === 'B' ? otherIcon : pointIcon} keyboard={false} title={selectionLabel ?? ru.map.selected}>
          {selectionLabel && <Tooltip permanent direction="top">{selectionLabel} · активная</Tooltip>}
        </Marker>
        {otherSelection && <Marker position={[otherSelection.latitude, otherSelection.longitude]} icon={otherSelection.label === 'A' ? pointIcon : otherIcon} keyboard={false} title={otherSelection.label}>
          <Tooltip permanent direction="bottom">{otherSelection.label}</Tooltip>
        </Marker>}
      </MapContainer>

      <div className="pointer-events-none absolute bottom-5 left-3 z-[500] rounded border border-line bg-base px-2.5 py-1.5 font-mono text-[11px] tabular-nums text-ink">
        {formatCoordinate(Math.abs(selection.latitude))}° {selection.latitude < 0 ? ru.map.south : ru.map.north} ·{' '}
        {formatCoordinate(Math.abs(selection.longitude))}° {selection.longitude < 0 ? ru.map.west : ru.map.east}
      </div>
      {mapError && <p role="status" className="absolute inset-x-3 top-14 z-[500] rounded border border-line bg-base p-2 text-xs">{ru.map.unavailable}</p>}
    </div>
  )
})
