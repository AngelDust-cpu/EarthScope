"""Response models for the regional map API (docs/REGIONAL-MAP-API.md).

``GET /api/maps/regions`` is a static capability document.  ``GET
/api/maps/trends`` returns one GeoJSON ``FeatureCollection`` with a bounded
209-cell display grid: real returned cell centres, clipped display rectangles,
the full selected-period annual series, and descriptive/HAC trend statistics
after Benjamini-Yekutieli adjustment.

The models are strict for the same reason as :mod:`app.schemas`: the API must
never emit a partially populated trend, a non-finite statistic, a feature that
skips a requested year, or per-cell inference that disagrees with the reported
q-value.  Those invariants are enforced here at the HTTP boundary as well as
inside the builder and its tests.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .schemas import MIN_TREND_YEARS, Variable

# The MVP demonstration extent is fixed by docs/REGIONAL-MAP-API.md: Kyrgyzstan
# and neighbouring areas, inclusive, with the 1990-2025 monthly/annual product.
REGIONAL_YEAR_MIN = 1990
REGIONAL_YEAR_MAX = 2025
REGIONAL_MAX_CELLS = 209

# A pointwise HAC interval is reported only for a series with at least 27
# complete consecutive years; the regional builder also requires the whole
# requested span to be complete, so no gap can hide behind the threshold.
MIN_INFERENCE_YEARS = 27
PRIMARY_HAC_LAGS = 3
HAC_SENSITIVITY_LAGS: tuple[int, ...] = (1, 3, 5)
FDR_METHOD = "BY"
FDR_Q = 0.10
INFERENCE_METHOD = "ols_slope_hac_bartlett"

Quality = Literal["complete", "incomplete", "insufficient_years"]
RegionalAggregation = Literal["annual_mean", "sum_monthly_daily_rate_times_days"]


def _require_finite(value: float, field: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def _finite_or_null(value: float | None, field: str) -> float | None:
    if value is None:
        return None
    return _require_finite(value, field)


class RegionalBounds(BaseModel):
    """Inclusive WGS84 display rectangle, in decimal degrees."""

    south: float = Field(ge=-90.0, le=90.0)
    north: float = Field(ge=-90.0, le=90.0)
    west: float = Field(ge=-180.0, le=180.0)
    east: float = Field(ge=-180.0, le=180.0)

    @field_validator("south", "north", "west", "east")
    @classmethod
    def _finite_edge(cls, value: float) -> float:
        return _require_finite(value, "bounds edge")

    @model_validator(mode="after")
    def _ordered_edges(self) -> "RegionalBounds":
        if self.south >= self.north:
            raise ValueError("south must be less than north")
        if self.west >= self.east:
            raise ValueError("west must be less than east")
        return self


class RegionalResolution(BaseModel):
    """Observed native grid spacing, inferred from returned cell centres."""

    latitude_degrees: float = Field(gt=0.0)
    longitude_degrees: float = Field(gt=0.0)

    @field_validator("latitude_degrees", "longitude_degrees")
    @classmethod
    def _finite_spacing(cls, value: float) -> float:
        return _require_finite(value, "grid spacing")


class RegionalSourceInfo(BaseModel):
    """Provenance of one regional layer.

    The regional product is the NASA POWER Monthly/Annual Regional endpoint in
    LST, community AG.  It must never be presented as the Daily Point (UTC)
    product, so the temporal identifiers are literals rather than free text.
    """

    name: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    api_version: str = Field(min_length=1)
    time_standard: Literal["LST"] = "LST"
    community: Literal["AG"] = "AG"
    request_urls: list[str] = Field(min_length=1)
    retrieved_at: str = Field(min_length=1)
    cached: bool
    analysis_cached: bool

    @field_validator("request_urls")
    @classmethod
    def _https_urls(cls, urls: list[str]) -> list[str]:
        for url in urls:
            if not url.startswith("https://") or len(url) <= len("https://"):
                raise ValueError("request_urls must be non-empty HTTPS URLs")
        return urls


class RegionalHacSensitivity(BaseModel):
    """One HAC lag specification reported for a cell's slope."""

    lags: Literal[1, 3, 5]
    p_value: float = Field(ge=0.0, le=1.0)

    @field_validator("p_value")
    @classmethod
    def _finite_p(cls, value: float) -> float:
        return _require_finite(value, "sensitivity p_value")


class RegionalTrend(BaseModel):
    """Descriptive slope, pointwise HAC(3) inference and BY-adjusted q.

    ``per_decade`` exists whenever at least five complete years are estimable.
    Inferential fields stay null unless the cell has at least 27 complete
    consecutive years inside the requested period.  A null p/CI/q is not
    evidence of no change; it means this test was not applied.
    """

    per_decade: float
    ci95_low: float | None = None
    ci95_high: float | None = None
    p_value: float | None = Field(default=None, ge=0.0, le=1.0)
    q_value_by: float | None = Field(default=None, ge=0.0, le=1.0)
    significant_by: bool | None = None
    hac_sensitivity: list[RegionalHacSensitivity] = Field(default_factory=list)

    @field_validator(
        "per_decade", "ci95_low", "ci95_high", "p_value", "q_value_by"
    )
    @classmethod
    def _finite_statistic(cls, value: float | None) -> float | None:
        return _finite_or_null(value, "regional trend statistic")

    @model_validator(mode="after")
    def _coherent_inference(self) -> "RegionalTrend":
        if (self.ci95_low is None) != (self.ci95_high is None):
            raise ValueError("ci95_low and ci95_high must both be present or null")
        if self.ci95_low is not None and self.ci95_low > self.ci95_high:
            raise ValueError("ci95_low must not exceed ci95_high")
        if self.p_value is None:
            if self.ci95_low is not None:
                raise ValueError("a confidence interval requires a p_value")
            if self.q_value_by is not None:
                raise ValueError("a q_value_by requires a p_value")
            if self.significant_by is not None:
                raise ValueError("a null p_value requires a null significance flag")
            if self.hac_sensitivity:
                raise ValueError("sensitivity requires a non-null p_value")
        else:
            if self.ci95_low is None:
                raise ValueError("a p_value requires a confidence interval")
            if self.q_value_by is None:
                raise ValueError("a p_value requires a BY-adjusted q_value")
            if self.significant_by is None:
                raise ValueError("a p_value requires a significance flag")
            if self.significant_by != (self.q_value_by <= FDR_Q):
                raise ValueError("significant_by must match q_value_by <= 0.10")
            if sorted(item.lags for item in self.hac_sensitivity) != list(
                HAC_SENSITIVITY_LAGS
            ):
                raise ValueError("hac_sensitivity must report lags 1, 3 and 5")
        return self


class RegionalAnnualValue(BaseModel):
    """One selected-period year of a cell.

    A null value is a missing/incomplete product year, never an imputation.
    ``valid_months`` counts the calendar months that passed the product's
    validity rule (0-12); a complete year has 12.
    """

    year: int = Field(ge=REGIONAL_YEAR_MIN, le=REGIONAL_YEAR_MAX)
    value: float | None
    valid_months: int = Field(ge=0, le=12)

    @field_validator("value")
    @classmethod
    def _finite_value(cls, value: float | None) -> float | None:
        return _finite_or_null(value, "annual value")

    @model_validator(mode="after")
    def _complete_year_has_all_months(self) -> "RegionalAnnualValue":
        if self.value is not None and self.valid_months != 12:
            raise ValueError("a non-null annual value requires 12 valid months")
        return self


class RegionalCellProperties(BaseModel):
    """Properties of one regional grid cell feature."""

    cell_id: str = Field(min_length=1)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    elevation_m: float | None = None
    quality: Quality
    valid_years: int = Field(ge=0)
    expected_years: int = Field(ge=1)
    max_gap_years: int = Field(ge=0)
    annual: list[RegionalAnnualValue]
    trend: RegionalTrend | None

    @field_validator("latitude", "longitude", "elevation_m")
    @classmethod
    def _finite_coordinate(cls, value: float | None) -> float | None:
        return _finite_or_null(value, "cell coordinate")

    @field_validator("annual")
    @classmethod
    def _ordered_years(
        cls, annual: list[RegionalAnnualValue]
    ) -> list[RegionalAnnualValue]:
        years = [item.year for item in annual]
        if years != sorted(set(years)):
            raise ValueError("annual must list distinct years in ascending order")
        return annual

    @model_validator(mode="after")
    def _coherent_cell(self) -> "RegionalCellProperties":
        if len(self.annual) != self.expected_years:
            raise ValueError("annual must contain every requested year")
        if self.valid_years != sum(
            1 for item in self.annual if item.value is not None
        ):
            raise ValueError("valid_years must equal the non-null annual values")
        if self.max_gap_years > self.expected_years:
            raise ValueError("max_gap_years must not exceed expected_years")

        if self.valid_years == self.expected_years:
            expected_quality: Quality = "complete"
        elif self.valid_years < MIN_TREND_YEARS:
            expected_quality = "insufficient_years"
        else:
            expected_quality = "incomplete"
        if self.quality != expected_quality:
            raise ValueError(f"quality must be {expected_quality!r}")

        if self.trend is None:
            if self.valid_years >= MIN_TREND_YEARS:
                raise ValueError(
                    "a descriptive trend is required when at least "
                    f"{MIN_TREND_YEARS} complete years are available"
                )
        return self


class RegionalGeometry(BaseModel):
    """Display rectangle for one cell: one closed exterior ring in lon/lat."""

    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[list[float]]] = Field(min_length=1)

    @model_validator(mode="after")
    def _closed_ring(self) -> "RegionalGeometry":
        for ring in self.coordinates:
            if len(ring) < 4:
                raise ValueError("a polygon ring needs at least four positions")
            if ring[0] != ring[-1]:
                raise ValueError("a polygon ring must be closed")
            for position in ring:
                if len(position) != 2:
                    raise ValueError("a position must be [longitude, latitude]")
                longitude, latitude = position
                if not math.isfinite(longitude) or not math.isfinite(latitude):
                    raise ValueError("position coordinates must be finite")
                if not -180.0 <= longitude <= 180.0:
                    raise ValueError("position longitude out of range")
                if not -90.0 <= latitude <= 90.0:
                    raise ValueError("position latitude out of range")
        return self


class RegionalCellFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: str = Field(min_length=1)
    geometry: RegionalGeometry
    properties: RegionalCellProperties

    @model_validator(mode="after")
    def _matching_id(self) -> "RegionalCellFeature":
        if self.id != self.properties.cell_id:
            raise ValueError("feature id must equal properties.cell_id")
        return self


class RegionalInference(BaseModel):
    """Inference contract for the whole response.

    One family is all tested cells of one variable, period and region, so the
    BY adjustment depends only on this response.
    """

    method: str = Field(min_length=1)
    hac_lags: Literal[3] = 3
    fdr: Literal["BY"] = "BY"
    fdr_q: float = Field(gt=0.0, le=1.0)
    tested_cells: int = Field(ge=0)

    @field_validator("fdr_q")
    @classmethod
    def _finite_q(cls, value: float) -> float:
        return _require_finite(value, "fdr_q")


class RegionalSummary(BaseModel):
    total_cells: int = Field(ge=0, le=REGIONAL_MAX_CELLS)
    trend_cells: int = Field(ge=0, le=REGIONAL_MAX_CELLS)
    fdr_cells: int = Field(ge=0, le=REGIONAL_MAX_CELLS)

    @model_validator(mode="after")
    def _ordered_counts(self) -> "RegionalSummary":
        if self.fdr_cells > self.trend_cells or self.trend_cells > self.total_cells:
            raise ValueError("fdr_cells <= trend_cells <= total_cells must hold")
        return self


class RegionalMetadata(BaseModel):
    region_id: str = Field(min_length=1)
    region_label: str = Field(min_length=1)
    bounds: RegionalBounds
    variable: Variable
    parameter: Literal["T2M", "PRECTOTCORR"]
    unit: Literal["°C", "mm"]
    aggregation: RegionalAggregation
    start_year: int = Field(ge=REGIONAL_YEAR_MIN, le=REGIONAL_YEAR_MAX)
    end_year: int = Field(ge=REGIONAL_YEAR_MIN, le=REGIONAL_YEAR_MAX)
    resolution: RegionalResolution
    source: RegionalSourceInfo
    inference: RegionalInference
    summary: RegionalSummary

    @model_validator(mode="after")
    def _coherent_metadata(self) -> "RegionalMetadata":
        if self.start_year > self.end_year:
            raise ValueError("start_year must not exceed end_year")

        expected = {
            "temperature": ("T2M", "°C", "annual_mean"),
            "precipitation": (
                "PRECTOTCORR",
                "mm",
                "sum_monthly_daily_rate_times_days",
            ),
        }[self.variable]
        if (self.parameter, self.unit, self.aggregation) != expected:
            raise ValueError(
                "parameter, unit and aggregation must match the variable"
            )
        if not math.isclose(self.inference.fdr_q, FDR_Q, abs_tol=1e-12):
            raise ValueError(f"inference.fdr_q must be {FDR_Q}")
        return self


class RegionalMapResponse(BaseModel):
    """Response of ``GET /api/maps/trends``: one bounded GeoJSON layer."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    metadata: RegionalMetadata
    features: list[RegionalCellFeature] = Field(
        default_factory=list, max_length=REGIONAL_MAX_CELLS
    )

    @model_validator(mode="after")
    def _coherent_collection(self) -> "RegionalMapResponse":
        expected_years = list(
            range(self.metadata.start_year, self.metadata.end_year + 1)
        )
        cell_ids: set[str] = set()
        trend_cells = 0
        fdr_cells = 0
        for feature in self.features:
            properties = feature.properties
            if properties.cell_id in cell_ids:
                raise ValueError("cell_id values must be unique")
            cell_ids.add(properties.cell_id)
            if properties.expected_years != len(expected_years):
                raise ValueError(
                    "every cell must cover the requested period exactly"
                )
            if [item.year for item in properties.annual] != expected_years:
                raise ValueError(
                    "every cell annual series must list the requested years"
                )
            if properties.trend is not None:
                trend_cells += 1
                if properties.trend.significant_by is True:
                    fdr_cells += 1

        summary = self.metadata.summary
        if summary.total_cells != len(self.features):
            raise ValueError("summary.total_cells must equal len(features)")
        if summary.trend_cells != trend_cells:
            raise ValueError("summary.trend_cells must equal fitted features")
        if summary.fdr_cells != fdr_cells:
            raise ValueError("summary.fdr_cells must equal BY-flagged features")
        tested = sum(
            1
            for feature in self.features
            if feature.properties.trend is not None
            and feature.properties.trend.p_value is not None
        )
        if self.metadata.inference.tested_cells != tested:
            raise ValueError(
                "inference.tested_cells must equal cells with a p_value"
            )
        return self


class RegionalVariableSpec(BaseModel):
    variable: Variable
    parameter: Literal["T2M", "PRECTOTCORR"]
    unit: Literal["°C", "mm"]
    aggregation: RegionalAggregation

    @model_validator(mode="after")
    def _coherent_variable(self) -> "RegionalVariableSpec":
        expected = {
            "temperature": ("T2M", "°C", "annual_mean"),
            "precipitation": (
                "PRECTOTCORR",
                "mm",
                "sum_monthly_daily_rate_times_days",
            ),
        }[self.variable]
        if (self.parameter, self.unit, self.aggregation) != expected:
            raise ValueError("parameter, unit and aggregation must match variable")
        return self


class RegionalRegionInfo(BaseModel):
    """One available regional layer; no data is loaded to list it."""

    region_id: str = Field(min_length=1)
    region_label: str = Field(min_length=1)
    bounds: RegionalBounds
    start_year: int = Field(ge=REGIONAL_YEAR_MIN, le=REGIONAL_YEAR_MAX)
    end_year: int = Field(ge=REGIONAL_YEAR_MIN, le=REGIONAL_YEAR_MAX)
    max_cells: int = Field(gt=0, le=REGIONAL_MAX_CELLS)
    variables: list[RegionalVariableSpec] = Field(min_length=1)


class RegionalRegionsResponse(BaseModel):
    """Response of ``GET /api/maps/regions``."""

    regions: list[RegionalRegionInfo] = Field(min_length=1)
