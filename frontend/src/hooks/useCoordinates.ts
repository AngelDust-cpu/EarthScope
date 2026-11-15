import { useCallback, useState } from 'react'

export interface Coordinates {
  latitude: number
  longitude: number
}

/** Preset required by the brief: Bishkek, Kyrgyzstan. */
export const BISHKEK: Coordinates = { latitude: 42.87, longitude: 74.6 }
export const REYKJAVIK: Coordinates = { latitude: 64.147, longitude: -21.942 }

const clamp = (value: number, min: number, max: number): number => Math.min(max, Math.max(min, value))

export function roundCoordinate(value: number): number {
  return Math.round(value * 1000) / 1000
}

/** Normalise a map longitude into [-180, 180). */
export function normalizeLongitude(value: number): number {
  return ((((value + 180) % 360) + 360) % 360) - 180
}

/**
 * Single owner of the selected grid point. Numeric entry, map clicks and the
 * preset all funnel through here so validation and rounding cannot diverge.
 */
export function useCoordinates(initial: Coordinates = BISHKEK) {
  const [coordinates, setCoordinates] = useState<Coordinates>(initial)

  const setFromMap = useCallback((latitude: number, longitude: number) => {
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return
    setCoordinates({
      latitude: roundCoordinate(clamp(latitude, -90, 90)),
      longitude: roundCoordinate(normalizeLongitude(longitude)),
    })
  }, [])

  const setNumeric = useCallback((latitude: number, longitude: number) => {
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return
    setCoordinates({
      latitude: roundCoordinate(clamp(latitude, -90, 90)),
      longitude: roundCoordinate(clamp(longitude, -180, 180)),
    })
  }, [])

  const applyPreset = useCallback((preset: Coordinates) => {
    setCoordinates({ latitude: preset.latitude, longitude: preset.longitude })
  }, [])

  const isBishkek =
    coordinates.latitude === BISHKEK.latitude && coordinates.longitude === BISHKEK.longitude

  return { coordinates, setFromMap, setNumeric, applyPreset, isBishkek }
}
