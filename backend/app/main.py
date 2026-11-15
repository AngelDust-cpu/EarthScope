"""FastAPI entry point for the EarthScope API.

Run from the repository root with::

    .\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --app-dir backend --reload

The Vite development origins (localhost/127.0.0.1, any port) are always
allowed.  For a static deployment, add explicit browser origins with
``EARTHSCOPE_ALLOWED_ORIGINS`` (comma- or whitespace-separated, for example
``EARTHSCOPE_ALLOWED_ORIGINS=https://earthscope.example``).  Invalid origin
configuration fails at startup rather than silently disabling CORS.
"""

from __future__ import annotations

import logging
import math
import os
import re
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
import json
from typing import Literal
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.concurrency import run_in_threadpool

from . import analysis, climate, extremes, heatwaves, regional_map, profile
from .power import PowerError, load_daily
from .regional_schemas import RegionalMapResponse, RegionalRegionsResponse
from . import global_map
from .schemas import (
    ALL_PERIOD,
    BASELINE_PRESET_END_YEAR,
    BASELINE_PRESET_START_YEAR,
    COVERAGE_MAX_YEAR,
    COVERAGE_MIN_YEAR,
    EXTREME_EVENTS,
    EXTREME_THRESHOLD_BOUNDS,
    MIN_TREND_YEARS,
    SEASONAL_PERIODS,
    AnomalyResponse,
    CompareRequest,
    CompareResponse,
    CoverageResponse,
    ErrorResponse,
    ExtremesResponse,
    HealthResponse,
    SeasonalResponse,
    TrendResponse,
)

logger = logging.getLogger("earthscope.api")

DEV_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
DEV_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
ORIGIN_ENV_VARS = ("EARTHSCOPE_ALLOWED_ORIGINS", "EARTHSCOPE_CORS_ORIGINS")

_VALID_VARIABLES = ("temperature", "precipitation")
_HTTP_ERROR_CODES = {
    400: "INVALID_REQUEST",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    422: "INVALID_REQUEST",
}


class ApiError(Exception):
    """An error that is already expressed in the public error envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_response(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


def _origin_setting() -> str:
    return ",".join(
        value for value in (os.getenv(name) for name in ORIGIN_ENV_VARS) if value
    )


def _parse_origins(raw: str) -> list[str]:
    """Parse and validate configured browser origins (not paths)."""

    origins: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[\s,]+", raw.strip()):
        candidate = item.strip().rstrip("/")
        if not candidate:
            continue
        if candidate == "*":
            return ["*"]

        parsed = urlsplit(candidate)
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.netloc
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
            or parsed.hostname is None
            or "*" in parsed.hostname
        ):
            raise RuntimeError(
                f"Invalid CORS origin {item!r}; use scheme://host[:port] "
                "entries in EARTHSCOPE_ALLOWED_ORIGINS."
            )
        try:
            port = parsed.port
        except ValueError as exc:
            raise RuntimeError(f"Invalid CORS origin {item!r}: bad port") from exc

        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        if port is None or (scheme == "https" and port == 443) or (
            scheme == "http" and port == 80
        ):
            normalised = f"{scheme}://{host}"
        else:
            normalised = f"{scheme}://{host}:{port}"
        if normalised not in seen:
            seen.add(normalised)
            origins.append(normalised)
    return origins


def _validation_message(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors()[:4]:
        location = [
            str(part)
            for part in error.get("loc", ())
            if part not in ("query", "body", "path")
        ]
        field = ".".join(location) or "request"
        message = str(error.get("msg") or "invalid value")
        parts.append(f"{field}: {message}")
    return "; ".join(parts) if parts else "Request parameters are invalid."


def _validate_coordinate(name: str, value: float, minimum: float, maximum: float) -> None:
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"{name} must be a finite number between {minimum:g} and {maximum:g}.",
        )


def _validate_years(start_year: int, end_year: int) -> None:
    if start_year < COVERAGE_MIN_YEAR or start_year > COVERAGE_MAX_YEAR:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"start_year must be between {COVERAGE_MIN_YEAR} and {COVERAGE_MAX_YEAR}.",
        )
    if end_year < COVERAGE_MIN_YEAR or end_year > COVERAGE_MAX_YEAR:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"end_year must be between {COVERAGE_MIN_YEAR} and {COVERAGE_MAX_YEAR}.",
        )
    if start_year > end_year:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "start_year must be less than or equal to end_year.",
        )


def _validate_variable(variable: str) -> None:
    if variable not in _VALID_VARIABLES:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "variable must be either 'temperature' or 'precipitation'.",
        )


def _validate_regional_years(start_year: int, end_year: int) -> None:
    """Regional maps use the fixed 1990-2025 monthly/annual product span."""

    minimum = regional_map.REGIONAL_YEAR_MIN
    maximum = regional_map.REGIONAL_YEAR_MAX
    if start_year < minimum or start_year > maximum:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"start_year must be between {minimum} and {maximum} for regional maps.",
        )
    if end_year < minimum or end_year > maximum:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"end_year must be between {minimum} and {maximum} for regional maps.",
        )
    if start_year > end_year:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "start_year must be less than or equal to end_year.",
        )


def _validate_baseline_years(baseline_start_year: int, baseline_end_year: int) -> None:
    if (
        baseline_start_year < COVERAGE_MIN_YEAR
        or baseline_start_year > COVERAGE_MAX_YEAR
    ):
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "baseline_start_year must be between "
            f"{COVERAGE_MIN_YEAR} and {COVERAGE_MAX_YEAR}.",
        )
    if baseline_end_year < COVERAGE_MIN_YEAR or baseline_end_year > COVERAGE_MAX_YEAR:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "baseline_end_year must be between "
            f"{COVERAGE_MIN_YEAR} and {COVERAGE_MAX_YEAR}.",
        )
    if baseline_start_year > baseline_end_year:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "baseline_start_year must be less than or equal to baseline_end_year.",
        )


def _validate_period(period: str) -> None:
    if period not in SEASONAL_PERIODS:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "period must be one of "
            + ", ".join(SEASONAL_PERIODS)
            + ".",
        )


def _validate_event(event: str) -> None:
    if event not in EXTREME_EVENTS:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "event must be one of " + ", ".join(EXTREME_EVENTS) + ".",
        )


def _validate_threshold(event: str, threshold: float) -> None:
    if not math.isfinite(threshold):
        raise ApiError(
            422,
            "INVALID_REQUEST",
            "threshold must be a finite number.",
        )
    minimum, maximum = EXTREME_THRESHOLD_BOUNDS[event]
    if threshold < minimum or threshold > maximum:
        raise ApiError(
            422,
            "INVALID_REQUEST",
            f"threshold for {event} must be between {minimum:g} and {maximum:g}.",
        )


def _power_error_status(exc: PowerError) -> int:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if isinstance(status, int) and status in (422, 502, 503):
        return status
    if getattr(exc, "code", None) == "INVALID_REQUEST":
        return 422
    if getattr(exc, "retryable", False) is True:
        return 503
    return 502


def _power_error_code(status_code: int) -> str:
    return "INVALID_REQUEST" if status_code == 422 else "POWER_UNAVAILABLE"


def _power_error_message(exc: PowerError) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = "NASA POWER data is currently unavailable."
    return text[:500]


def _load_point(
    *,
    latitude: float,
    longitude: float,
    variable: str,
    start_year: int,
    end_year: int,
) -> tuple[dict[str, float], dict]:
    """Load one point, mapping upstream failures onto the public error envelope."""

    try:
        return load_daily(
            latitude=latitude,
            longitude=longitude,
            variable=variable,
            start_year=start_year,
            end_year=end_year,
        )
    except PowerError as exc:
        message = _power_error_message(exc)
        status_code = _power_error_status(exc)
        logger.warning("NASA POWER request failed (%s): %s", status_code, message)
        raise ApiError(status_code, _power_error_code(status_code), message) from exc


def _data_contract_error(exc: analysis.DataContractError) -> ApiError:
    message = " ".join(str(exc).split())[:500]
    logger.warning("NASA POWER data failed validation: %s", message)
    return ApiError(
        502,
        "POWER_UNAVAILABLE",
        f"NASA POWER data failed validation: {message}",
    )


def _regional_contract_error(exc: analysis.DataContractError) -> ApiError:
    message = " ".join(str(exc).split())[:500]
    logger.warning("NASA POWER regional data failed validation: %s", message)
    return ApiError(
        502,
        "POWER_UNAVAILABLE",
        f"NASA POWER regional data failed validation: {message}",
    )


def _register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(422, "INVALID_REQUEST", _validation_message(exc))

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        if isinstance(exc.detail, dict):
            error = exc.detail.get("error")
            if (
                isinstance(error, dict)
                and isinstance(error.get("code"), str)
                and isinstance(error.get("message"), str)
            ):
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"error": error},
                    headers=exc.headers,
                )
        code = _HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _error_response(exc.status_code, code, message, headers=exc.headers)

    @application.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled error for %s %s",
            request.method,
            request.url.path,
            exc_info=exc,
        )
        return _error_response(
            500,
            "INTERNAL_ERROR",
            "An unexpected server error occurred. Please try again.",
        )


def _register_routes(application: FastAPI) -> None:
    @application.get("/api/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @application.get(
        "/api/coverage", response_model=CoverageResponse, tags=["system"]
    )
    def coverage() -> CoverageResponse:
        return CoverageResponse(
            min_year=COVERAGE_MIN_YEAR,
            max_year=COVERAGE_MAX_YEAR,
            min_years=MIN_TREND_YEARS,
        )

    @application.get(
        "/api/trend",
        response_model=TrendResponse,
        responses={
            422: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["analysis"],
    )
    def trend(
        latitude: float = Query(
            ..., description="WGS84 latitude in decimal degrees (-90 to 90)."
        ),
        longitude: float = Query(
            ..., description="WGS84 longitude in decimal degrees (-180 to 180)."
        ),
        variable: str = Query(
            ..., description="Either 'temperature' or 'precipitation'."
        ),
        start_year: int = Query(..., description="First inclusive UTC calendar year."),
        end_year: int = Query(..., description="Last inclusive UTC calendar year."),
    ) -> TrendResponse:
        _validate_coordinate("latitude", latitude, -90.0, 90.0)
        _validate_coordinate("longitude", longitude, -180.0, 180.0)
        _validate_variable(variable)
        _validate_years(start_year, end_year)

        daily, source = _load_point(
            latitude=latitude,
            longitude=longitude,
            variable=variable,
            start_year=start_year,
            end_year=end_year,
        )

        try:
            return analysis.build_trend_response(
                daily=daily,
                source=source,
                latitude=latitude,
                longitude=longitude,
                variable=variable,  # type: ignore[arg-type]
                start_year=start_year,
                end_year=end_year,
            )
        except analysis.DataContractError as exc:
            raise _data_contract_error(exc) from exc

    @application.post(
        "/api/compare",
        response_model=CompareResponse,
        responses={
            422: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["analysis"],
    )
    def compare(payload: CompareRequest) -> CompareResponse:
        """Compare one variable over one year range at two independent points.

        Each point is loaded separately through the existing POWER cache, so a
        failure of either fetch aborts the whole comparison: the API never
        returns a partial pair or a substituted value.
        """

        point_a = payload.point_a
        point_b = payload.point_b

        daily_a, source_a = _load_point(
            latitude=point_a.latitude,
            longitude=point_a.longitude,
            variable=payload.variable,
            start_year=payload.start_year,
            end_year=payload.end_year,
        )
        daily_b, source_b = _load_point(
            latitude=point_b.latitude,
            longitude=point_b.longitude,
            variable=payload.variable,
            start_year=payload.start_year,
            end_year=payload.end_year,
        )

        try:
            return analysis.build_compare_response(
                daily_a=daily_a,
                source_a=source_a,
                latitude_a=point_a.latitude,
                longitude_a=point_a.longitude,
                daily_b=daily_b,
                source_b=source_b,
                latitude_b=point_b.latitude,
                longitude_b=point_b.longitude,
                variable=payload.variable,
                start_year=payload.start_year,
                end_year=payload.end_year,
            )
        except analysis.DataContractError as exc:
            raise _data_contract_error(exc) from exc

    @application.get(
        "/api/anomalies",
        response_model=AnomalyResponse,
        responses={
            422: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["analysis"],
    )
    def anomalies(
        latitude: float = Query(
            ..., description="WGS84 latitude in decimal degrees (-90 to 90)."
        ),
        longitude: float = Query(
            ..., description="WGS84 longitude in decimal degrees (-180 to 180)."
        ),
        variable: str = Query(
            ..., description="Either 'temperature' or 'precipitation'."
        ),
        start_year: int = Query(..., description="First inclusive UTC calendar year."),
        end_year: int = Query(..., description="Last inclusive UTC calendar year."),
        baseline_start_year: int = Query(
            BASELINE_PRESET_START_YEAR,
            description=(
                "First inclusive baseline year; defaults to the 1991-2020 "
                "30-year preset."
            ),
        ),
        baseline_end_year: int = Query(
            BASELINE_PRESET_END_YEAR,
            description=(
                "Last inclusive baseline year; defaults to the 1991-2020 "
                "30-year preset."
            ),
        ),
    ) -> AnomalyResponse:
        """Annual values with anomalies against an equally weighted baseline.

        Every input is validated before the single POWER request, which covers
        the union of the display and baseline spans so one cache entry serves
        both.  The baseline may lie outside the display period.
        """

        _validate_coordinate("latitude", latitude, -90.0, 90.0)
        _validate_coordinate("longitude", longitude, -180.0, 180.0)
        _validate_variable(variable)
        _validate_years(start_year, end_year)
        _validate_baseline_years(baseline_start_year, baseline_end_year)

        fetch_start_year, fetch_end_year = climate.anomaly_fetch_span(
            start_year,
            end_year,
            baseline_start_year,
            baseline_end_year,
        )
        daily, source = _load_point(
            latitude=latitude,
            longitude=longitude,
            variable=variable,
            start_year=fetch_start_year,
            end_year=fetch_end_year,
        )

        try:
            return climate.build_anomaly_response(
                daily=daily,
                source=source,
                latitude=latitude,
                longitude=longitude,
                variable=variable,
                start_year=start_year,
                end_year=end_year,
                baseline_start_year=baseline_start_year,
                baseline_end_year=baseline_end_year,
            )
        except analysis.DataContractError as exc:
            raise _data_contract_error(exc) from exc

    @application.get(
        "/api/seasonal",
        response_model=SeasonalResponse,
        responses={
            422: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["analysis"],
    )
    def seasonal(
        latitude: float = Query(
            ..., description="WGS84 latitude in decimal degrees (-90 to 90)."
        ),
        longitude: float = Query(
            ..., description="WGS84 longitude in decimal degrees (-180 to 180)."
        ),
        variable: str = Query(
            ..., description="Either 'temperature' or 'precipitation'."
        ),
        start_year: int = Query(..., description="First inclusive UTC calendar year."),
        end_year: int = Query(..., description="Last inclusive UTC calendar year."),
        period: str = Query(
            ALL_PERIOD,
            description=(
                "winter|spring|summer|autumn|all|m01..m12. 'all' returns the "
                "four seasons in calendar order."
            ),
        ),
    ) -> SeasonalResponse:
        """Seasonal or monthly aggregates and OLS trends for one point.

        A winter request reaches one year back for the previous December, so the
        whole response still comes from a single daily POWER range.  Winter 1981
        has no prior December inside coverage and stays null.
        """

        _validate_coordinate("latitude", latitude, -90.0, 90.0)
        _validate_coordinate("longitude", longitude, -180.0, 180.0)
        _validate_variable(variable)
        _validate_years(start_year, end_year)
        _validate_period(period)

        fetch_start_year, fetch_end_year = climate.seasonal_fetch_span(
            start_year,
            end_year,
            period,
        )
        daily, source = _load_point(
            latitude=latitude,
            longitude=longitude,
            variable=variable,
            start_year=fetch_start_year,
            end_year=fetch_end_year,
        )

        try:
            return climate.build_seasonal_response(
                daily=daily,
                source=source,
                latitude=latitude,
                longitude=longitude,
                variable=variable,
                start_year=start_year,
                end_year=end_year,
                period=period,
            )
        except analysis.DataContractError as exc:
            raise _data_contract_error(exc) from exc

    @application.get(
        "/api/extremes",
        response_model=ExtremesResponse,
        responses={
            422: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["analysis"],
    )
    def extreme_events(
        latitude: float = Query(
            ..., description="WGS84 latitude in decimal degrees (-90 to 90)."
        ),
        longitude: float = Query(
            ..., description="WGS84 longitude in decimal degrees (-180 to 180)."
        ),
        start_year: int = Query(..., description="First inclusive UTC calendar year."),
        end_year: int = Query(..., description="Last inclusive UTC calendar year."),
        event: str = Query(
            ...,
            description=(
                "hot_days (T2M_MAX > threshold), cold_nights (T2M_MIN < "
                "threshold) or heavy_rain (PRECTOTCORR > threshold)."
            ),
        ),
        threshold: float = Query(
            ...,
            description=(
                "Strict comparison threshold: °C for hot_days/cold_nights "
                "and mm/day for heavy_rain. Equality is not counted."
            ),
        ),
    ) -> ExtremesResponse:
        """Strict threshold-exceedance days per complete UTC calendar year.

        The request reuses the shared ``load_daily`` ingestion and exact-query
        cache through ``_load_point``.  A year contributes an event count only
        when every one of its 365/366 UTC days is present; incomplete years
        keep diagnostics but report null events and no fitted value.
        """

        _validate_coordinate("latitude", latitude, -90.0, 90.0)
        _validate_coordinate("longitude", longitude, -180.0, 180.0)
        _validate_years(start_year, end_year)
        _validate_event(event)
        _validate_threshold(event, threshold)

        daily, source = _load_point(
            latitude=latitude,
            longitude=longitude,
            variable=extremes.power_variable(event),
            start_year=start_year,
            end_year=end_year,
        )

        try:
            return extremes.build_extremes_response(
                daily=daily,
                source=source,
                latitude=latitude,
                longitude=longitude,
                event=event,
                threshold=threshold,
                start_year=start_year,
                end_year=end_year,
            )
        except analysis.DataContractError as exc:
            raise _data_contract_error(exc) from exc

    @application.get(
        "/api/heatwaves",
        response_model=heatwaves.HeatwaveResponse,
        responses={
            422: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["analysis"],
    )
    def heatwave_analysis(
        latitude: float = Query(
            ..., description="WGS84 latitude in decimal degrees (-90 to 90)."
        ),
        longitude: float = Query(
            ..., description="WGS84 longitude in decimal degrees (-180 to 180)."
        ),
        start_year: int = Query(..., description="First inclusive UTC calendar year."),
        end_year: int = Query(..., description="Last inclusive UTC calendar year."),
        threshold: float = Query(
            heatwaves.DEFAULT_THRESHOLD,
            description=(
                "Strict daily T2M_MAX threshold in °C. A day exactly at the "
                "threshold is not a heatwave day."
            ),
        ),
        min_duration: int = Query(
            heatwaves.DEFAULT_MIN_DURATION,
            ge=heatwaves.MIN_DURATION_MIN,
            le=heatwaves.MIN_DURATION_MAX,
            description="Minimum consecutive exceedance days for one episode.",
        ),
    ) -> heatwaves.HeatwaveResponse:
        """Heatwave episodes and descriptive annual trends for one point.

        Detection reuses the shared ``load_daily`` ingestion for T2M_MAX
        through ``_load_point``, so a request is one POWER range and one cache
        entry.  Runs of strictly exceedance days are followed across Dec 31 and
        attributed entirely to their start year; outer-window censoring and
        incomplete years are disclosed in ``warnings`` instead of being
        silently dropped or filled.
        """

        _validate_coordinate("latitude", latitude, -90.0, 90.0)
        _validate_coordinate("longitude", longitude, -180.0, 180.0)
        _validate_years(start_year, end_year)
        _validate_threshold("hot_days", threshold)

        daily, source = _load_point(
            latitude=latitude,
            longitude=longitude,
            variable=heatwaves.POWER_VARIABLE,
            start_year=start_year,
            end_year=end_year,
        )

        try:
            return heatwaves.build_heatwave_response(
                daily=daily,
                source=source,
                latitude=latitude,
                longitude=longitude,
                start_year=start_year,
                end_year=end_year,
                threshold=threshold,
                min_duration=min_duration,
            )
        except analysis.DataContractError as exc:
            raise _data_contract_error(exc) from exc

    def world_data(load):
        try:
            return load()
        except FileNotFoundError as exc:
            raise ApiError(503, 'GLOBAL_ARCHIVE_UNAVAILABLE', 'The NASA POWER world archive has not been prepared.') from exc

    @application.get('/api/maps/world', tags=['maps'])
    def world_map(variable: Literal['temperature', 'precipitation'] = 'temperature'):
        *_, limit, body, tested, version = world_data(lambda: global_map.get_global(variable))
        return Response(body, media_type='application/octet-stream', headers={
            'X-EarthScope-Scale': str(limit), 'X-EarthScope-Tested': str(tested),
            'X-EarthScope-Power-Version': version,
        })

    @application.get('/api/maps/world/cell', tags=['maps'])
    def world_cell(
        row: int = Query(ge=0, le=360), col: int = Query(ge=0, le=575),
        variable: Literal['temperature', 'precipitation'] = 'temperature',
    ):
        return world_data(lambda: global_map.cell_result(variable, row, col))

    @application.get(
        "/api/maps/regions",
        response_model=RegionalRegionsResponse,
        tags=["maps"],
    )
    def map_regions() -> RegionalRegionsResponse:
        """Describe the available demonstration region and variable contract.

        This is a static capability document: no NASA request is made and no
        disk cache is read, so it is safe for the map page to call on mount.
        """

        return regional_map.list_regions()

    @application.get(
        "/api/maps/trends",
        response_model=RegionalMapResponse,
        responses={
            422: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
        tags=["maps"],
    )
    def regional_trends(
        region: str = Query(
            regional_map.DEFAULT_REGION_ID,
            description="Regional layer ID; see GET /api/maps/regions.",
        ),
        variable: str = Query(
            regional_map.DEFAULT_VARIABLE,
            description="Either 'temperature' (T2M) or 'precipitation'.",
        ),
        start_year: int = Query(
            regional_map.REGIONAL_YEAR_MIN,
            description="First inclusive year inside the fixed 1990-2025 span.",
        ),
        end_year: int = Query(
            regional_map.REGIONAL_YEAR_MAX,
            description="Last inclusive year inside the fixed 1990-2025 span.",
        ),
    ) -> RegionalMapResponse:
        """Return one bounded regional GeoJSON layer with annual cell series.

        One call loads (or reuses, via the Data-owned processed cache) one
        parameter for the whole region and returns every yearly value the map
        needs.  Panning or selecting a cell never calls this route again and
        there is no per-cell endpoint, so no viewer interaction reaches NASA.
        """

        spec = regional_map.REGIONS_BY_ID.get(region)
        if spec is None:
            available = ", ".join(sorted(regional_map.REGIONS_BY_ID))
            raise ApiError(
                404,
                "NOT_FOUND",
                f"Unknown region {region!r}; available regions: {available}.",
            )
        _validate_variable(variable)
        _validate_regional_years(start_year, end_year)

        try:
            return regional_map.build_regional_map(
                region_id=spec.region_id,
                variable=variable,
                start_year=start_year,
                end_year=end_year,
            )
        except regional_map.RegionalDataUnavailable as exc:
            logger.warning("Regional map layer is unavailable: %s", exc)
            raise ApiError(
                503,
                "POWER_UNAVAILABLE",
                "Regional map data is not configured on this server.",
            ) from exc
        except PowerError as exc:
            message = _power_error_message(exc)
            status_code = _power_error_status(exc)
            logger.warning(
                "NASA POWER regional request failed (%s): %s", status_code, message
            )
            raise ApiError(
                status_code, _power_error_code(status_code), message
            ) from exc
        except analysis.DataContractError as exc:
            raise _regional_contract_error(exc) from exc


class ResearchReportRoute(APIRoute):
    """Render only validated responses from the existing analysis endpoints."""

    def get_route_handler(self):
        handle = super().get_route_handler()
        kind = {
            "/api/trend": "trend",
            "/api/compare": "compare",
            "/api/anomalies": "anomalies",
            "/api/seasonal": "seasonal",
            "/api/extremes": "extremes",
            "/api/heatwaves": "heatwaves",
            "/api/maps/world/cell": "world-cell",
        }.get(self.path)
        if kind is None:
            return handle

        async def with_report(request: Request) -> Response:
            response = await handle(request)
            response.headers.append("Vary", "Accept")
            if request.headers.get("accept") != "application/pdf" or response.status_code != 200:
                return response
            from .report_pdf import render_report

            # Valid heatwave queries may contain one episode every other day.
            limit = 2_000_000 if kind == "heatwaves" else 256_000
            if len(response.body) > limit:
                raise ApiError(422, "REPORT_TOO_LARGE", "Результат слишком велик для PDF.")
            payload = json.loads(response.body)
            if request.headers.get("x-earthscope-report") == "detective":
                payload["_detective"] = True
            pdf = await run_in_threadpool(render_report, kind, payload)
            return Response(pdf, media_type="application/pdf", headers={
                "Content-Disposition": f'attachment; filename="EarthScope-{kind}.pdf"',
                "Cache-Control": "no-store",
                "Vary": "Accept, X-EarthScope-Report",
            })

        return with_report


def create_app() -> FastAPI:
    """Create the API application, including CORS and error envelopes."""

    configured_origins = _parse_origins(_origin_setting())
    wildcard = "*" in configured_origins
    allow_origins = ["*"] if wildcard else list(DEV_ORIGINS) + configured_origins

    application = FastAPI(
        title="EarthScope API",
        version="0.1.0",
        description=(
            "Annual NASA POWER temperature and precipitation trends at one "
            "WGS84 coordinate, anomaly baselines, seasonal and monthly "
            "aggregates, strict extreme-event day counts, a descriptive "
            "two-point comparison, a bounded regional climate trend map, and "
            "threshold-based heatwave episodes with descriptive trends."
        ),
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_origin_regex=None if wildcard else DEV_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=['X-EarthScope-Scale', 'X-EarthScope-Tested', 'X-EarthScope-Power-Version', 'Content-Disposition'],
        max_age=600,
    )
    _register_exception_handlers(application)
    application.router.route_class = ResearchReportRoute
    _register_routes(application)
    profile.register_routes(application)
    return application


app = create_app()
