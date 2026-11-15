"""Response DTOs for ``GET /api/climate-profile`` (EarthScope 2.8).

The profile adds no scientific model of its own: it composes the existing,
already validated DTOs (``TrendResponse``, ``AnomalyResponse``,
``SeasonalResponse``, ``ExtremesResponse``) into eight sections.  These models
therefore only assemble those objects and enforce the cross-section invariants
of ``docs/CLIMATE-PROFILE-CONTRACT.md``:

* a section is ``ok`` exactly when it carries data and no message;
* ``error`` and ``unavailable`` sections carry no data and a non-empty message;
* ``heatwaves`` is always ``unavailable`` because this base has no heatwave
  module (documented dependency, never a substituted value);
* every ``ok`` section echoes the requested point, period, variable, baseline
  and threshold, so a section can never be wired to another point or threshold;
* a profile with no successful network section cannot be represented, because
  the route answers HTTP 503 with the existing error envelope instead.

Threshold defaults and bounds reuse ``app.schemas``: the profile does not
redefine the extreme-event contract.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from .schemas import (
    COVERAGE_MAX_YEAR,
    COVERAGE_MIN_YEAR,
    EXTREME_THRESHOLD_BOUNDS,
    AnomalyResponse,
    ExtremesResponse,
    SeasonalResponse,
    TrendResponse,
)

__all__ = [
    "DEFAULT_COLD_THRESHOLD",
    "DEFAULT_HOT_THRESHOLD",
    "DEFAULT_RAIN_THRESHOLD",
    "NETWORK_PROFILE_SECTIONS",
    "PROFILE_SECTIONS",
    "AnomalySection",
    "ClimateProfileResponse",
    "ExtremesSection",
    "HeatwaveSection",
    "ProfileSectionStatus",
    "ProfileSections",
    "ProfileThresholds",
    "SeasonalSection",
    "TrendSection",
]

#: Fixed absolute defaults exposed (and editable) by the UI.  The values are
#: research thresholds, not a calibrated risk classification.
DEFAULT_HOT_THRESHOLD = 30.0
DEFAULT_COLD_THRESHOLD = 0.0
DEFAULT_RAIN_THRESHOLD = 20.0

ProfileSectionStatus = Literal["ok", "error", "unavailable"]

#: Section keys in contract order.
PROFILE_SECTIONS: tuple[str, ...] = (
    "temperature",
    "precipitation",
    "anomalies",
    "seasonal",
    "hot_days",
    "cold_nights",
    "heavy_rain",
    "heatwaves",
)

#: Sections that depend on a NASA POWER daily request.  ``heatwaves`` is
#: deliberately excluded: it is unavailable without any network access.
NETWORK_PROFILE_SECTIONS: tuple[str, ...] = tuple(
    name for name in PROFILE_SECTIONS if name != "heatwaves"
)


def _check_section(
    status: str,
    data: Any,
    message: str | None,
    label: str,
) -> None:
    """Enforce the status/data/message coherence shared by every section."""

    if status == "ok":
        if data is None:
            raise ValueError(f"{label}: status 'ok' requires data")
        if message is not None:
            raise ValueError(f"{label}: status 'ok' must not carry a message")
        return
    if data is not None:
        raise ValueError(f"{label}: status {status!r} must not carry data")
    if message is None or not message.strip():
        raise ValueError(f"{label}: status {status!r} requires a message")


class _SectionBase(BaseModel):
    """Base section: ``status`` first so JSON keeps ``status, data, message``."""

    status: ProfileSectionStatus


class TrendSection(_SectionBase):
    """Annual trend for one variable (``TrendResponse``)."""

    data: TrendResponse | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "TrendSection":
        _check_section(self.status, self.data, self.message, "trend")
        return self


class AnomalySection(_SectionBase):
    """Temperature anomalies against the configured baseline."""

    data: AnomalyResponse | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "AnomalySection":
        _check_section(self.status, self.data, self.message, "anomalies")
        return self


class SeasonalSection(_SectionBase):
    """Temperature seasons (``period='all'``) with their own OLS fits."""

    data: SeasonalResponse | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "SeasonalSection":
        _check_section(self.status, self.data, self.message, "seasonal")
        return self


class ExtremesSection(_SectionBase):
    """One absolute-threshold extreme event (``ExtremesResponse``)."""

    data: ExtremesResponse | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "ExtremesSection":
        _check_section(self.status, self.data, self.message, "extremes")
        return self


class HeatwaveSection(_SectionBase):
    """Heatwaves have no module in this base, so this section has no data."""

    data: None = None
    message: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> "HeatwaveSection":
        _check_section(self.status, self.data, self.message, "heatwaves")
        if self.status != "unavailable":
            raise ValueError("heatwaves must be 'unavailable' in this version")
        return self


class ProfileThresholds(BaseModel):
    """Absolute thresholds echoed by the response (strict comparisons)."""

    hot_days: float = DEFAULT_HOT_THRESHOLD
    cold_nights: float = DEFAULT_COLD_THRESHOLD
    heavy_rain: float = DEFAULT_RAIN_THRESHOLD

    @field_validator("hot_days", "cold_nights", "heavy_rain")
    @classmethod
    def _bounded(cls, value: float, info: ValidationInfo) -> float:
        field = str(info.field_name)
        if not math.isfinite(value):
            raise ValueError(f"thresholds.{field} must be finite")
        minimum, maximum = EXTREME_THRESHOLD_BOUNDS[field]
        if value < minimum or value > maximum:
            raise ValueError(
                f"thresholds.{field} must be between {minimum:g} and {maximum:g}"
            )
        return value


class ProfileSections(BaseModel):
    """The eight sections of one profile, in contract order."""

    temperature: TrendSection
    precipitation: TrendSection
    anomalies: AnomalySection
    seasonal: SeasonalSection
    hot_days: ExtremesSection
    cold_nights: ExtremesSection
    heavy_rain: ExtremesSection
    heatwaves: HeatwaveSection

    NETWORK_FIELDS: ClassVar[tuple[str, ...]] = NETWORK_PROFILE_SECTIONS

    def network_sections(self) -> list[tuple[str, Any]]:
        """Return ``(name, section)`` pairs for the network-backed sections."""

        return [(name, getattr(self, name)) for name in NETWORK_PROFILE_SECTIONS]

    @property
    def ok_network_count(self) -> int:
        return sum(1 for _name, section in self.network_sections() if section.status == "ok")

    @property
    def all_network_failed(self) -> bool:
        return self.ok_network_count == 0


def _same_number(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-9)


class ClimateProfileResponse(BaseModel):
    """Response of ``GET /api/climate-profile`` (and the basis of its CSV/PDF)."""

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    start_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    end_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    baseline_start_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    baseline_end_year: int = Field(ge=COVERAGE_MIN_YEAR, le=COVERAGE_MAX_YEAR)
    thresholds: ProfileThresholds
    sections: ProfileSections
    summary: list[str]
    limitations: list[str]

    @field_validator("latitude", "longitude")
    @classmethod
    def _finite_coordinate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("coordinate must be finite")
        return value

    @field_validator("summary", "limitations")
    @classmethod
    def _non_empty_lines(cls, value: list[str]) -> list[str]:
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("summary and limitations entries must be non-empty")
        return value

    @model_validator(mode="after")
    def _coherent_profile(self) -> "ClimateProfileResponse":
        if self.start_year > self.end_year:
            raise ValueError("start_year must not exceed end_year")
        if self.baseline_start_year > self.baseline_end_year:
            raise ValueError(
                "baseline_start_year must not exceed baseline_end_year"
            )

        if self.sections.heatwaves.status != "unavailable":
            raise ValueError("the heatwaves section must be 'unavailable'")
        if self.sections.all_network_failed:
            raise ValueError(
                "a profile response requires at least one successful network section"
            )

        for name in ("temperature", "precipitation"):
            section = getattr(self.sections, name)
            data = section.data
            if section.status != "ok" or data is None:
                continue
            self._match_point(name, data)
            if data.variable != name:
                raise ValueError(f"{name} section data must use variable {name!r}")

        anomalies = self.sections.anomalies
        if anomalies.status == "ok" and anomalies.data is not None:
            data = anomalies.data
            self._match_point("anomalies", data)
            if data.variable != "temperature":
                raise ValueError("anomalies section data must use temperature")
            if (
                data.baseline_start_year != self.baseline_start_year
                or data.baseline_end_year != self.baseline_end_year
            ):
                raise ValueError(
                    "anomalies section data must use the requested baseline"
                )

        seasonal = self.sections.seasonal
        if seasonal.status == "ok" and seasonal.data is not None:
            data = seasonal.data
            self._match_point("seasonal", data)
            if data.variable != "temperature":
                raise ValueError("seasonal section data must use temperature")
            if data.period != "all":
                raise ValueError("seasonal section data must use period 'all'")

        for name in ("hot_days", "cold_nights", "heavy_rain"):
            section = getattr(self.sections, name)
            data = section.data if section.status == "ok" else None
            if data is None:
                continue
            self._match_point(name, data)
            if data.event != name:
                raise ValueError(f"{name} section data must report event {name!r}")
            if not _same_number(
                data.threshold, getattr(self.thresholds, name)
            ):
                raise ValueError(
                    f"{name} section data must use the requested threshold"
                )
        return self

    def _match_point(self, label: str, data: Any) -> None:
        if not (
            _same_number(data.latitude, self.latitude)
            and _same_number(data.longitude, self.longitude)
        ):
            raise ValueError(f"{label} section data must use the requested point")
        if data.start_year != self.start_year or data.end_year != self.end_year:
            raise ValueError(f"{label} section data must use the requested years")
