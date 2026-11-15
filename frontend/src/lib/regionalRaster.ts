import { REGIONAL_COLORS, type RegionalCell, type RegionalMapResponse } from './regionalMap'
import type { MultiPolygon, Polygon } from 'geojson'

export type RasterMode = 'smooth' | 'native' | 'significance' | 'significance-native'

export const mercatorY = (latitude: number) => Math.asinh(Math.tan(latitude * Math.PI / 180))
export const inverseMercatorY = (value: number) => Math.atan(Math.sinh(value)) * 180 / Math.PI

export function createRegionalGrid(data: RegionalMapResponse) {
  if (!data.features.length) return null
  const xs = data.features.map(cell => cell.properties.longitude)
  const ys = data.features.map(cell => cell.properties.latitude)
  const firstX = Math.min(...xs), firstY = Math.min(...ys)
  const dx = data.metadata.resolution.longitude_degrees
  const dy = data.metadata.resolution.latitude_degrees
  const cols = Math.round((Math.max(...xs) - firstX) / dx) + 1
  const rows = Math.round((Math.max(...ys) - firstY) / dy) + 1
  const cells = new Map<number, { cell: RegionalCell; west: number; east: number; south: number; north: number }>()
  let west = Infinity, east = -Infinity, south = Infinity, north = -Infinity

  for (const cell of data.features) {
    const ring = cell.geometry.coordinates[0]
    const x = Math.round((cell.properties.longitude - firstX) / dx)
    const y = Math.round((cell.properties.latitude - firstY) / dy)
    const extent = {
      west: Math.min(...ring.map(point => point[0])), east: Math.max(...ring.map(point => point[0])),
      south: Math.min(...ring.map(point => point[1])), north: Math.max(...ring.map(point => point[1])),
    }
    cells.set(y * cols + x, { cell, ...extent })
    west = Math.min(west, extent.west); east = Math.max(east, extent.east)
    south = Math.min(south, extent.south); north = Math.max(north, extent.north)
  }

  const at = (x: number, y: number) => x >= 0 && x < cols && y >= 0 && y < rows ? cells.get(y * cols + x) : undefined
  const cellAt = (longitude: number, latitude: number) => {
    const entry = at(Math.round((longitude - firstX) / dx), Math.round((latitude - firstY) / dy))
    if (entry && longitude >= entry.west && longitude <= entry.east && latitude >= entry.south && latitude <= entry.north) return entry.cell
    return undefined
  }

  function smoothValue(longitude: number, latitude: number, ownValue: number) {
    const x = Math.max(0, Math.min(cols - 1, (longitude - firstX) / dx))
    const y = Math.max(0, Math.min(rows - 1, (latitude - firstY) / dy))
    const x0 = Math.floor(x), y0 = Math.floor(y)
    const fx = x - x0, fy = y - y0
    const a = at(x0, y0)?.cell.properties.trend?.per_decade
    const b = at(Math.min(x0 + 1, cols - 1), y0)?.cell.properties.trend?.per_decade
    const c = at(x0, Math.min(y0 + 1, rows - 1))?.cell.properties.trend?.per_decade
    const d = at(Math.min(x0 + 1, cols - 1), Math.min(y0 + 1, rows - 1))?.cell.properties.trend?.per_decade
    // Avoid borrowing values across a hole. Keep the native value on valid cells next to it.
    if (a == null || b == null || c == null || d == null) return ownValue
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy
  }

  return { west, east, south, north, cellAt, smoothValue }
}

export type RegionalGrid = NonNullable<ReturnType<typeof createRegionalGrid>>
export type CountryBoundary = Polygon | MultiPolygon

export function boundaryRasterPath(boundary: CountryBoundary, grid: RegionalGrid, width: number, height: number) {
  const path = new Path2D()
  const top = mercatorY(grid.north), range = top - mercatorY(grid.south)
  const polygons = boundary.type === 'Polygon' ? [boundary.coordinates] : boundary.coordinates
  for (const polygon of polygons) for (const ring of polygon) {
    ring.forEach(([longitude, latitude], i) => {
      const x = (longitude - grid.west) / (grid.east - grid.west) * width
      const y = (top - mercatorY(latitude)) / range * height
      if (i === 0) path.moveTo(x, y)
      else path.lineTo(x, y)
    })
    path.closePath()
  }
  return path
}

export function rasterPoint(grid: RegionalGrid, longitude: number, latitude: number, width: number, height: number) {
  const top = mercatorY(grid.north)
  return [(longitude - grid.west) / (grid.east - grid.west) * width,
    (top - mercatorY(latitude)) / (top - mercatorY(grid.south)) * height] as const
}
const rgb = (hex: string) => [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16))
const negative = rgb(REGIONAL_COLORS.negative), zero = rgb(REGIONAL_COLORS.zero), positive = rgb(REGIONAL_COLORS.positive)
const significant = rgb(REGIONAL_COLORS.significant), notSignificant = rgb(REGIONAL_COLORS.notSignificant)

export function setRasterPixel(pixels: Uint8ClampedArray, offset: number, mode: RasterMode, value: number, limit: number) {
  if (!Number.isFinite(value)) return
  if (mode.startsWith('significance')) {
    for (let channel = 0; channel < 3; channel++) {
      pixels[offset + channel] = notSignificant[channel] + (significant[channel] - notSignificant[channel]) * value
    }
  } else {
    const end = value < 0 ? negative : positive
    const weight = Math.min(1, Math.abs(value) / limit)
    for (let channel = 0; channel < 3; channel++) {
      pixels[offset + channel] = zero[channel] + (end[channel] - zero[channel]) * weight
    }
  }
  pixels[offset + 3] = 255
}

export function renderRegionalRaster(grid: RegionalGrid, mode: RasterMode, limit: number) {
  const top = mercatorY(grid.north), bottom = mercatorY(grid.south)
  const longitudeSpan = grid.east - grid.west
  const ratio = (top - bottom) / (longitudeSpan * Math.PI / 180)
  const width = Math.max(1, Math.round(1440 / Math.max(1, ratio)))
  const height = Math.max(1, Math.round(width * ratio))
  const raster = new ImageData(width, height)
  for (let y = 0; y < height; y++) {
    const latitude = inverseMercatorY(top + (bottom - top) * (y + 0.5) / height)
    for (let x = 0; x < width; x++) {
      const longitude = grid.west + longitudeSpan * (x + 0.5) / width
      const trend = grid.cellAt(longitude, latitude)?.properties.trend
      if (!trend || (mode.startsWith('significance') && trend.significant_by == null)) continue
      const offset = (y * width + x) * 4
      const value = mode.startsWith('significance') ? Number(trend.significant_by) : mode === 'smooth'
        ? grid.smoothValue(longitude, latitude, trend.per_decade) : trend.per_decade
      setRasterPixel(raster.data, offset, mode, value, limit)
    }
  }
  return raster
}
