# EarthScope 2.3 API

Both routes are GET, use the existing JSON error envelope and accept WGS84
`latitude`, `longitude`, `variable` (temperature or precipitation),
`start_year`, `end_year` (inclusive, 1981–2025). All responses share these
same fields, plus `unit` (°C or mm), `aggregation` (mean or sum), existing
`source: SourceInfo` and `warnings: string[]`. Source describes the actual
downloaded (or cached) daily POWER series. Each request loads at most one
contiguous range, reusing the exact-query cache.

* `/api/anomalies`: additionally accepts `baseline_start_year`,
  `baseline_end_year` (defaults 1991 and 2020) in the same coverage (baseline can be outside the display
  period). Pull the union span. Response adds `baseline_start_year`,
  `baseline_end_year`, `baseline_mean: number|null`,
  `baseline_complete_years: number`, and `series` of
  `{year, value: number|null, anomaly: number|null, valid_days, expected_days}`.
  Value is the annual aggregate; anomaly is value minus the equally weighted
  mean of annual aggregates. Strict rule: every day of every baseline year
  must be valid; otherwise baseline_mean and all anomalies are null. Missing
  display years stay null. 1991–2020 is a 30-year preset; custom shorter
  baselines are references, not climate normals.
* `/api/seasonal`: additionally accepts `period` =
  winter|spring|summer|autumn|all|m01…m12 (default all). Response adds `period` and
  `periods: {period: string, series: SeriesPoint[], trend: TrendStats|null}[]`
  with the existing series and trend DTOs. For `all`, return winter, spring,
  summer, autumn in order using one daily fetch; for other periods return one
  entry. Winter uses previous December + current Jan–Feb. Fetch the prior
  year if available; winter 1981 remains null, even if Jan–Feb are complete.
  Temperatures average daily values, precipitation sums daily rates integrated
  over one-day intervals. Any missing or invalid expected UTC day yields a
  null period value for that year; existing OLS uses at least 5 complete years.
  The frontend may describe the largest absolute API slope only when the
  available fits use the same set of complete years; it never infers a
  statistically established difference across seasons.

Internal Data contract (`backend/app/climate_data.py`):
`anomaly_series(daily, variable, start_year, end_year, baseline_start_year,
baseline_end_year) -> tuple[list[dict], float|None, int]` returns display rows,
baseline mean, and complete baseline count.
`aggregate_period(daily, variable, start_year, end_year, period) ->
list[AnnualAggregate]` receives a single period code (never all). Reuse annual
aggregation and the existing dataclass. Backend owns `climate.py`, `main.py`
and `schemas.py`; Data owns `climate_data.py`.
