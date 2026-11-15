import { apiUrl } from './apiBase'
import type { RegionalVariable } from './regionalMap'
import { inverseMercatorY, mercatorY, setRasterPixel, type RasterMode } from './regionalRaster'

const HEIGHT = 361, WIDTH = 576, SIZE = WIDTH * HEIGHT
export const WORLD_LATITUDE = 85.0511287798
export const WORLD_SOURCE = 'https://registry.opendata.aws/nasa-power/'
export const WORLD_YEARS = '1990–2025'

export interface WorldLayer {
  slopes: Float32Array
  flags: Float32Array
  limit: number
  tested: number
  version: string
  variable: RegionalVariable
}

export interface WorldCellDetails {
  latitude: number; longitude: number; parameter: string; unit: string
  slope: number | null; ci_low: number | null; ci_high: number | null
  p: number | null; q: number | null; version: string
  series: Array<{ year: number; value: number | null }>
}

export async function fetchWorld(variable: RegionalVariable, signal: AbortSignal): Promise<WorldLayer> {
  const response = await fetch(apiUrl(`/api/maps/world?variable=${variable}`), { signal })
  if (response.status === 503) throw new Error('Глобальный архив NASA POWER ещё не подготовлен на сервере.')
  if (!response.ok) throw new Error('Не удалось загрузить глобальный слой.')
  const body = await response.arrayBuffer()
  const limit = Number(response.headers.get('X-EarthScope-Scale'))
  if (body.byteLength !== SIZE * 8 || !(limit > 0)) throw new Error('Повреждён глобальный слой NASA POWER.')
  return { slopes: new Float32Array(body, 0, SIZE), flags: new Float32Array(body, SIZE * 4, SIZE),
    limit, tested: Number(response.headers.get('X-EarthScope-Tested')), version: response.headers.get('X-EarthScope-Power-Version') ?? '', variable }
}

export function worldCellAt(latitude: number, longitude: number) {
  const col = wrappedColumn(Math.round((longitude + 180) / .625))
  const row = Math.max(0, Math.min(HEIGHT - 1, Math.round((latitude + 90) / .5)))
  return { row, col, latitude: -90 + row * .5, longitude: -180 + col * .625, index: row * WIDTH + col }
}

export async function fetchWorldCell(variable: RegionalVariable, row: number, col: number, signal: AbortSignal): Promise<WorldCellDetails> {
  const response = await fetch(apiUrl(`/api/maps/world/cell?variable=${variable}&row=${row}&col=${col}`), { signal })
  if (!response.ok) throw new Error('Не удалось открыть годовой ряд.')
  return response.json()
}

const wrappedColumn = (column: number) => ((column % WIDTH) + WIDTH) % WIDTH

function significanceSurface(flags: Float32Array) {
  const surface = new Float32Array(SIZE)
  const radius = 2
  const weights = Array.from({ length: radius + 1 }, (_, offset) => Math.exp(-(offset ** 2) / 1.28))
  for (let row = 0; row < HEIGHT; row++) {
    for (let column = 0; column < WIDTH; column++) {
      const index = row * WIDTH + column
      if (!Number.isFinite(flags[index])) {
        surface[index] = NaN
        continue
      }
      let total = 0
      let weightSum = 0
      for (let dy = Math.max(-radius, -row); dy <= Math.min(radius, HEIGHT - 1 - row); dy++) {
        for (let dx = -radius; dx <= radius; dx++) {
          const flag = flags[(row + dy) * WIDTH + wrappedColumn(column + dx)]
          if (!Number.isFinite(flag)) continue
          const weight = weights[Math.abs(dx)] * weights[Math.abs(dy)]
          total += flag * weight
          weightSum += weight
        }
      }
      surface[index] = total / weightSum
    }
  }
  return surface
}

export function renderWorld(layer: WorldLayer, mode: RasterMode) {
  const size = 1440
  const samples = mode === 'significance' ? significanceSurface(layer.flags)
    : mode === 'significance-native' ? layer.flags : layer.slopes
  const frame = new ImageData(size, size)
  const top = mercatorY(WORLD_LATITUDE)
  for (let y = 0; y < size; y++) {
    const latitude = inverseMercatorY(top * (1 - 2 * (y + .5) / size))
    const gridY = (latitude + 90) / .5
    const row = Math.floor(gridY), fy = gridY - row
    const nearestRow = Math.round(gridY) * WIDTH
    for (let x = 0; x < size; x++) {
      const gridX = (x + .5) / size * WIDTH
      const column = Math.floor(gridX), fx = gridX - column
      const index = nearestRow + wrappedColumn(Math.round(gridX))
      let value = samples[index]
      if (!mode.endsWith('native') && Number.isFinite(value)) {
        const next = wrappedColumn(column + 1)
        const a = samples[row * WIDTH + column], b = samples[row * WIDTH + next]
        const c = samples[(row + 1) * WIDTH + column], d = samples[(row + 1) * WIDTH + next]
        if (Number.isFinite(a + b + c + d)) value = (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy
      }
      setRasterPixel(frame.data, (y * size + x) * 4, mode, value, layer.limit)
    }
  }
  return frame
}
