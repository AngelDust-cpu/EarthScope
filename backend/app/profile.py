"""Climate profile orchestration for EarthScope 2.8.

``GET /api/climate-profile`` composes existing analyses for one WGS84 point:

================  ===========================================================
``temperature``   ``analysis.build_trend_response`` on T2M
``precipitation`` ``analysis.build_trend_response`` on PRECTOTCORR
``anomalies``     ``climate.build_anomaly_response`` on T2M (explicit baseline)
``seasonal``      ``climate.build_seasonal_response`` on T2M (``period=all``)
``hot_days``      ``extremes.build_extremes_response`` on T2M_MAX
``cold_nights``   ``extremes.build_extremes_response`` on T2M_MIN
``heavy_rain``    ``extremes.build_extremes_response`` on PRECTOTCORR
``heatwaves``     ``unavailable``: no heatwave module exists in this base
================  ===========================================================

Budget and reuse
----------------
A profile performs **exactly four** :func:`app.power.load_daily` calls, one per
POWER parameter (T2M, T2M_MAX, T2M_MIN, PRECTOTCORR), sequentially.  All four
share one expanded span that covers the requested display years, the
configurable baseline and the December preceding ``start_year`` (needed by the
winter season inside ``period=all``).  The four daily mappings are then reused
by every annual, anomaly, seasonal and extreme section, so no section fetches
again and the exact-query cache in :mod:`app.power` sees one key per parameter.

Failure isolation
-----------------
Each section is built independently.  A ``PowerError`` (upstream) or
``DataContractError`` (contract violation) becomes that section's
``status="error"`` with a message while the response stays HTTP 200 and the
other sections remain intact - no value is imputed, filled or substituted.  If
every network-backed section fails, the route answers HTTP 503 with the
existing error envelope.

No scientific algorithm is implemented here: every number comes from the
existing services.  This module chooses spans, isolates failures and formats
the summary, limitations and CSV export.
"""

from __future__ import annotations

import csv
import io
import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response

from . import analysis, climate, extremes
from .power import PowerError, load_daily
from .report_format import wants_pdf
from .profile_schemas import (
    DEFAULT_COLD_THRESHOLD,
    DEFAULT_HOT_THRESHOLD,
    DEFAULT_RAIN_THRESHOLD,
    PROFILE_SECTIONS,
    AnomalySection,
    ClimateProfileResponse,
    ExtremesSection,
    HeatwaveSection,
    ProfileSections,
    ProfileThresholds,
    SeasonalSection,
    TrendSection,
)
from .schemas import (
    ALL_PERIOD,
    BASELINE_PRESET_END_YEAR,
    BASELINE_PRESET_START_YEAR,
    CLIMATE_NORMAL_YEARS,
    COVERAGE_MAX_YEAR,
    COVERAGE_MIN_YEAR,
    EXTREME_THRESHOLD_BOUNDS,
    MIN_TREND_YEARS,
    AnomalyResponse,
    ExtremesResponse,
    SeasonalResponse,
    SourceInfo,
    TrendResponse,
)

logger = logging.getLogger("earthscope.profile")

__all__ = [
    "CSV_COLUMNS",
    "PROFILE_CSV_PATH",
    "PROFILE_PATH",
    "PROFILE_VARIABLES",
    "ProfileQuery",
    "build_profile",
    "profile_fetch_span",
    "profile_query",
    "register_routes",
    "render_profile_csv",
]

PROFILE_PATH = "/api/climate-profile"
PROFILE_CSV_PATH = f"{PROFILE_PATH}.csv"

#: The four ``load_daily`` variables, in call order.  ``PRECTOTCORR`` serves
#: both the precipitation trend and the heavy-rain extremes; T2M serves the
#: temperature trend, the anomalies and the seasons.
PROFILE_VARIABLES: tuple[str, ...] = (
    "temperature",
    "temperature_max",
    "temperature_min",
    "precipitation",
)

_LABELS: dict[str, str] = {
    "temperature": "Температура (T2M)",
    "precipitation": "Осадки (PRECTOTCORR)",
    "anomalies": "Аномалии температуры",
    "seasonal": "Сезоны",
    "hot_days": "Жаркие дни",
    "cold_nights": "Холодные ночи",
    "heavy_rain": "Сильный дождь",
    "heatwaves": "Волны жары",
}

_SHORT_LABELS: dict[str, str] = {
    "temperature": "температура",
    "precipitation": "осадки",
    "anomalies": "аномалии",
    "seasonal": "сезоны",
    "hot_days": "жаркие дни",
    "cold_nights": "холодные ночи",
    "heavy_rain": "сильный дождь",
}

_SEASON_LABELS: dict[str, str] = {
    "winter": "зима",
    "spring": "весна",
    "summer": "лето",
    "autumn": "осень",
}

_EVENT_DEFINITIONS: dict[str, str] = {
    "hot_days": "T2M_MAX > {threshold:g} °C",
    "cold_nights": "T2M_MIN < {threshold:g} °C",
    "heavy_rain": "PRECTOTCORR > {threshold:g} мм/сут",
}

_UNIT_BY_SECTION: dict[str, str] = {
    "temperature": "°C",
    "precipitation": "mm",
    "anomalies": "°C",
    "seasonal": "°C",
    "hot_days": "days",
    "cold_nights": "days",
    "heavy_rain": "days",
    "heatwaves": "",
}

HEATWAVE_MESSAGE = (
    "Волны жары не рассчитываются внутри профиля: модуль heatwaves доступен "
    "отдельно в разделе «Погодные экстремумы → Волны жары». "
    "Подключение его результатов к профилю ещё не реализовано."
)


# --- Errors ---------------------------------------------------------------
def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    """Build an error that the existing envelope handler in ``main`` renders."""

    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )


def _power_message(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    if not text:
        text = "NASA POWER data is currently unavailable."
    return text[:500]


# --- Query ----------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ProfileQuery:
    """Validated profile parameters echoed by every section."""

    latitude: float
    longitude: float
    start_year: int
    end_year: int
    baseline_start_year: int
    baseline_end_year: int
    hot_threshold: float
    cold_threshold: float
    rain_threshold: float

    @property
    def thresholds(self) -> ProfileThresholds:
        return ProfileThresholds(
            hot_days=self.hot_threshold,
            cold_nights=self.cold_threshold,
            heavy_rain=self.rain_threshold,
        )


def _validate_coordinate(name: str, value: float, minimum: float, maximum: float) -> None:
    if not math.isfinite(value) or value < minimum or value > maximum:
        raise _http_error(
            422,
            "INVALID_REQUEST",
            f"{name} must be a finite number between {minimum:g} and {maximum:g}.",
        )


def _validate_years(start_year: int, end_year: int, *, prefix: str = "") -> None:
    for name, year in (("start_year", start_year), ("end_year", end_year)):
        if year < COVERAGE_MIN_YEAR or year > COVERAGE_MAX_YEAR:
            raise _http_error(
                422,
                "INVALID_REQUEST",
                f"{prefix}{name} must be between "
                f"{COVERAGE_MIN_YEAR} and {COVERAGE_MAX_YEAR}.",
            )
    if start_year > end_year:
        raise _http_error(
            422,
            "INVALID_REQUEST",
            f"{prefix}start_year must be less than or equal to {prefix}end_year.",
        )


def _validate_threshold(name: str, event: str, value: float) -> None:
    if not math.isfinite(value):
        raise _http_error(
            422,
            "INVALID_REQUEST",
            f"{name} must be a finite number.",
        )
    minimum, maximum = EXTREME_THRESHOLD_BOUNDS[event]
    if value < minimum or value > maximum:
        raise _http_error(
            422,
            "INVALID_REQUEST",
            f"{name} must be between {minimum:g} and {maximum:g} for {event}.",
        )


def profile_query(
    latitude: float = Query(
        ..., description="WGS84 latitude in decimal degrees (-90 to 90)."
    ),
    longitude: float = Query(
        ..., description="WGS84 longitude in decimal degrees (-180 to 180)."
    ),
    start_year: int = Query(
        ..., description="First inclusive UTC calendar year of the display period."
    ),
    end_year: int = Query(
        ..., description="Last inclusive UTC calendar year of the display period."
    ),
    baseline_start_year: int = Query(
        BASELINE_PRESET_START_YEAR,
        description=(
            "First inclusive baseline year; defaults to the 1991-2020 preset. "
            "The baseline is explicitly configurable."
        ),
    ),
    baseline_end_year: int = Query(
        BASELINE_PRESET_END_YEAR,
        description=(
            "Last inclusive baseline year; defaults to the 1991-2020 preset."
        ),
    ),
    hot_threshold: float = Query(
        DEFAULT_HOT_THRESHOLD,
        description=(
            "Absolute threshold for hot days: T2M_MAX > threshold in °C. "
            "Equality is not counted."
        ),
    ),
    cold_threshold: float = Query(
        DEFAULT_COLD_THRESHOLD,
        description=(
            "Absolute threshold for cold nights: T2M_MIN < threshold in °C. "
            "Equality is not counted."
        ),
    ),
    rain_threshold: float = Query(
        DEFAULT_RAIN_THRESHOLD,
        description=(
            "Absolute threshold for heavy rain: PRECTOTCORR > threshold in "
            "mm/day. Equality is not counted."
        ),
    ),
) -> ProfileQuery:
    """Validate every parameter before any NASA POWER request is made."""

    _validate_coordinate("latitude", latitude, -90.0, 90.0)
    _validate_coordinate("longitude", longitude, -180.0, 180.0)
    _validate_years(start_year, end_year)
    _validate_years(
        baseline_start_year,
        baseline_end_year,
        prefix="baseline_",
    )
    _validate_threshold("hot_threshold", "hot_days", hot_threshold)
    _validate_threshold("cold_threshold", "cold_nights", cold_threshold)
    _validate_threshold("rain_threshold", "heavy_rain", rain_threshold)

    return ProfileQuery(
        latitude=latitude,
        longitude=longitude,
        start_year=start_year,
        end_year=end_year,
        baseline_start_year=baseline_start_year,
        baseline_end_year=baseline_end_year,
        hot_threshold=hot_threshold,
        cold_threshold=cold_threshold,
        rain_threshold=rain_threshold,
    )


def profile_fetch_span(
    start_year: int,
    end_year: int,
    baseline_start_year: int,
    baseline_end_year: int,
) -> tuple[int, int]:
    """One shared daily span for all four parameters.

    It is the union of the display period and the baseline, extended by
    ``start_year - 1`` for the December that belongs to the first winter
    (``period=all``).  The extension is skipped when it would leave the POWER
    coverage, so ``winter`` of ``COVERAGE_MIN_YEAR`` stays null instead of
    reaching outside the supported span.
    """

    fetch_start = min(start_year, baseline_start_year)
    if start_year - 1 >= COVERAGE_MIN_YEAR:
        fetch_start = min(fetch_start, start_year - 1)
    return fetch_start, max(end_year, baseline_end_year)


# --- Loading --------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LoadResult:
    """Outcome of one ``load_daily`` call, with its failure isolated."""

    daily: Mapping[str, float] | None
    source: Any
    message: str | None

    @property
    def available(self) -> bool:
        return self.daily is not None and self.source is not None


def _load_series(
    *,
    query: ProfileQuery,
    variable: str,
    start_year: int,
    end_year: int,
) -> LoadResult:
    """Load one POWER parameter once, mapping upstream failures to a message."""

    try:
        daily, source = load_daily(
            latitude=query.latitude,
            longitude=query.longitude,
            variable=variable,
            start_year=start_year,
            end_year=end_year,
        )
    except PowerError as exc:
        message = _power_message(exc)
        logger.warning(
            "NASA POWER %s request failed for the climate profile: %s",
            variable,
            message,
        )
        return LoadResult(None, None, message)
    except Exception as exc:  # defensive: one variable must not fail the profile
        logger.exception("Unexpected NASA POWER failure for %s", variable)
        return LoadResult(None, None, _power_message(exc))

    if not isinstance(daily, Mapping):
        logger.warning("NASA POWER %s returned a non-mapping daily payload", variable)
        return LoadResult(
            None,
            None,
            f"NASA POWER {variable} вернул данные не в формате суточного словаря.",
        )
    if not isinstance(source, (Mapping, SourceInfo)):
        logger.warning("NASA POWER %s returned no usable source metadata", variable)
        return LoadResult(
            None,
            None,
            f"NASA POWER {variable} вернул неполные метаданные источника.",
        )
    return LoadResult(daily, source, None)


def _load_profile(
    query: ProfileQuery,
    start_year: int,
    end_year: int,
) -> dict[str, LoadResult]:
    """Load exactly four parameters sequentially over the shared span."""

    return {
        variable: _load_series(
            query=query,
            variable=variable,
            start_year=start_year,
            end_year=end_year,
        )
        for variable in PROFILE_VARIABLES
    }


# --- Section isolation ----------------------------------------------------
def _contract_message(label: str, exc: BaseException) -> str:
    detail = " ".join(str(exc).split())[:400]
    return f"{label}: данные NASA POWER не прошли проверку контракта. {detail}".strip()


def _isolated(
    section_class: Callable[..., Any],
    label: str,
    build: Callable[[], Any],
) -> Any:
    """Build one section; a failure becomes that section's error status."""

    try:
        data = build()
    except analysis.DataContractError as exc:
        logger.warning("Climate profile %s failed validation: %s", label, exc)
        return section_class(
            status="error",
            data=None,
            message=_contract_message(label, exc),
        )
    except Exception:  # defensive: never let one section break the profile
        logger.exception("Climate profile %s failed unexpectedly", label)
        return section_class(
            status="error",
            data=None,
            message=f"{label}: раздел не построен из-за внутренней ошибки сервера.",
        )
    return section_class(status="ok", data=data, message=None)


def _unavailable_section(section_class: Callable[..., Any], load: LoadResult, label: str) -> Any:
    return section_class(
        status="error",
        data=None,
        message=load.message or f"{label}: данные NASA POWER недоступны.",
    )


def _trend_section(
    load: LoadResult,
    *,
    query: ProfileQuery,
    variable: str,
    label: str,
) -> TrendSection:
    if not load.available:
        return _unavailable_section(TrendSection, load, label)
    return _isolated(
        TrendSection,
        label,
        lambda: analysis.build_trend_response(
            daily=load.daily,
            source=load.source,
            latitude=query.latitude,
            longitude=query.longitude,
            variable=variable,  # type: ignore[arg-type]
            start_year=query.start_year,
            end_year=query.end_year,
        ),
    )


def _anomaly_section(load: LoadResult, *, query: ProfileQuery) -> AnomalySection:
    label = _LABELS["anomalies"]
    if not load.available:
        return _unavailable_section(AnomalySection, load, label)
    return _isolated(
        AnomalySection,
        label,
        lambda: climate.build_anomaly_response(
            daily=load.daily,
            source=load.source,
            latitude=query.latitude,
            longitude=query.longitude,
            variable="temperature",
            start_year=query.start_year,
            end_year=query.end_year,
            baseline_start_year=query.baseline_start_year,
            baseline_end_year=query.baseline_end_year,
        ),
    )


def _seasonal_section(load: LoadResult, *, query: ProfileQuery) -> SeasonalSection:
    label = _LABELS["seasonal"]
    if not load.available:
        return _unavailable_section(SeasonalSection, load, label)
    return _isolated(
        SeasonalSection,
        label,
        lambda: climate.build_seasonal_response(
            daily=load.daily,
            source=load.source,
            latitude=query.latitude,
            longitude=query.longitude,
            variable="temperature",
            start_year=query.start_year,
            end_year=query.end_year,
            period=ALL_PERIOD,
        ),
    )


def _extremes_section(
    load: LoadResult,
    *,
    query: ProfileQuery,
    event: str,
    threshold: float,
) -> ExtremesSection:
    label = _LABELS[event]
    if not load.available:
        return _unavailable_section(ExtremesSection, load, label)
    return _isolated(
        ExtremesSection,
        label,
        lambda: extremes.build_extremes_response(
            daily=load.daily,
            source=load.source,
            latitude=query.latitude,
            longitude=query.longitude,
            event=event,
            threshold=threshold,
            start_year=query.start_year,
            end_year=query.end_year,
        ),
    )


def _build_sections(
    query: ProfileQuery,
    loaded: Mapping[str, LoadResult],
) -> ProfileSections:
    """Build the eight sections from the four already loaded series."""

    return ProfileSections(
        temperature=_trend_section(
            loaded["temperature"],
            query=query,
            variable="temperature",
            label=_LABELS["temperature"],
        ),
        precipitation=_trend_section(
            loaded["precipitation"],
            query=query,
            variable="precipitation",
            label=_LABELS["precipitation"],
        ),
        anomalies=_anomaly_section(loaded["temperature"], query=query),
        seasonal=_seasonal_section(loaded["temperature"], query=query),
        hot_days=_extremes_section(
            loaded["temperature_max"],
            query=query,
            event="hot_days",
            threshold=query.hot_threshold,
        ),
        cold_nights=_extremes_section(
            loaded["temperature_min"],
            query=query,
            event="cold_nights",
            threshold=query.cold_threshold,
        ),
        heavy_rain=_extremes_section(
            loaded["precipitation"],
            query=query,
            event="heavy_rain",
            threshold=query.rain_threshold,
        ),
        heatwaves=HeatwaveSection(
            status="unavailable",
            data=None,
            message=HEATWAVE_MESSAGE,
        ),
    )


# --- Summary and limitations ---------------------------------------------
def _ru(value: float, digits: int = 2, *, signed: bool = False) -> str:
    """Format a number for Russian prose (comma decimal separator)."""

    text = f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"
    return text.replace(".", ",")


def _p_clause(value: float) -> str:
    return "p < 0,001" if value < 0.001 else f"p = {_ru(value, 3)}"


def _trend_summary(model: TrendResponse, label: str) -> list[str]:
    complete = [point.value for point in model.series if point.value is not None]
    if not complete:
        return [
            f"{label}: нет ни одного полного года, поэтому среднее и тренд не "
            "рассчитаны (пропуски не заполняются)."
        ]
    mean = math.fsum(complete) / len(complete)
    lines = [
        f"{label}: среднее по {len(complete)} полным годам — "
        f"{_ru(mean)} {model.unit}."
    ]
    trend = model.trend
    if trend is None:
        lines.append(
            f"{label}: тренд не рассчитан — нужно не менее {MIN_TREND_YEARS} "
            "полных лет."
        )
        return lines
    verdict = "значим" if trend.significant else "не значим"
    lines.append(
        f"{label}: {_ru(trend.per_decade, signed=True)} {model.unit} за 10 лет, "
        f"95 % ДИ {_ru(trend.ci95_low, signed=True)}…"
        f"{_ru(trend.ci95_high, signed=True)}, "
        f"{_p_clause(trend.p_value)}, R²={_ru(trend.r_squared)}, "
        f"полных лет {trend.years_used}; тренд {verdict} при α=0,05."
    )
    return lines


def _anomaly_summary(model: AnomalyResponse) -> list[str]:
    span = model.baseline_end_year - model.baseline_start_year + 1
    if model.baseline_mean is None:
        return [
            f"Аномалии: база {model.baseline_start_year}–"
            f"{model.baseline_end_year} неполная "
            f"({model.baseline_complete_years} из {span} лет), поэтому среднее "
            "базы и аномалии не рассчитаны."
        ]
    lines = [
        f"Аномалии: база {model.baseline_start_year}–{model.baseline_end_year} = "
        f"{_ru(model.baseline_mean)} {model.unit} "
        f"({model.baseline_complete_years} полных лет)."
    ]
    latest = next(
        (point for point in reversed(model.series) if point.anomaly is not None),
        None,
    )
    if latest is not None:
        lines.append(
            f"Аномалии: последний полный год {latest.year} — "
            f"{_ru(latest.anomaly, signed=True)} {model.unit} к базе."
        )
    return lines


def _seasonal_summary(model: SeasonalResponse) -> list[str]:
    parts: list[str] = []
    for entry in model.periods:
        if entry.trend is None:
            continue
        name = _SEASON_LABELS.get(entry.period, entry.period)
        parts.append(f"{name} {_ru(entry.trend.per_decade, signed=True)}")
    if not parts:
        return [
            "Сезоны: тренды не рассчитаны — не хватает полных сезонов "
            f"(нужно не менее {MIN_TREND_YEARS} полных лет на сезон)."
        ]
    return [
        f"Сезоны: наклоны за 10 лет, {model.unit} — " + "; ".join(parts) + "."
    ]


def _extremes_summary(model: ExtremesResponse, label: str) -> list[str]:
    complete = [point.events for point in model.series if point.events is not None]
    definition = _EVENT_DEFINITIONS[model.event].format(threshold=model.threshold)
    if not complete:
        return [
            f"{label} ({definition}): нет полных лет, счётчики не рассчитаны."
        ]
    mean = sum(complete) / len(complete)
    text = (
        f"{label} ({definition}): {_ru(mean, 1)} дн./год в среднем по "
        f"{len(complete)} полным годам"
    )
    trend = model.trend
    if trend is None:
        text += "; тренд не рассчитан."
    elif trend.ci95_low is None or trend.p_value is None:
        text += (
            f"; тренд {_ru(trend.per_decade, signed=True)} дн./365 дн. за "
            "10 лет, интервал и p не рассчитывались."
        )
    else:
        verdict = "значим" if trend.significant else "не значим"
        text += (
            f"; тренд {_ru(trend.per_decade, signed=True)} дн./365 дн. за "
            f"10 лет, 95 % ДИ {_ru(trend.ci95_low, signed=True)}…"
            f"{_ru(trend.ci95_high, signed=True)}, "
            f"{_p_clause(trend.p_value)}; {verdict} при α=0,05."
        )
    return [text]


def _summary(sections: ProfileSections, query: ProfileQuery) -> list[str]:
    lines = [
        f"Точка {query.latitude:.4f}°, {query.longitude:.4f}°; период "
        f"{query.start_year}–{query.end_year}; база аномалий "
        f"{query.baseline_start_year}–{query.baseline_end_year}."
    ]
    for name in PROFILE_SECTIONS[:-1]:
        section = getattr(sections, name)
        label = _LABELS[name]
        if section.status != "ok" or section.data is None:
            lines.append(f"{label}: раздел недоступен — {section.message}")
            continue
        if name in ("temperature", "precipitation"):
            lines.extend(_trend_summary(section.data, label))
        elif name == "anomalies":
            lines.extend(_anomaly_summary(section.data))
        elif name == "seasonal":
            lines.extend(_seasonal_summary(section.data))
        else:
            lines.extend(_extremes_summary(section.data, label))
    return lines


def _missing_years(series: Any, attribute: str) -> list[int]:
    return [
        point.year for point in series if getattr(point, attribute) is None
    ]


def _coverage_gaps(sections: ProfileSections) -> list[str]:
    gaps: list[str] = []
    for name in ("temperature", "precipitation", "anomalies"):
        section = getattr(sections, name)
        if section.status != "ok" or section.data is None:
            continue
        missing = _missing_years(section.data.series, "value")
        if missing:
            gaps.append(f"{_SHORT_LABELS[name]} — {len(missing)} г.")
    seasonal = sections.seasonal
    if seasonal.status == "ok" and seasonal.data is not None:
        worst = max(
            (
                len(_missing_years(entry.series, "value"))
                for entry in seasonal.data.periods
            ),
            default=0,
        )
        if worst:
            gaps.append(f"сезоны — до {worst} г. на сезон")
    for name in ("hot_days", "cold_nights", "heavy_rain"):
        section = getattr(sections, name)
        if section.status != "ok" or section.data is None:
            continue
        missing = _missing_years(section.data.series, "events")
        if missing:
            gaps.append(f"{_SHORT_LABELS[name]} — {len(missing)} г.")
    return gaps


def _limitations(sections: ProfileSections, query: ProfileQuery) -> list[str]:
    items = [
        "NASA POWER — оценка реанализа в узле сетки, а не измерение на "
        "метеостанции; значение относится только к выбранной точке и периоду.",
        "Основная метеорологическая сетка MERRA-2 имеет шаг 0,5° по широте "
        "и 0,625° по долготе. T2M, T2M_MAX и T2M_MIN описывают воздух на "
        "высоте 2 м, PRECTOTCORR — скорректированные осадки. Точная "
        "атрибуция продукта, время загрузки и покрытие приведены в каждом разделе.",
        "Отсутствие статистической поддержки не доказывает отсутствие "
        "изменений. Различия сезонных наклонов статистически не проверялись. "
        "Наборы полных лет у показателей могут различаться.",
        "Для годовых и сезонных трендов интервалы и p-значения МНК "
        "предполагают независимые остатки с постоянной дисперсией. "
        "При автокорреляции эта оценка неопределённости может быть "
        "оптимистичной.",
        "Для пороговых событий, когда ряд подходит для статистического "
        "вывода, интервалы и p-значения используют HAC(3). Эта поправка "
        "учитывает зависимость остатков на коротких лагах. Вывод для "
        "короткого или вырожденного ряда может быть недоступен.",
        "Повторный выбор точек, периодов, сезонов и порогов создаёт "
        "множественные сравнения; p-значения здесь не корректируются.",
        f"База аномалий задана явно: {query.baseline_start_year}–"
        f"{query.baseline_end_year}. При хотя бы одном неполном базовом годе "
        "базовое среднее и все аномалии подавляются, а не заполняются.",
        f"Пороги экстремумов — фиксированные абсолютные: T2M_MAX > "
        f"{query.hot_threshold:g} °C, T2M_MIN < {query.cold_threshold:g} °C, "
        f"PRECTOTCORR > {query.rain_threshold:g} мм/сут; равенство порогу "
        "событием не считается.",
        "Сезоны фиксированы для Северного полушария (DJF/MAM/JJA/SON); зимний "
        "сезон помечен годом января и включает декабрь предыдущего года.",
    ]
    baseline_span = query.baseline_end_year - query.baseline_start_year + 1
    if baseline_span < CLIMATE_NORMAL_YEARS:
        items.append(
            f"База {query.baseline_start_year}–{query.baseline_end_year} "
            f"короче {CLIMATE_NORMAL_YEARS} лет: это reference period, а не "
            "климатическая норма."
        )
    items.append(
        "Волны жары не рассчитываются внутри профиля. Модуль heatwaves "
        "доступен отдельно в разделе «Погодные экстремумы → Волны жары»."
    )

    gaps = _coverage_gaps(sections)
    if gaps:
        items.append(
            "Неполное покрытие исключено из средних и трендов: "
            + "; ".join(gaps)
            + ". Пропуски не заполнялись."
        )

    failed = [
        _SHORT_LABELS[name]
        for name in PROFILE_SECTIONS[:-1]
        if getattr(sections, name).status == "error"
    ]
    if failed:
        items.append(
            "Ответ частичный: разделы с ошибкой — "
            + ", ".join(failed)
            + "; остальные рассчитаны независимо, отсутствующие значения не "
            "заменены."
        )
    return items


# --- Profile --------------------------------------------------------------
def build_profile(query: ProfileQuery) -> ClimateProfileResponse:
    """Assemble one profile: four loads, eight sections, no extra fetch."""

    fetch_start, fetch_end = profile_fetch_span(
        query.start_year,
        query.end_year,
        query.baseline_start_year,
        query.baseline_end_year,
    )
    loaded = _load_profile(query, fetch_start, fetch_end)
    sections = _build_sections(query, loaded)

    if sections.all_network_failed:
        detail = next(
            (
                section.message
                for _name, section in sections.network_sections()
                if section.message
            ),
            "NASA POWER не ответил.",
        )
        logger.warning("Every climate profile section failed: %s", detail)
        raise _http_error(
            503,
            "POWER_UNAVAILABLE",
            f"Все разделы климатического профиля недоступны. {detail}"[:500],
        )

    return ClimateProfileResponse(
        latitude=query.latitude,
        longitude=query.longitude,
        start_year=query.start_year,
        end_year=query.end_year,
        baseline_start_year=query.baseline_start_year,
        baseline_end_year=query.baseline_end_year,
        thresholds=query.thresholds,
        sections=sections,
        summary=_summary(sections, query),
        limitations=_limitations(sections, query),
    )


# --- CSV ------------------------------------------------------------------
CSV_COLUMNS: tuple[str, ...] = (
    "section",
    "series",
    "year",
    "statistic",
    "value",
    "unit",
    "status",
    "valid_days",
    "expected_days",
    "threshold",
    "note",
)

_UNITS_NOTE = (
    "units: annual_mean=°C (mean of complete daily T2M); annual_sum=mm (sum of "
    "complete daily PRECTOTCORR); annual_value and baseline_mean=°C in the anomaly "
    "section; anomaly=°C; period_value=°C (mean over the "
    "period); event_days=days per year; event_fraction=share of valid days; "
    "ols_fitted/ols_fitted_rate=OLS estimate at a complete year; "
    "section_status describes availability; per_decade and ci95_low/high use "
    "the row's trend unit; p_value is dimensionless; years_used counts years; "
    "count_per_decade is the change in raw event days per decade"
)


def _csv_number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.10g}"


def _profile_sources(profile: ClimateProfileResponse) -> list[SourceInfo]:
    """Unique source metadata of the successful sections, in section order."""

    seen: set[tuple[str, str]] = set()
    sources: list[SourceInfo] = []
    for name in PROFILE_SECTIONS:
        section = getattr(profile.sections, name)
        data = section.data if section.status == "ok" else None
        if data is None:
            continue
        source = data.source
        key = (source.parameter, source.request_url)
        if key in seen:
            continue
        seen.add(key)
        sources.append(source)
    return sources


def _csv_header(profile: ClimateProfileResponse) -> list[str]:
    thresholds = profile.thresholds
    lines = [
        "EarthScope climate profile CSV. Values are copied from one validated "
        "API response; nothing is recomputed here. Synthetic test data never "
        "reaches this export.",
        f"point: latitude={profile.latitude!r} longitude={profile.longitude!r}; "
        f"years={profile.start_year}-{profile.end_year}; anomaly "
        f"baseline={profile.baseline_start_year}-{profile.baseline_end_year}",
        f"thresholds (strict): hot_days T2M_MAX > {thresholds.hot_days:g} °C; "
        f"cold_nights T2M_MIN < {thresholds.cold_nights:g} °C; heavy_rain "
        f"PRECTOTCORR > {thresholds.heavy_rain:g} mm/day",
        _UNITS_NOTE,
        "coverage: valid_days/expected_days count the UTC days present in the "
        "requested year; an incomplete year keeps a null value and is excluded "
        "from means and trends",
    ]
    for source in _profile_sources(profile):
        lines.append(
            f"provenance: parameter={source.parameter}; source={source.name}; "
            f"dataset={source.dataset}; unit={source.daily_unit}; "
            f"time={source.time_standard}; retrieved_at={source.retrieved_at}; "
            f"cached={'yes' if source.cached else 'no'}; "
            f"request_url={source.request_url}"
        )
    return lines


def _status_row(
    name: str,
    section: Any,
    profile: ClimateProfileResponse,
) -> list[str]:
    threshold = getattr(profile.thresholds, name, None)
    note = section.message or ""
    if note and note.lstrip().startswith(("=", "+", "-", "@")):
        note = "'" + note
    return [
        name,
        "",
        "",
        "section_status",
        "",
        _UNIT_BY_SECTION[name],
        section.status,
        "",
        "",
        "" if threshold is None else f"{threshold:g}",
        note,
    ]


def _series_rows(
    name: str,
    points: Any,
    *,
    unit: str,
    value_statistic: str,
    value_attribute: str,
    threshold: str = "",
    fitted_attribute: str | None = None,
    fitted_statistic: str = "",
    fitted_unit: str | None = None,
) -> list[list[str]]:
    rows: list[list[str]] = []
    for point in points:
        rows.append(
            [
                name,
                "annual",
                str(point.year),
                value_statistic,
                _csv_number(getattr(point, value_attribute)),
                unit,
                "",
                str(point.valid_days),
                str(point.expected_days),
                threshold,
                "",
            ]
        )
    if fitted_attribute is None:
        return rows
    for point in points:
        fitted = getattr(point, fitted_attribute)
        if fitted is None:
            continue
        rows.append(
            [
                name,
                "annual",
                str(point.year),
                fitted_statistic,
                _csv_number(fitted),
                fitted_unit or unit,
                "",
                "",
                "",
                threshold,
                "OLS estimate at a complete year",
            ]
        )
    return rows


def _value_rows(
    name: str,
    data: Any,
) -> list[list[str]]:
    if name in ("temperature", "precipitation"):
        return _series_rows(
            name,
            data.series,
            unit=data.unit,
            value_statistic=(
                "annual_mean" if name == "temperature" else "annual_sum"
            ),
            value_attribute="value",
            fitted_attribute="fitted",
            fitted_statistic="ols_fitted",
        )
    if name == "anomalies":
        rows = _series_rows(
            name,
            data.series,
            unit=data.unit,
            value_statistic="annual_value",
            value_attribute="value",
        )
        rows.extend(
            [
                name,
                "annual",
                str(point.year),
                "anomaly",
                _csv_number(point.anomaly),
                data.unit,
                "",
                str(point.valid_days),
                str(point.expected_days),
                "",
                f"vs baseline {data.baseline_start_year}-{data.baseline_end_year}",
            ]
            for point in data.series
        )
        rows.append(
            [
                name,
                "baseline",
                f"{data.baseline_start_year}-{data.baseline_end_year}",
                "baseline_mean",
                _csv_number(data.baseline_mean),
                data.unit,
                "",
                "",
                "",
                "",
                f"{data.baseline_complete_years} complete baseline years; "
                "equally weighted",
            ]
        )
        return rows
    if name == "seasonal":
        rows: list[list[str]] = []
        for entry in data.periods:
            for point in entry.series:
                rows.append(
                    [
                        name,
                        entry.period,
                        str(point.year),
                        "period_value",
                        _csv_number(point.value),
                        data.unit,
                        "",
                        str(point.valid_days),
                        str(point.expected_days),
                        "",
                        "",
                    ]
                )
            for point in entry.series:
                if point.fitted is None:
                    continue
                rows.append(
                    [
                        name,
                        entry.period,
                        str(point.year),
                        "ols_fitted",
                        _csv_number(point.fitted),
                        data.unit,
                        "",
                        "",
                        "",
                        "",
                        "OLS estimate at a complete period",
                    ]
                )
        return rows
    threshold = f"{data.threshold:g}"
    definition = _EVENT_DEFINITIONS[data.event].format(threshold=data.threshold)
    rows = _series_rows(
        name,
        data.series,
        unit="days",
        value_statistic="event_days",
        value_attribute="events",
        threshold=threshold,
        fitted_attribute="fitted",
        fitted_statistic="ols_fitted_rate",
        fitted_unit="days/365days",
    )
    for row in rows:
        if row[3] == "event_days":
            row[10] = definition
    rows.extend(
        [
            name,
            "annual",
            str(point.year),
            "event_fraction",
            _csv_number(point.fraction),
            "fraction",
            "",
            str(point.valid_days),
            str(point.expected_days),
            threshold,
            definition,
        ]
        for point in data.series
    )
    return rows


def render_profile_csv(profile: ClimateProfileResponse) -> str:
    """Render the profile as a tidy, long CSV with units and provenance."""

    buffer = io.StringIO()
    for line in _csv_header(profile):
        buffer.write("# " + " ".join(line.splitlines()) + "\n")
    for line in profile.limitations:
        buffer.write("# limitation: " + " ".join(line.splitlines()) + "\n")

    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for name in PROFILE_SECTIONS:
        section = getattr(profile.sections, name)
        writer.writerow(_status_row(name, section, profile))
        if section.status != "ok" or section.data is None:
            continue
        writer.writerows(_value_rows(name, section.data))
        data = section.data
        entries = data.periods if name == "seasonal" else [data]
        for entry in entries:
            trend = getattr(entry, "trend", None)
            if trend is None:
                continue
            period = entry.period if name == "seasonal" else "annual"
            unit = "event days per 365 days per decade" if name in ("hot_days", "cold_nights", "heavy_rain") else f"{data.unit}/decade"
            for statistic in ("per_decade", "ci95_low", "ci95_high", "p_value", "years_used", "count_per_decade"):
                value = getattr(trend, statistic, None)
                if value is None:
                    continue
                statistic_unit = "1" if statistic == "p_value" else "years" if statistic == "years_used" else "days/decade" if statistic == "count_per_decade" else unit
                writer.writerow([name, period, "", statistic, _csv_number(value), statistic_unit, "", "", "", "", "copied from validated trend"])
    return buffer.getvalue()


# --- Routes ---------------------------------------------------------------
def _wants_pdf(request: Request) -> bool:
    return wants_pdf(request.headers.get("accept", ""))


def _pdf_response(profile: ClimateProfileResponse) -> Response:
    from . import profile_pdf

    try:
        body = profile_pdf.render_profile_pdf(profile)
    except profile_pdf.ProfilePdfError as exc:
        logger.warning("Climate profile PDF is unavailable: %s", exc)
        raise _http_error(503, "PDF_UNAVAILABLE", str(exc)) from exc
    except Exception as exc:  # defensive: never leak a stack trace as a PDF
        logger.exception("Climate profile PDF rendering failed")
        raise _http_error(
            503,
            "PDF_UNAVAILABLE",
            "Не удалось собрать PDF климатического профиля.",
        ) from exc
    return Response(
        body,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                'attachment; filename="EarthScope-climate-profile.pdf"'
            ),
            "Cache-Control": "no-store",
            "Vary": "Accept",
        },
    )


def _register_profile_routes(application: FastAPI) -> None:
    @application.get(
        PROFILE_PATH,
        response_model=ClimateProfileResponse,
        tags=["analysis"],
        name="climate_profile",
    )
    def climate_profile(
        request: Request,
        response: Response,
        query: ProfileQuery = Depends(profile_query),
    ) -> Any:
        """One point profile: four daily loads, eight isolated sections.

        ``Accept: application/pdf`` returns the same validated profile as one
        PDF report; the default and ``Accept: application/json`` return the
        JSON body.  Partial section errors are HTTP 200; if every network
        section fails the response is HTTP 503.
        """

        profile = build_profile(query)
        if _wants_pdf(request):
            return _pdf_response(profile)
        response.headers["Vary"] = "Accept"
        return profile

    @application.get(
        PROFILE_CSV_PATH,
        tags=["analysis"],
        name="climate_profile_csv",
        response_class=Response,
    )
    def climate_profile_csv(
        query: ProfileQuery = Depends(profile_query),
    ) -> Response:
        """Tidy long CSV of the same validated profile, with units/provenance.

        The header block documents the point, period, baseline, strict
        thresholds, units and NASA POWER provenance.  Only values already
        present in the JSON profile are exported.
        """

        profile = build_profile(query)
        return Response(
            render_profile_csv(profile).encode("utf-8-sig"),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    'attachment; filename="EarthScope-climate-profile.csv"'
                ),
                "Cache-Control": "no-store",
            },
        )


def register_routes(application: FastAPI) -> None:
    """Register ``GET /api/climate-profile`` and ``GET /api/climate-profile.csv``.

    Called by the integration owner from ``app.main.create_app``; this module
    imports nothing from ``main`` so there is no circular import.
    """

    _register_profile_routes(application)
