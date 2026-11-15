# EarthScope 2.5 — regional climate trend map

## Verified NASA POWER input

The MVP uses one bounded rectangle, **Kyrgyzstan and neighboring areas**: latitude 39–44° N, longitude 69–81° E, inclusive. It is a rectangular demonstration extent, not a national boundary. NASA's Monthly/Annual Regional API requires one parameter per request. Observed validation errors require at least a 2° span in both dimensions and reject a longitude span above 10°. The MVP therefore requests two sequential 6° longitude tiles, 69–75° E and 75–81° E, with a delay and per-request disk caching.

The live 1990–2025 T2M regional probe using community RE returned 110 features in each tile and 209 unique centers after tile-boundary deduplication. Its grid had 11 latitude rows at 0.5° spacing and 19 longitude columns at 0.625° spacing. Each cell carried 468 keys (12 months plus the NASA annual key for each of 36 years), with no fill values in that probe. A separate community AG request for the matching cell returned the same grid coordinate, metadata and 2016 T2M/PRECTOTCORR values as RE; the MVP uses AG to align with the existing Daily Point community. The full AG grid is revalidated during data ingestion. The response uses GeoJSON features with `[longitude, latitude, elevation_m]`; grid spacing is inferred from returned centers and validated, never assumed for an arbitrary source. NASA's example is 0.5° × 0.5°, but NASA says the API serves source-native resolution.

Checked 2026-11-14: Regional response header reports source `MERRA2` (displayed in documentation as MERRA-2), Monthly and Annual API v2.10.0, `time_standard=LST`; parameter metadata reports T2M in `C` and PRECTOTCORR in `mm/day`. The matching Daily Point probe reports MERRA-2 v2.10.3 in UTC. At (73.75° E, 41.0° N) in 2016, annual T2M was 7.16 °C regionally versus 7.155 °C from Daily UTC. For precipitation, the sum of the 12 monthly rates times calendar days was 286.08 mm versus 285.52 mm from Daily UTC (about 0.2%). These are consistent but not identical temporal products; regional and existing point results must not be silently mixed.

Sources: [NASA POWER Monthly and Annual API](https://power.larc.nasa.gov/docs/services/api/temporal/monthly/) (Regional endpoint and one-parameter limit; accessed 2026-11-14); [NASA POWER Daily API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) (accessed 2026-11-14). NASA documentation is publicly accessible; license and rate-limit terms were not independently established in this implementation.

## Contract

`GET /api/maps/regions` returns the available demonstration region and its requested bounds, fixed supported year range (1990–2025), and supported variables. It does not load NASA data.

`GET /api/maps/trends?region=kg-surroundings&variable=temperature&start_year=1990&end_year=2025` builds or reuses one regional layer. Variables are `temperature` (T2M, °C) and `precipitation` (PRECTOTCORR, annual total mm). Years are inclusive and must lie inside 1990–2025. The first cold-cache build performs two bounded, sequential NASA requests for one parameter. Changing map viewport never calls this route. The backend caches raw NASA tile responses for 30 days and caches processed results against the raw source retrieval timestamp, request URLs, dataset, and API version. A refreshed raw source therefore invalidates the derived layer. Identical concurrent builds are deduplicated.

The response is a GeoJSON FeatureCollection containing metadata and at most 209 cell features. Each feature is a polygon for display, with properties for stable cell ID, returned center coordinates and elevation, coverage/quality, per-decade slope, pointwise HAC interval, raw p, BY-adjusted q, and the selected-period annual values. Cell footprints are constructed from the observed native grid spacing around the returned centers and clipped to the declared rectangle; these polygons are a display representation, not separately supplied NASA measurements. The response includes the full selected annual series for each cell, so selecting cells creates no extra NASA request and needs no per-cell endpoint.

## Calculation and inference

### Display raster

The map builds its Canvas image from the original cell centers, footprints and per-decade slopes. The raster is sampled in the map's Web Mercator projection. Smooth mode uses bilinear interpolation in geographic coordinates inside the data footprint. It requires four neighboring trend values. Near missing data the valid cell retains its native value and the missing cell stays transparent. Native and BY modes sample the original cell footprint and keep discrete values or classes. Clicks and tooltips resolve to original cells. The displayed surface has no role in analysis, inference or exports.

The optional Kyrgyzstan display mask is the KGZ feature from [World Bank Official Boundaries, ADM0](https://datacatalog.worldbank.org/search/dataset/0038272/world-bank-official-boundaries), CC BY 4.0, retrieved November 14, 2026. The bundled GeoJSON keeps the country geometry with coordinates rounded to five decimal places from the [World Bank feature service](https://services.arcgis.com/iQ1dY19aHwbSDYIF/arcgis/rest/services/World_Bank_Global_Administrative_Divisions/FeatureServer/1). The mask clips the display. The NASA data extent, resolution, statistics and CSV exports retain their original coverage.

- Temperature uses NASA's `YYYY13` annual T2M mean key, but accepts that year only when the annual key and all 12 month keys are finite and not the `-999` fill value.
- Precipitation ignores the annual daily-rate key for totals. It computes `sum(monthly_PRECTOTCORR_mm_per_day × calendar_days_in_month)` and accepts the year only when all 12 monthly values are valid. February uses the Gregorian leap-year calendar.
- Missing months/years stay missing. No interpolation, zero-fill, or cross-product substitution is allowed.
- OLS estimates a descriptive linear slope against calendar year. A slope requires at least five complete years. HAC(3) CI and raw p-value require at least 27 complete consecutive years in the selected span. Shorter, missing, or gapped series can show a descriptive slope only when estimable; inferential fields remain null. Pointwise intervals are not simultaneous intervals.
- Adjust raw p-values with Benjamini–Yekutieli at q = 0.10 separately for each variable, period, and regional cell family. BY is selected for control under arbitrary dependence; it does not correct temporal-model misspecification or make adjacent cells independent. The UI must say that q > 0.10 means insufficient evidence under this test, not no climate change.
- Report sensitivity p-values for HAC(1), HAC(3), and HAC(5) when inference is available. No regional aggregate, attribution, or forecast is claimed.

## Response shape (TypeScript sketch)

```ts
interface RegionalMapResponse {
  type: 'FeatureCollection'
  metadata: {
    region_id: 'kg-surroundings'
    region_label: string
    bounds: { south: number; north: number; west: number; east: number }
    variable: 'temperature' | 'precipitation'
    parameter: 'T2M' | 'PRECTOTCORR'
    unit: '°C' | 'mm'
    aggregation: 'annual_mean' | 'sum_monthly_daily_rate_times_days'
    start_year: number
    end_year: number
    resolution: { latitude_degrees: number; longitude_degrees: number }
    source: {
      name: 'NASA POWER'; dataset: string; api_version: string
      time_standard: 'LST'; community: 'AG'; request_urls: string[]
      retrieved_at: string; cached: boolean; analysis_cached: boolean
    }
    inference: { method: string; hac_lags: 3; fdr: 'BY'; fdr_q: 0.1; tested_cells: number }
    summary: { total_cells: number; trend_cells: number; fdr_cells: number }
  }
  features: Array<{
    type: 'Feature'
    id: string
    geometry: { type: 'Polygon'; coordinates: number[][][] }
    properties: {
      cell_id: string; latitude: number; longitude: number; elevation_m: number | null
      quality: 'complete' | 'incomplete' | 'insufficient_years'
      valid_years: number; expected_years: number; max_gap_years: number
      annual: Array<{ year: number; value: number | null; valid_months: number }>
      trend: null | {
        per_decade: number; ci95_low: number | null; ci95_high: number | null
        p_value: number | null; q_value_by: number | null; significant_by: boolean | null
        hac_sensitivity: Array<{ lags: 1 | 3 | 5; p_value: number }>
      }
    }
  }>
}
```

Errors use the existing `{"error":{"code":"...","message":"..."}}` API envelope. The first version is a synchronous, bounded 209-cell calculation; a job/status API is unnecessary unless live measurements show this exceeds request limits.
