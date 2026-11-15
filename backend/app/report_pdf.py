"""PDF research reports rendered from verified EarthScope responses.

The single entry point is :func:`render_report`.  It takes the JSON body that an
existing API route already returns, validates it against the same Pydantic model
that guards that route, and renders a Russian-language, vector PDF with a chart,
the reported statistics, provenance and the method's limits.

Design notes
------------
* **Validation, not re-computation.**  Every number printed comes from the
  validated payload.  The renderer never re-fits a trend, re-derives a q-value or
  fills a missing year.  ``world-cell`` is the only kind without a response model
  of its own (``GET /api/maps/world/cell`` returns a plain mapping), so its
  payload is validated into the local :class:`ResearchReport` model, which mirrors
  the keys produced by :func:`app.global_map.cell_result`.
* **Platform fonts.**  Cyrillic needs an embedded TrueType font, so the module
  registers a verified platform pair: Arial on Windows, DejaVu Sans on Linux
  (Arial again on macOS).  A candidate is accepted only when both faces exist and
  cover the Cyrillic alphabet; there is no silent fallback to a core PDF font,
  because that would render blank boxes instead of text.  ``EARTHSCOPE_PDF_FONT``
  and ``EARTHSCOPE_PDF_FONT_BOLD`` may point at an explicit local pair (useful in
  containers).  Registration happens once, under a lock.
* **Vector charts.**  Charts are ReportLab ``LinePlot`` drawings added to the
  Platypus story, so they stay vector in the PDF (no raster images).
* **Links.**  Only URLs from :data:`TRUSTED_LINKS` become clickable.  Payload
  strings (warnings, dataset names, the POWER ``request_url``) are XML-escaped and
  rendered as inert text; the request URL is displayed for provenance but is not
  turned into a hyperlink.
* **Reproducibility.**  The document carries no wall-clock timestamp.  Provenance
  timing comes from the payload's ``retrieved_at`` and the canvas is built with
  ``invariant=True``, so the same payload yields byte-identical PDFs.
* **Extensions.**  ``heatwaves`` is not one of the six core kinds in
  :data:`KINDS`: :func:`render_report` delegates it to
  :func:`app.heatwave_report.render_heatwave_pdf`, which validates with
  :class:`app.heatwaves.HeatwaveResponse` and reuses this module's font, style,
  table and canvas helpers.  The core kinds and their contracts stay unchanged.

Public interface::

    render_report(kind: str, payload: dict) -> bytes
    KINDS, MODEL_FOR_KIND, InvalidReportKind, InvalidReportPayload, ReportFontError
"""

from __future__ import annotations

import io
import math
import os
import sys
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib import colors
from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .global_map import GRID_SHAPE, PARAMETERS, YEARS
from .schemas import (
    AnomalyResponse,
    CompareResponse,
    ExtremesResponse,
    SeasonalResponse,
    TrendResponse,
    TrendStats,
)

__all__ = [
    "KINDS",
    "MODEL_FOR_KIND",
    "InvalidReportKind",
    "InvalidReportPayload",
    "ReportFontError",
    "ResearchReport",
    "render_report",
]

KINDS: tuple[str, ...] = (
    "trend",
    "compare",
    "anomalies",
    "seasonal",
    "extremes",
    "world-cell",
)

# --- Trusted, static links -------------------------------------------------
# Every URL below is documented in docs/SCIENTIFIC_METHOD.md (checked
# 2026-11-14) or in app/global_map.py.  Nothing from a request payload is ever
# promoted to a hyperlink.
POWER_DAILY_DOCS = "https://power.larc.nasa.gov/docs/services/api/temporal/daily/"
POWER_MONTHLY_DOCS = "https://power.larc.nasa.gov/docs/services/api/temporal/monthly/"
POWER_REGISTRY = "https://registry.opendata.aws/nasa-power/"
WMO_NORMALS = "https://www.ncei.noaa.gov/products/wmo-climate-normals"

TRUSTED_LINKS: dict[str, str] = {
    "power_daily": POWER_DAILY_DOCS,
    "power_monthly": POWER_MONTHLY_DOCS,
    "power_registry": POWER_REGISTRY,
    "wmo_normals": WMO_NORMALS,
}

# --- Layout ---------------------------------------------------------------
MARGIN = 45.0
CONTENT_WIDTH = A4[0] - 2 * MARGIN
CHART_HEIGHT = 186.0
CHART_PLOT_HEIGHT = 136.0

INK = colors.HexColor("#1b2733")
MUTED = colors.HexColor("#5b6b7a")
ACCENT = colors.HexColor("#1f4e79")
SERIES_BLUE = colors.HexColor("#1f4e79")
SERIES_GREEN = colors.HexColor("#2e7d32")
SERIES_ORANGE = colors.HexColor("#c55a11")
SERIES_PLUM = colors.HexColor("#6a3d9a")
SERIES_TEAL = colors.HexColor("#00796b")
SERIES_FIT = colors.HexColor("#c55a11")
RULE = colors.HexColor("#d5dde5")
BAND = colors.HexColor("#eef2f6")
GREY_LINE = colors.HexColor("#9aa5b1")

SERIES_PALETTE: tuple[Color, ...] = (
    SERIES_BLUE,
    SERIES_GREEN,
    SERIES_PLUM,
    SERIES_TEAL,
)

FONT_NAME = "EarthScopeSans"
FONT_BOLD = "EarthScopeSans-Bold"

_CYRILLIC = (
    "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ"
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
)
# Characters the renderer itself emits, next to the Cyrillic alphabet.  A face
# that lacks any of them would produce a black box in the PDF.
_EXTRA_GLYPHS = "°—«»≤²·±"
_REQUIRED_GLYPHS = _CYRILLIC + _EXTRA_GLYPHS


class ReportFontError(RuntimeError):
    """No usable Cyrillic TrueType font pair was found on this platform."""


class InvalidReportKind(ValueError):
    """The requested report kind is not one of :data:`KINDS`."""

    def __init__(self, kind: object) -> None:
        self.kind = kind
        super().__init__(
            f"unknown report kind {kind!r}; expected one of {', '.join(KINDS)}"
        )


class InvalidReportPayload(ValueError):
    """The payload does not satisfy the response model for its kind.

    The message carries field locations and Pydantic messages only; payload
    values are deliberately omitted so a malformed request cannot echo secrets
    into logs.
    """

    def __init__(self, kind: str, errors: Sequence[Mapping[str, Any]]) -> None:
        self.kind = kind
        self.errors = tuple(errors)
        details = []
        for error in self.errors[:5]:
            location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
            details.append(f"{location}: {error.get('msg', 'invalid value')}")
        if len(self.errors) > 5:
            details.append(f"... and {len(self.errors) - 5} more")
        super().__init__(
            f"{kind} payload failed schema validation: " + "; ".join(details)
        )


# --- Typed world-cell payload --------------------------------------------
_WORLD_UNIT_BY_PARAMETER: dict[str, str] = {
    parameter: unit for parameter, unit in PARAMETERS.values()
}
_WORLD_YEAR_MIN = min(YEARS)
_WORLD_YEAR_MAX = max(YEARS)
# Cell centres in global_map.cell_result span the full sphere inclusively, so the
# spacing is the span divided by the number of gaps between centres.
_WORLD_LAT_STEP = 180.0 / (GRID_SHAPE[0] - 1)
_WORLD_LON_STEP = 360.0 / (GRID_SHAPE[1] - 1)


class WorldCellPoint(BaseModel):
    """One archived annual value of a world cell; ``None`` is a missing year."""

    year: int = Field(ge=_WORLD_YEAR_MIN, le=_WORLD_YEAR_MAX)
    value: float | None

    @field_validator("value")
    @classmethod
    def _finite_or_null(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not math.isfinite(value):
            raise ValueError("cell value must be finite")
        return value


class ResearchReport(BaseModel):
    """Typed payload of ``GET /api/maps/world/cell``.

    This mirrors :func:`app.global_map.cell_result` exactly.  ``slope``,
    ``ci_low``, ``ci_high``, ``p`` and ``q`` are nullable because the archive
    stores ``NaN`` for cells whose fit is undefined (for example at a pole).
    The model only checks coherence; it never recomputes an estimate.
    """

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    parameter: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    slope: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    p: float | None = Field(default=None, ge=0.0, le=1.0)
    q: float | None = Field(default=None, ge=0.0, le=1.0)
    version: str = Field(min_length=1)
    series: list[WorldCellPoint] = Field(min_length=1)

    @field_validator("latitude", "longitude", "slope", "ci_low", "ci_high")
    @classmethod
    def _finite_or_null(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        return value

    @field_validator("series")
    @classmethod
    def _ordered_years(
        cls, series: list[WorldCellPoint]
    ) -> list[WorldCellPoint]:
        years = [point.year for point in series]
        if years != sorted(set(years)):
            raise ValueError("series must list distinct years in ascending order")
        return series

    @model_validator(mode="after")
    def _coherent_cell(self) -> "ResearchReport":
        expected_unit = _WORLD_UNIT_BY_PARAMETER.get(self.parameter)
        if expected_unit is None:
            allowed = ", ".join(sorted(_WORLD_UNIT_BY_PARAMETER))
            raise ValueError(f"parameter must be one of {allowed}")
        if self.unit != expected_unit:
            raise ValueError(f"unit must be {expected_unit!r} for {self.parameter}")

        if self.slope is None:
            if any(
                value is not None
                for value in (self.ci_low, self.ci_high, self.p, self.q)
            ):
                raise ValueError("a null slope requires null ci/p/q values")
            return self

        if (self.ci_low is None) != (self.ci_high is None):
            raise ValueError("ci_low and ci_high must both be present or null")
        if self.ci_low is not None and self.ci_low > self.ci_high:
            raise ValueError("ci_low must not exceed ci_high")
        return self


MODEL_FOR_KIND: dict[str, type[BaseModel]] = {
    "trend": TrendResponse,
    "compare": CompareResponse,
    "anomalies": AnomalyResponse,
    "seasonal": SeasonalResponse,
    "extremes": ExtremesResponse,
    "world-cell": ResearchReport,
}


# --- Font handling --------------------------------------------------------
_font_lock = threading.Lock()
_fonts_registered = False


def _font_candidates() -> list[tuple[str, str]]:
    """Return ordered (regular, bold) candidate paths for this platform."""

    pairs: list[tuple[str, str]] = []
    override = (
        os.environ.get("EARTHSCOPE_PDF_FONT"),
        os.environ.get("EARTHSCOPE_PDF_FONT_BOLD"),
    )
    if all(override):
        pairs.append((str(override[0]), str(override[1])))

    if sys.platform == "win32":
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        pairs.append((str(fonts / "arial.ttf"), str(fonts / "arialbd.ttf")))
    elif sys.platform == "darwin":
        fonts = Path("/System/Library/Fonts/Supplemental")
        pairs.append((str(fonts / "Arial.ttf"), str(fonts / "Arial Bold.ttf")))
    else:
        for root in (
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path("/usr/share/fonts/truetype"),
        ):
            pairs.append(
                (
                    str(root / "truetype/dejavu/DejaVuSans.ttf"),
                    str(root / "truetype/dejavu/DejaVuSans-Bold.ttf"),
                )
            )
            pairs.append(
                (
                    str(root / "dejavu/DejaVuSans.ttf"),
                    str(root / "dejavu/DejaVuSans-Bold.ttf"),
                )
            )
    # Keep the order, drop duplicates.
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            unique.append(pair)
    return unique


def _covers_cyrillic(face: Any) -> bool:
    return all(face.charToGlyph.get(ord(ch), 0) for ch in _REQUIRED_GLYPHS)


def _register_fonts(candidates: Sequence[tuple[str, str]] | None = None) -> None:
    """Register the first candidate pair that exists and covers Cyrillic.

    Raises :class:`ReportFontError` when nothing usable is found; a Latin-only
    core font is never used, because it would silently drop Russian text.
    """

    global _fonts_registered
    if _fonts_registered:
        return
    with _font_lock:
        if _fonts_registered:
            return
        tried: list[str] = []
        for regular, bold in candidates if candidates is not None else _font_candidates():
            try:
                regular_font = TTFont(FONT_NAME, regular)
                bold_font = TTFont(FONT_BOLD, bold)
            except Exception:  # missing, unreadable or not a TrueType file
                tried.append(regular)
                continue
            if not (
                _covers_cyrillic(regular_font.face)
                and _covers_cyrillic(bold_font.face)
            ):
                tried.append(f"{regular} (no Cyrillic coverage)")
                continue
            if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(regular_font)
            if FONT_BOLD not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(bold_font)
            pdfmetrics.registerFontFamily(
                FONT_NAME,
                normal=FONT_NAME,
                bold=FONT_BOLD,
                italic=FONT_NAME,
                boldItalic=FONT_BOLD,
            )
            _fonts_registered = True
            return
        raise ReportFontError(
            "no Cyrillic TrueType font pair available; install Arial or "
            "fonts-dejavu (DejaVuSans.ttf + DejaVuSans-Bold.ttf) or set "
            "EARTHSCOPE_PDF_FONT / EARTHSCOPE_PDF_FONT_BOLD. Tried: "
            + ", ".join(tried or [path for pair in _font_candidates() for path in pair])
        )


# --- Number formatting ----------------------------------------------------
# Numbers use a dot decimal separator and comma group separator, which is what
# the API emits.  Rounding is display-only; no statistic is recomputed.
def _num(value: float | None, digits: int = 2) -> str:
    """Format a finite statistic, or an em dash when it is not available."""

    if value is None:
        return "—"
    if not math.isfinite(value):
        return "—"
    if value == 0:
        value = 0.0
    if abs(value) >= 1e7:
        return f"{value:.{digits}e}"
    if abs(value) < 10.0**-digits:
        # Do not print a nonzero estimate as "0.00"; keep its magnitude.
        return f"{value:.{max(digits, 2)}e}"
    return f"{value:,.{digits}f}"


def _p_text(p: float | None) -> str:
    if p is None:
        return "—"
    if p < 0.001:
        return "<0.001"
    return _num(p, 3)


def _ci_text(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "—"
    return f"[{_num(low)}; {_num(high)}]"


def _significant_text(significant: bool | None) -> str:
    if significant is True:
        return "да (p < 0,05)"
    if significant is False:
        return "нет (p ≥ 0,05)"
    return "инференция не выполнялась"


def _latitude(value: float) -> str:
    hemisphere = "с. ш." if value >= 0 else "ю. ш."
    return f"{_num(abs(value), 3)}° {hemisphere}"


def _longitude(value: float) -> str:
    hemisphere = "в. д." if value >= 0 else "з. д."
    return f"{_num(abs(value), 3)}° {hemisphere}"


def _clamp_text(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _year_ranges(years: Sequence[int]) -> str:
    """Compress a list of years into ``1989, 1991–1993`` style ranges."""

    if not years:
        return ""
    ordered = sorted(set(years))
    parts: list[str] = []
    start = previous = ordered[0]
    for year in ordered[1:]:
        if year == previous + 1:
            previous = year
            continue
        parts.append(f"{start}" if start == previous else f"{start}–{previous}")
        start = previous = year
    parts.append(f"{start}" if start == previous else f"{start}–{previous}")
    return ", ".join(parts)


_VARIABLE_LABEL = {
    "temperature": "температура воздуха (temperature, T2M)",
    "precipitation": "атмосферные осадки (precipitation, PRECTOTCORR)",
}

_AGGREGATION_LABEL = {
    "mean": "среднее суточных значений (mean)",
    "sum": "сумма суточных значений (sum)",
}

_SLOPE_UNIT = {
    "°C": "°C за 10 лет",
    "mm": "мм за 10 лет",
}

_ANOMALY_UNIT = {
    "°C": "°C",
    "mm": "мм",
}

_SEASON_LABEL = {
    "winter": "зима (winter, DJF)",
    "spring": "весна (spring, MAM)",
    "summer": "лето (summer, JJA)",
    "autumn": "осень (autumn, SON)",
}

_MONTH_LABEL = {
    f"m{month:02d}": f"{name} ({f'm{month:02d}'})"
    for month, name in enumerate(
        (
            "январь",
            "февраль",
            "март",
            "апрель",
            "май",
            "июнь",
            "июль",
            "август",
            "сентябрь",
            "октябрь",
            "ноябрь",
            "декабрь",
        ),
        start=1,
    )
}

_EVENT_LABEL = {
    "hot_days": "жаркие дни",
    "cold_nights": "холодные ночи",
    "heavy_rain": "сильные осадки",
}

_EVENT_DEFINITION = {
    "hot_days": "суточный максимум T2M_MAX строго выше порога",
    "cold_nights": "суточный минимум T2M_MIN строго ниже порога",
    "heavy_rain": "суточная сумма PRECTOTCORR строго выше порога",
}


def _period_name(period: str) -> str:
    return _SEASON_LABEL.get(period) or _MONTH_LABEL.get(period) or period


def _kind_title(kind: str) -> str:
    titles = {
        "trend": "Климатический тренд в точке",
        "compare": "Сравнение двух точек",
        "anomalies": "Аномалии относительно базового периода",
        "seasonal": "Сезонные и месячные ряды",
        "extremes": "Пороговые события",
        "world-cell": "Ячейка мировой карты трендов",
    }
    return titles[kind]


def _kind_subtitle(kind: str) -> str:
    subtitles = {
        "trend": "МНК-тренд годовых значений NASA POWER Daily Point (UTC)",
        "compare": "Описательное сравнение на общих полных годах",
        "anomalies": "Отклонения годовых значений от среднего базового периода",
        "seasonal": "Фиксированные сезоны Северного полушария и календарные месяцы",
        "extremes": "Строгие превышения абсолютного порога по полным годам",
        "world-cell": "Годовой ряд ячейки локального архива NASA POWER (LST)",
    }
    return subtitles[kind]


# --- Paragraph styles -----------------------------------------------------
@lru_cache(maxsize=1)
def _styles() -> dict[str, ParagraphStyle]:
    """Paragraph styles built once; they only reference font names, not files."""

    return {
        "brand": ParagraphStyle(
            "brand",
            fontName=FONT_BOLD,
            fontSize=8,
            leading=10,
            textColor=ACCENT,
        ),
        "title": ParagraphStyle(
            "title",
            fontName=FONT_BOLD,
            fontSize=17,
            leading=21,
            textColor=INK,
            spaceBefore=2,
            spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            fontName=FONT_NAME,
            fontSize=9.5,
            leading=13,
            textColor=MUTED,
            spaceAfter=2,
        ),
        "h2": ParagraphStyle(
            "h2",
            fontName=FONT_BOLD,
            fontSize=11.5,
            leading=15,
            textColor=ACCENT,
            spaceBefore=12,
            spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "h3",
            fontName=FONT_BOLD,
            fontSize=9.8,
            leading=13,
            textColor=INK,
            spaceBefore=8,
            spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "body",
            fontName=FONT_NAME,
            fontSize=9.2,
            leading=13.2,
            textColor=INK,
            spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "small",
            fontName=FONT_NAME,
            fontSize=7.9,
            leading=11,
            textColor=MUTED,
        ),
        "caption": ParagraphStyle(
            "caption",
            fontName=FONT_NAME,
            fontSize=8,
            leading=11,
            textColor=MUTED,
            spaceBefore=3,
            spaceAfter=2,
        ),
        "cell": ParagraphStyle(
            "cell",
            fontName=FONT_NAME,
            fontSize=8.2,
            leading=10.8,
            textColor=INK,
        ),
        "cell_bold": ParagraphStyle(
            "cell_bold",
            fontName=FONT_BOLD,
            fontSize=8.2,
            leading=10.8,
            textColor=INK,
        ),
        "cell_muted": ParagraphStyle(
            "cell_muted",
            fontName=FONT_NAME,
            fontSize=8.2,
            leading=10.8,
            textColor=MUTED,
        ),
        "head": ParagraphStyle(
            "head",
            fontName=FONT_BOLD,
            fontSize=8.2,
            leading=10.8,
            textColor=colors.white,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            fontName=FONT_NAME,
            fontSize=8.8,
            leading=12.6,
            leftIndent=11,
            firstLineIndent=-11,
            textColor=INK,
            spaceAfter=2,
        ),
        "mono": ParagraphStyle(
            "mono",
            fontName=FONT_NAME,
            fontSize=7.4,
            leading=9.6,
            textColor=MUTED,
            wordWrap="CJK",
        ),
    }


def _paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


def _link(key: str, text: str, style: ParagraphStyle) -> Paragraph:
    """Build a hyperlink from the constant whitelist only."""

    url = TRUSTED_LINKS[key]
    href = escape(url, {'"': "&quot;"})
    return Paragraph(
        f'<link href="{href}" color="#1f4e79">{escape(text)}</link>',
        style,
    )


# --- Charts ---------------------------------------------------------------
@dataclass(frozen=True)
class _Line:
    """One logical series; ``None`` values split the drawn polyline."""

    points: tuple[tuple[float, float | None], ...]
    color: Color
    dashed: bool = False
    marked: bool = True
    width: float = 1.3


def _segments(line: _Line) -> list[list[tuple[float, float]]]:
    segments: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for year, value in line.points:
        if value is None:
            if current:
                segments.append(current)
                current = []
            continue
        current.append((float(year), float(value)))
    if current:
        segments.append(current)
    return segments


def _nice_step(raw: float) -> float:
    if raw <= 0 or not math.isfinite(raw):
        return 1.0
    exponent = math.floor(math.log10(raw))
    base = 10.0**exponent
    for factor in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= factor * base:
            return factor * base
    return 10.0 * base


def _axis_digits(step: float) -> int:
    if step >= 1:
        return 0
    if step >= 0.1:
        return 1
    if step >= 0.01:
        return 2
    return 3


def _time_chart(
    lines: Sequence[_Line],
    *,
    caption: str,
    zero_line: bool = False,
) -> list[Any]:
    """Build a vector line chart plus its Russian caption."""

    years = [year for line in lines for year, _ in line.points]
    values = [
        value for line in lines for _, value in line.points if value is not None
    ]
    if not years or not values:
        return [_paragraph("Ряд не содержит значений для графика.", _styles()["small"])]

    x_min, x_max = min(years), max(years)
    if x_min == x_max:
        x_min, x_max = x_min - 1, x_max + 1
    if zero_line:
        values.append(0.0)
    low, high = min(values), max(values)
    if high == low:
        padding = abs(high) * 0.06 or 0.5
        low, high = low - padding, high + padding
    span = high - low
    low -= span * 0.10
    high += span * 0.12
    step = _nice_step((high - low) / 4.0)
    low = math.floor(low / step) * step
    high = math.ceil(high / step) * step
    if high <= low:
        high = low + step

    plot_width = CONTENT_WIDTH - 54.0
    drawing = Drawing(CONTENT_WIDTH, CHART_HEIGHT)
    chart = LinePlot()
    chart.x = 40.0
    chart.y = 30.0
    chart.width = plot_width
    chart.height = CHART_PLOT_HEIGHT
    chart.strokeColor = RULE
    chart.fillColor = colors.white
    chart.behindAxes = 0

    segments: list[list[tuple[float, float]]] = []
    owners: list[_Line] = []
    for line in lines:
        for segment in _segments(line):
            segments.append(segment)
            owners.append(line)
    marker = makeMarker("FilledCircle", size=1.9)
    for index, (segment, line) in enumerate(zip(segments, owners)):
        if line.marked:
            chart.lines[index].symbol = marker
        chart.lines[index].strokeColor = line.color
        chart.lines[index].strokeWidth = line.width
        if line.dashed:
            chart.lines[index].strokeDashArray = [3, 2]
        else:
            chart.lines[index].strokeDashArray = None
    chart.data = segments

    chart.xValueAxis.valueMin = float(x_min)
    chart.xValueAxis.valueMax = float(x_max)
    chart.xValueAxis.forceZero = 0
    chart.xValueAxis.strokeWidth = 0.6
    chart.xValueAxis.strokeColor = MUTED
    chart.xValueAxis.labels.fontName = FONT_NAME
    chart.xValueAxis.labels.fontSize = 7
    chart.xValueAxis.labels.fillColor = MUTED
    chart.xValueAxis.labelTextFormat = "%.0f"
    chart.xValueAxis.gridStrokeColor = RULE
    chart.xValueAxis.gridStrokeWidth = 0.4

    digits = _axis_digits(step)
    chart.yValueAxis.valueMin = float(low)
    chart.yValueAxis.valueMax = float(high)
    chart.yValueAxis.valueStep = float(step)
    chart.yValueAxis.forceZero = 0
    chart.yValueAxis.strokeWidth = 0.6
    chart.yValueAxis.strokeColor = MUTED
    chart.yValueAxis.labels.fontName = FONT_NAME
    chart.yValueAxis.labels.fontSize = 7
    chart.yValueAxis.labels.fillColor = MUTED
    chart.yValueAxis.labelTextFormat = lambda value: _num(value, digits)
    chart.yValueAxis.gridStrokeColor = RULE
    chart.yValueAxis.gridStrokeWidth = 0.4

    drawing.add(chart)
    style = _styles()
    return [
        drawing,
        Paragraph(escape(caption), style["caption"]),
    ]


def _legend(entries: Sequence[tuple[Color, str]]) -> Table:
    """Colour swatches with Russian labels (no chart legend dependency)."""

    style = _styles()
    swatch = TableStyle(
        [
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
    )
    data: list[list[Any]] = []
    for color, label in entries:
        data.append([Paragraph("", style["small"]), _paragraph(label, style["small"])])
    table = Table(data, colWidths=[12, None], hAlign="LEFT")
    table.setStyle(swatch)
    for index, (color, _) in enumerate(entries):
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, index), (0, index), color),
                    ("TOPPADDING", (0, index), (0, index), 3),
                    ("BOTTOMPADDING", (0, index), (0, index), 3),
                ]
            )
        )
    return table


# --- Tables ---------------------------------------------------------------
def _kv_table(rows: Sequence[tuple[str, str]], label_width: float = 152.0) -> Table:
    style = _styles()
    body: list[list[Any]] = []
    for label, value in rows:
        body.append(
            [
                _paragraph(label, style["cell_muted"]),
                _paragraph(value, style["cell"]),
            ]
        )
    table = Table(body, colWidths=[label_width, CONTENT_WIDTH - label_width])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, BAND]),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE),
            ]
        )
    )
    return table


def _grid_table(
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    widths: Sequence[float],
    *,
    align_right_from: int = 1,
) -> Table:
    style = _styles()
    body: list[list[Any]] = [
        [Paragraph(escape(text), style["head"]) for text in header]
    ]
    for row in rows:
        body.append([Paragraph(escape(text), style["cell"]) for text in row])
    table = Table(
        body,
        colWidths=list(widths),
        repeatRows=1,
        hAlign="LEFT",
        splitByRow=1,
    )
    commands: list[tuple[Any, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE),
        ("ALIGN", (align_right_from, 1), (-1, -1), "RIGHT"),
    ]
    table.setStyle(TableStyle(commands))
    return table


def _bullets(items: Sequence[str]) -> list[Paragraph]:
    style = _styles()["bullet"]
    return [Paragraph(f"—\u00a0{escape(item)}", style) for item in items]


# --- Shared blocks --------------------------------------------------------
def _masthead(kind: str) -> list[Any]:
    style = _styles()
    masthead: list[Any] = [
        Paragraph("EARTHSCOPE · NASA SPACE APPS CHALLENGE 2026", style["brand"]),
        Paragraph(escape(_kind_title(kind)), style["title"]),
        Paragraph(escape(_kind_subtitle(kind)), style["subtitle"]),
        HRFlowable(
            width="100%",
            thickness=1.1,
            color=ACCENT,
            spaceBefore=3,
            spaceAfter=9,
        ),
    ]
    return masthead


def _source_block(
    *,
    name: str,
    parameter: str,
    dataset: str,
    daily_unit: str,
    time_standard: str,
    retrieved_at: str,
    cached: bool,
    request_url: str | None,
    link_key: str,
    link_label: str,
) -> list[Any]:
    style = _styles()
    block: list[Any] = [
        Paragraph("Данные и источник", style["h2"]),
        _kv_table(
            [
                ("Источник", name),
                ("Параметр POWER", parameter),
                ("Набор данных", dataset),
                ("Единица параметра", daily_unit),
                ("Стандарт времени", time_standard),
                ("Получено (UTC)", retrieved_at),
                ("Ответ из кэша", "да" if cached else "нет"),
            ]
        ),
        Spacer(1, 4),
        _link(link_key, link_label, style["small"]),
        Spacer(1, 3),
    ]
    if request_url:
        block.extend(
            [
                Paragraph("Точный запрос к POWER (текст, без перехода по ссылке):", style["small"]),
                Paragraph(escape(_clamp_text(request_url, 300)), style["mono"]),
            ]
        )
    return block


def _warnings_block(warnings: Sequence[str]) -> list[Any]:
    if not warnings:
        return []
    style = _styles()
    shown = list(warnings[:10])
    block: list[Any] = [
        Paragraph("Предупреждения API (без перевода)", style["h2"]),
        Paragraph(
            escape(
                "Сервер вернул эти предупреждения вместе с данными; они приведены "
                "дословно и не являются оценкой отчёта."
            ),
            style["body"],
        ),
    ]
    block.extend(_bullets([_clamp_text(text, 400) for text in shown]))
    if len(warnings) > len(shown):
        block.append(
            Paragraph(
                escape(f"… и ещё {len(warnings) - len(shown)} предупреждений."),
                style["small"],
            )
        )
    return block


_COMMON_LIMITS: tuple[str, ...] = (
    "Значения NASA POWER — оценка реанализа MERRA-2 в узле сетки, а не измерение "
    "на метеостанции, не среднее по городу и не данные наблюдений.",
    "Оценка относится только к выбранной точке и периоду; она не является "
    "прогнозом и не доказывает причинно-следственную связь.",
    "Годы без всех календарных суток исключаются; пропуски не заполняются и не "
    "интерполируются, поэтому число полных лет может быть меньше длины периода.",
    "МНК-прямая предполагает линейный тренд и независимые остатки с постоянной "
    "дисперсией; автокорреляция, разрывы и нелинейность могут исказить интервал "
    "и p-значение.",
    "Повторный выбор точек, периодов, сезонов и порогов создаёт множественные "
    "сравнения; p-значение в этом отчёте не корректируется, если это не указано "
    "явно.",
)

_KIND_LIMITS: dict[str, tuple[str, ...]] = {
    "trend": (
        "Наклон приведён за 10 лет; он не пересчитывается в отчёте и относится "
        "только к показанному ряду.",
        "p ≥ 0,05 означает недостаточно доказательств ненулевого тренда, а не "
        "доказательство отсутствия изменений.",
    ),
    "compare": (
        "Разница наклонов описательная: 95 % интервал и p-значение для неё не "
        "рассчитываются, и отчёт их не показывает.",
        "Сравнение двух отдельных p-значений не является статистическим тестом "
        "различия между точками.",
        "Разница использует только годы, полные в обеих точках; отдельные тренды "
        "могут опираться на разные наборы лет.",
    ),
    "anomalies": (
        "Базовое среднее — простое среднее полных базовых лет; короткий "
        "пользовательский период не является климатической нормой.",
        "Аномалии — разности в °C или мм, а не проценты, не индексы и не оценки "
        "тренда.",
        "При неполном хотя бы одном базовом году базовое среднее и все аномалии "
        "подавляются, а не заполняются.",
    ),
    "seasonal": (
        "Сезоны фиксированы для Северного полушария: зима DJF, весна MAM, лето "
        "JJA, осень SON; в других регионах они не описывают местные сезоны.",
        "Зима помечена годом января: зима 2020 включает декабрь 2019; зима 1981 "
        "не может быть полной из-за начала покрытия POWER.",
        "Каждый сезон и месяц проверяется отдельно, без поправки на "
        "множественные сравнения и без учёта автокорреляции.",
    ),
    "extremes": (
        "Порог — абсолютный, выбранный пользователем, а не определение NASA или "
        "ВМО; один и тот же порог означает разное в разных климатах.",
        "Неравенство строгое: значение, равное порогу, событием не считается.",
        "Частота нормирована на 365 дней, чтобы високосные годы были сравнимы; "
        "наклон выражен в днях событий за 365 дней за 10 лет.",
        "HAC-инференция (Бартлетт, Ньюи–Уэст) приблизительна, особенно вблизи "
        "минимальной длины ряда, и не исправляет нелинейность, разрывы и "
        "повторный перебор порогов.",
    ),
    "world-cell": (
        "Показаны только годовой ряд ячейки и уже рассчитанные для неё "
        "статистики; отчёт не пересчитывает наклон, интервал, p и q.",
        "Ячейка — элемент сетки архива; её прямоугольник выводится из шага сетки "
        "и не является границей NASA или административной единицей.",
        "q — p-значение с поправкой Бенамини–Йекутиели по всей карте для этого "
        "параметра (порог q ≤ 0,10); поправка снижает ложные находки, но не "
        "исправляет ошибку временной модели.",
        "Соседние ячейки пространственно коррелированы; q ≤ 0,10 не является "
        "одновременным интервалом для всей карты.",
        "Архив использует местное солнечное время (LST) и не сопоставим напрямую "
        "с суточными рядами UTC из точечных отчётов.",
    ),
}


def _limits_block(kind: str) -> list[Any]:
    style = _styles()
    block: list[Any] = [Paragraph("Ограничения и как читать отчёт", style["h2"])]
    block.extend(_bullets(_KIND_LIMITS[kind]))
    block.extend(_bullets(_COMMON_LIMITS))
    return block


def _sources_block(kind: str) -> list[Any]:
    style = _styles()
    entries: list[tuple[str, str]] = [
        ("power_daily", "NASA POWER Daily Point API (UTC) — описание параметров"),
        ("power_monthly", "NASA POWER Monthly/Annual API (LST) — региональные ячейки"),
        ("power_registry", "NASA POWER на реестре открытых данных AWS"),
    ]
    if kind in {"anomalies", "seasonal"}:
        entries.append(
            ("wmo_normals", "WMO Climate Normals (NCEI) — даты опорного периода")
        )
    block: list[Any] = [Paragraph("Проверенные ссылки", style["h2"])]
    for key, label in entries:
        block.append(_link(key, label, style["body"]))
        block.append(Paragraph(escape(TRUSTED_LINKS[key]), style["mono"]))
    block.append(
        Paragraph(
            escape(
                "Ссылки ограничены этим статическим списком; адреса из ответа API "
                "показаны как текст и не открываются по нажатию."
            ),
            style["small"],
        )
    )
    return block


def _footer_note() -> list[Any]:
    style = _styles()
    return [
        Spacer(1, 6),
        Paragraph(
            escape(
                "Отчёт сформирован автоматически из проверенного ответа API "
                "EarthScope. Интерфейс отчёта на русском языке; предупреждения API "
                "приведены без перевода. Числа округлены только для отображения."
            ),
            style["small"],
        ),
    ]


def _trend_rows(trend: TrendStats | None, unit: str) -> list[tuple[str, str]]:
    if trend is None:
        return [
            ("Тренд", "не рассчитан: менее 5 полных лет"),
            ("Наклон", "—"),
            ("95 % интервал", "—"),
            ("p-значение", "—"),
            ("Значимость", "—"),
            ("R²", "—"),
        ]
    rows = [
        ("Полных лет в тренде", str(trend.years_used)),
        ("Наклон", f"{_num(trend.per_decade)} {_SLOPE_UNIT.get(unit, 'за 10 лет')}"),
        ("95 % интервал наклона", _ci_text(trend.ci95_low, trend.ci95_high)),
        ("p-значение (двустороннее)", _p_text(trend.p_value)),
        ("Значимый тренд (α = 0,05)", _significant_text(trend.significant)),
        ("R²", _num(trend.r_squared, 3)),
    ]
    return rows


def _trend_footnote(trend: TrendStats | None) -> list[Any]:
    style = _styles()
    notes: list[Any] = []
    if trend is not None and trend.p_value == 0:
        notes.append(
            Paragraph(
                escape(
                    "p = 0 — предел при нулевой остаточной дисперсии (точное "
                    "совпадение с прямой), а не доказательство бесконечной "
                    "точности."
                ),
                style["small"],
            )
        )
    if trend is not None and trend.significant is False:
        notes.append(
            Paragraph(
                escape(
                    "p ≥ 0,05: недостаточно доказательств ненулевого тренда. Это не "
                    "доказывает, что климат не менялся."
                ),
                style["small"],
            )
        )
    return notes


def _detective_block(kind: str, model: Any) -> list[Any]:
    """Short template narration used by the ``X-EarthScope-Report: detective`` route.

    It restates validated fields only: the estimated sign, the exact p-value and
    the strict threshold, plus the caveat that this is not proof of absence or
    cause.  Nothing is recomputed.
    """

    style = _styles()
    lines: list[str] = []
    if kind == "trend":
        trend = model.trend
        if trend is None:
            lines.append(
                "Тренд не рассчитан: полных лет меньше пяти, поэтому направление "
                "изменения по этому ряду не определяется."
            )
        else:
            if trend.per_decade > 0:
                direction = "положительный (значения растут)"
            elif trend.per_decade < 0:
                direction = "отрицательный (значения убывают)"
            else:
                direction = "нулевой по точечной оценке"
            lines.append(
                f"Наклон {direction}: {_num(trend.per_decade)} "
                f"{_SLOPE_UNIT.get(model.unit, 'за 10 лет')} по {trend.years_used} "
                "полным годам."
            )
            if trend.significant:
                lines.append(
                    f"p = {_p_text(trend.p_value)} < 0.05: при допущениях МНК данных "
                    "достаточно для ненулевого линейного тренда; это не доказывает "
                    "причину изменений."
                )
            else:
                lines.append(
                    f"p = {_p_text(trend.p_value)} ≥ 0.05: доказательств ненулевого "
                    "тренда недостаточно. Это не доказывает, что изменений нет."
                )
    elif kind == "compare":
        difference = model.comparison.slope_difference_per_decade
        if difference is None:
            lines.append(
                "Разница наклонов не рассчитана: общих полных лет меньше пяти."
            )
        else:
            relation = (
                "быстрее в точке A"
                if difference > 0
                else "быстрее в точке B"
                if difference < 0
                else "одинаково по точечной оценке"
            )
            lines.append(
                f"A − B = {_num(difference)} "
                f"{_SLOPE_UNIT.get(model.point_a.unit, 'за 10 лет')}: по знаку "
                f"изменение {relation}, на {model.comparison.years_used} общих "
                "полных годах."
            )
        lines.append(
            "Это описательная разница: интервал и p-значение для неё не "
            "рассчитываются, а сравнение двух отдельных p-значений не является "
            "тестом различия."
        )
    elif kind == "anomalies":
        if model.baseline_mean is None:
            lines.append(
                "Базовое среднее не рассчитано: хотя бы один базовый год неполный, "
                "поэтому аномалии отсутствуют."
            )
        else:
            lines.append(
                f"База {model.baseline_start_year}–{model.baseline_end_year}: "
                f"среднее {_num(model.baseline_mean)} {model.unit} по "
                f"{model.baseline_complete_years} полным годам."
            )
        lines.append(
            "Аномалия — разность годового значения и базового среднего; это не "
            "тренд, не процент и не норма ВМО, если период короче 30 лет."
        )
    elif kind == "seasonal":
        for entry in model.periods:
            if entry.trend is None:
                lines.append(
                    f"{_period_name(entry.period)}: тренд не рассчитан (меньше пяти "
                    "полных окон)."
                )
                continue
            direction = (
                "рост"
                if entry.trend.per_decade > 0
                else "снижение"
                if entry.trend.per_decade < 0
                else "нулевая оценка"
            )
            lines.append(
                f"{_period_name(entry.period)}: {direction}, "
                f"{_num(entry.trend.per_decade)} {model.unit} за 10 лет, "
                f"p = {_p_text(entry.trend.p_value)}."
            )
        lines.append(
            "Сезоны и месяцы проверяются отдельно, без поправки на множественные "
            "сравнения; ранжирование наклонов не является тестом различий."
        )
    elif kind == "extremes":
        trend = model.trend
        lines.append(
            f"Событие «{_EVENT_LABEL[model.event]}» при пороге "
            f"{_num(model.threshold)} {model.threshold_unit} "
            f"(оператор {model.operator}, строгое неравенство)."
        )
        if trend is None:
            lines.append(
                "Тренд частоты не рассчитан: полных лет меньше пяти."
            )
        else:
            direction = (
                "частота растёт"
                if trend.per_decade > 0
                else "частота снижается"
                if trend.per_decade < 0
                else "частота без изменения по точечной оценке"
            )
            lines.append(
                f"{direction}: {_num(trend.per_decade)} дней на 365 суток за 10 лет "
                f"по {trend.years_used} полным годам."
            )
            if trend.p_value is None:
                lines.append(
                    "Инференция не выполнялась: HAC-интервал и p-значение не "
                    "рассчитаны, наклон остаётся описательным."
                )
            elif trend.p_value < 0.05:
                lines.append(
                    f"p = {_p_text(trend.p_value)} < 0.05 при HAC(3): интервал "
                    "исключает ноль при допущениях метода; это не прогноз."
                )
            else:
                lines.append(
                    f"p = {_p_text(trend.p_value)} ≥ 0.05: доказательств изменения "
                    "частоты недостаточно."
                )
    else:
        if model.slope is None:
            lines.append(
                "Наклон ячейки не определён: для этого ряда нет пригодной оценки."
            )
        else:
            flag = (
                "не рассчитан"
                if model.q is None
                else ("выполнен" if model.q <= 0.10 else "не выполнен")
            )
            lines.append(
                f"Наклон ячейки {_num(model.slope)} "
                f"{_SLOPE_UNIT.get(model.unit, 'за 10 лет')}; q = "
                f"{_p_text(model.q)}; порог q ≤ 0.10 {flag}."
            )
        lines.append(
            "Показаны только переданные ряд и статистики; поправка BY снижает "
            "ложные находки, но не доказывает причину и не является прогнозом."
        )

    block: list[Any] = [
        Paragraph("Режим «Детектив»", style["h2"]),
        Paragraph(
            escape(
                "Шаблонный текст по полям проверенного ответа; числа не "
                "пересчитываются."
            ),
            style["small"],
        ),
    ]
    block.extend(_bullets(lines))
    block.append(Spacer(1, 2))
    return block


# --- Per-kind story builders ---------------------------------------------
def _story_trend(model: TrendResponse) -> list[Any]:
    style = _styles()
    complete = [point.year for point in model.series if point.value is not None]
    missing = [point.year for point in model.series if point.value is None]
    story: list[Any] = _masthead("trend")
    story.append(
        _kv_table(
            [
                ("Широта", _latitude(model.latitude)),
                ("Долгота", _longitude(model.longitude)),
                ("Переменная", f"{_VARIABLE_LABEL[model.variable]} ({model.variable})"),
                ("Период", f"{model.start_year}–{model.end_year}"),
                ("Единица", model.unit),
                ("Агрегация", f"{_AGGREGATION_LABEL[model.aggregation]} ({model.aggregation})"),
                ("Полных лет", f"{len(complete)} из {len(model.series)}"),
            ]
        )
    )
    story.append(Paragraph("Ход ряда и тренд", style["h2"]))
    chart = _time_chart(
        [
            _Line(
                tuple((point.year, point.value) for point in model.series),
                SERIES_BLUE,
            ),
            _Line(
                tuple((point.year, point.fitted) for point in model.series),
                SERIES_ORANGE,
                dashed=True,
                marked=False,
            ),
        ],
        caption=(
            "Рис. 1. Годовые значения (синяя линия) и МНК-прямая (оранжевый "
            "пунктир). Пропущенные годы не соединяются. Ось X — календарный год, "
            f"ось Y — {model.unit}."
        ),
    )
    story.extend(chart)
    story.append(_legend([(SERIES_BLUE, "годовые значения"), (SERIES_ORANGE, "линейный тренд")]))
    story.append(Paragraph("Статистика", style["h2"]))
    story.append(_kv_table(_trend_rows(model.trend, model.unit)))
    story.extend(_trend_footnote(model.trend))
    if missing:
        story.append(
            Paragraph(
                escape(
                    "Неполные годы исключены из тренда: "
                    f"{_year_ranges(missing)}."
                ),
                style["small"],
            )
        )

    story.append(Paragraph("Годовые значения", style["h2"]))
    rows = [
        [
            str(point.year),
            _num(point.value),
            _num(point.fitted),
            f"{point.valid_days}/{point.expected_days}",
        ]
        for point in model.series
    ]
    story.append(
        _grid_table(
            ("Год", f"Значение, {model.unit}", "МНК-оценка", "Суток"),
            rows,
            (62, 150, 150, CONTENT_WIDTH - 362),
        )
    )
    story.extend(
        _warnings_block(model.warnings)
        + _source_block(
            name=model.source.name,
            parameter=model.source.parameter,
            dataset=model.source.dataset,
            daily_unit=model.source.daily_unit,
            time_standard=model.source.time_standard,
            retrieved_at=model.source.retrieved_at,
            cached=model.source.cached,
            request_url=model.source.request_url,
            link_key="power_daily",
            link_label="Документация NASA POWER Daily API",
        )
        + _limits_block("trend")
        + _sources_block("trend")
        + _footer_note()
    )
    return story


def _story_compare(model: CompareResponse) -> list[Any]:
    style = _styles()
    point_a, point_b = model.point_a, model.point_b
    years_a = {point.year: point.value for point in point_a.series}
    years_b = {point.year: point.value for point in point_b.series}
    all_years = sorted(set(years_a) | set(years_b))
    story: list[Any] = _masthead("compare")
    story.append(
        _kv_table(
            [
                ("Точка A", f"{_latitude(point_a.latitude)}, {_longitude(point_a.longitude)}"),
                ("Точка B", f"{_latitude(point_b.latitude)}, {_longitude(point_b.longitude)}"),
                ("Переменная", _VARIABLE_LABEL[point_a.variable]),
                ("Период", f"{point_a.start_year}–{point_a.end_year}"),
                ("Единица", point_a.unit),
                ("Общих полных лет", str(model.comparison.years_used)),
                ("Описательный метод (Descriptive)", model.comparison.method),
            ]
        )
    )
    story.append(Paragraph("Ряды двух точек", style["h2"]))
    story.extend(
        _time_chart(
            [
                _Line(
                    tuple((point.year, point.value) for point in point_a.series),
                    SERIES_BLUE,
                ),
                _Line(
                    tuple((point.year, point.value) for point in point_b.series),
                    SERIES_GREEN,
                ),
            ],
            caption=(
                "Рис. 1. Годовые значения точки A (синяя) и точки B (зелёная). "
                "Пропуски не соединяются; обе линии в одной единице, потому что "
                "переменная и период общие."
            ),
        )
    )
    story.append(_legend([(SERIES_BLUE, "точка A"), (SERIES_GREEN, "точка B")]))
    story.append(Paragraph("Тренды каждой точки", style["h2"]))
    rows = []
    trend_a = _dict(_trend_rows(point_a.trend, point_a.unit))
    trend_b = _dict(_trend_rows(point_b.trend, point_b.unit))
    labels = [
        "Полных лет в тренде",
        "Наклон",
        "95 % интервал наклона",
        "p-значение (двустороннее)",
        "Значимый тренд (α = 0,05)",
        "R²",
    ]
    for label in labels:
        rows.append([label, trend_a.get(label, "—"), trend_b.get(label, "—")])
    story.append(
        _grid_table(
            ("Показатель", "Точка A", "Точка B"),
            rows,
            (150, 177, 177),
            align_right_from=1,
        )
    )
    story.extend(_trend_footnote(point_a.trend) + _trend_footnote(point_b.trend))

    story.append(Paragraph("Разница A − B на общих годах", style["h2"]))
    difference = model.comparison.slope_difference_per_decade
    story.append(
        _kv_table(
            [
                (
                    "Метод",
                    f"{model.comparison.method} (Descriptive, без инференции)",
                ),
                ("Общих полных лет", str(model.comparison.years_used)),
                (
                    "Наклон разности",
                    "—"
                    if difference is None
                    else f"{_num(difference)} {_SLOPE_UNIT.get(point_a.unit, 'за 10 лет')}",
                ),
                ("95 % интервал", "не рассчитывается"),
                ("p-значение", "не рассчитывается"),
                ("Инференция", "нет, результат описательный"),
            ]
        )
    )
    story.append(Paragraph(escape(_clamp_text(model.comparison.note, 400)), style["small"]))

    story.append(Paragraph("Годовые значения", style["h2"]))
    series_rows = [
        [str(year), _num(years_a.get(year)), _num(years_b.get(year))]
        for year in all_years
    ]
    story.append(
        _grid_table(
            ("Год", f"A, {point_a.unit}", f"B, {point_b.unit}"),
            series_rows,
            (62, 130, 130),
        )
    )
    story.extend(
        _warnings_block(
            tuple(dict.fromkeys([*point_a.warnings, *point_b.warnings]))
        )
        + _source_block(
            name=point_a.source.name,
            parameter=point_a.source.parameter,
            dataset=point_a.source.dataset,
            daily_unit=point_a.source.daily_unit,
            time_standard=point_a.source.time_standard,
            retrieved_at=point_a.source.retrieved_at,
            cached=point_a.source.cached,
            request_url=point_a.source.request_url,
            link_key="power_daily",
            link_label="Документация NASA POWER Daily API",
        )
    )
    if point_b.source.request_url != point_a.source.request_url:
        story.append(
            Paragraph(
                escape("Запрос для точки B (текст, без перехода по ссылке):"),
                style["small"],
            )
        )
        story.append(
            Paragraph(
                escape(_clamp_text(point_b.source.request_url, 300)), style["mono"]
            )
        )
        if point_b.source.retrieved_at != point_a.source.retrieved_at:
            story.append(
                Paragraph(
                    escape(f"Точка B получена (UTC): {point_b.source.retrieved_at}"),
                    style["small"],
                )
            )
    story.extend(_limits_block("compare") + _sources_block("compare") + _footer_note())
    return story


def _dict(rows: Sequence[tuple[str, str]]) -> dict[str, str]:
    return {label: value for label, value in rows}


def _story_anomalies(model: AnomalyResponse) -> list[Any]:
    style = _styles()
    baseline_label = (
        f"{model.baseline_start_year}–{model.baseline_end_year}"
        f" ({model.baseline_complete_years} полных лет)"
    )
    story: list[Any] = _masthead("anomalies")
    story.append(
        _kv_table(
            [
                ("Широта", _latitude(model.latitude)),
                ("Долгота", _longitude(model.longitude)),
                ("Переменная", _VARIABLE_LABEL[model.variable]),
                ("Период", f"{model.start_year}–{model.end_year}"),
                ("Базовый период", baseline_label),
                (
                    "Базовое среднее",
                    "—"
                    if model.baseline_mean is None
                    else f"{_num(model.baseline_mean)} {model.unit}",
                ),
                ("Единица", model.unit),
                ("Агрегация", _AGGREGATION_LABEL[model.aggregation]),
            ]
        )
    )
    if model.baseline_mean is None:
        story.append(
            Paragraph(
                escape(
                    "Базовое среднее подавлено: хотя бы один базовый год неполный. "
                    "Аномалии поэтому не рассчитываются и в таблице отмечены «—»."
                ),
                style["body"],
            )
        )
    story.append(Paragraph("Аномалии по годам", style["h2"]))
    story.extend(
        _time_chart(
            [
                _Line(
                    tuple((point.year, point.anomaly) for point in model.series),
                    SERIES_BLUE,
                )
            ],
            caption=(
                "Рис. 1. Отклонение годового значения от базового среднего; "
                f"единица — {_ANOMALY_UNIT.get(model.unit, model.unit)}. "
                "Серая пунктирная линия — ноль."
            ),
            zero_line=True,
        )
    )
    story.append(Paragraph("Годовые значения и аномалии", style["h2"]))
    rows = [
        [
            str(point.year),
            _num(point.value),
            _num(point.anomaly),
            f"{point.valid_days}/{point.expected_days}",
        ]
        for point in model.series
    ]
    story.append(
        _grid_table(
            ("Год", f"Значение, {model.unit}", f"Аномалия, {model.unit}", "Суток"),
            rows,
            (62, 146, 146, CONTENT_WIDTH - 354),
        )
    )
    story.extend(
        _warnings_block(model.warnings)
        + _source_block(
            name=model.source.name,
            parameter=model.source.parameter,
            dataset=model.source.dataset,
            daily_unit=model.source.daily_unit,
            time_standard=model.source.time_standard,
            retrieved_at=model.source.retrieved_at,
            cached=model.source.cached,
            request_url=model.source.request_url,
            link_key="power_daily",
            link_label="Документация NASA POWER Daily API",
        )
        + _limits_block("anomalies")
        + _sources_block("anomalies")
        + _footer_note()
    )
    return story


def _story_seasonal(model: SeasonalResponse) -> list[Any]:
    style = _styles()
    story: list[Any] = _masthead("seasonal")
    story.append(
        _kv_table(
            [
                ("Широта", _latitude(model.latitude)),
                ("Долгота", _longitude(model.longitude)),
                ("Переменная", _VARIABLE_LABEL[model.variable]),
                ("Период", f"{model.start_year}–{model.end_year}"),
                ("Запрошенный период", _period_name(model.period)),
                ("Единица", model.unit),
                ("Агрегация", _AGGREGATION_LABEL[model.aggregation]),
                ("Периодов в отчёте", str(len(model.periods))),
            ]
        )
    )
    story.append(Paragraph("Ряды по периодам", style["h2"]))
    lines: list[_Line] = []
    legend: list[tuple[Color, str]] = []
    for index, entry in enumerate(model.periods):
        color = SERIES_PALETTE[index % len(SERIES_PALETTE)]
        lines.append(
            _Line(
                tuple((point.year, point.value) for point in entry.series),
                color,
            )
        )
        legend.append((color, f"{_period_name(entry.period)} ({entry.period})"))
    story.extend(
        _time_chart(
            lines,
            caption=(
                "Рис. 1. Годовые агрегаты по выбранным периодам. Окно без всех "
                "календарных суток остаётся пропуском и не соединяется."
            ),
        )
    )
    story.append(_legend(legend))

    story.append(Paragraph("Тренды по периодам", style["h2"]))
    rows: list[list[str]] = []
    for entry in model.periods:
        trend = entry.trend
        complete = sum(1 for point in entry.series if point.value is not None)
        rows.append(
            [
                _period_name(entry.period),
                f"{complete} из {len(entry.series)}",
                "—" if trend is None else _num(trend.per_decade),
                "—" if trend is None else _ci_text(trend.ci95_low, trend.ci95_high),
                "—" if trend is None else _p_text(trend.p_value),
                "—" if trend is None else _num(trend.r_squared, 3),
                "—" if trend is None else _significant_text(trend.significant),
            ]
        )
    story.append(
        _grid_table(
            (
                "Период",
                "Полных окон",
                "Наклон",
                "95 % интервал",
                "p",
                "R²",
                "α = 0,05",
            ),
            rows,
            (84, 62, 62, 118, 50, 44, CONTENT_WIDTH - 420),
        )
    )
    story.append(
        Paragraph(
            escape(
                "Наклоны приведены в единицах показателя за 10 лет. Ранжирование "
                "по величине наклона не является формальным тестом различий."
            ),
            style["small"],
        )
    )

    if len(model.periods) == 1:
        entry = model.periods[0]
        story.append(Paragraph("Годовые значения периода", style["h2"]))
        rows = [
            [
                str(point.year),
                _num(point.value),
                _num(point.fitted),
                f"{point.valid_days}/{point.expected_days}",
            ]
            for point in entry.series
        ]
        story.append(
            _grid_table(
                ("Год", f"Значение, {model.unit}", "МНК-оценка", "Суток"),
                rows,
                (62, 150, 150, CONTENT_WIDTH - 362),
            )
        )
    else:
        story.append(
            Paragraph(
                escape(
                    "Полные годовые ряды каждого периода доступны в JSON-ответе "
                    "API; отчёт показывает их сводку и график."
                ),
                style["small"],
            )
        )

    story.extend(
        _warnings_block(model.warnings)
        + _source_block(
            name=model.source.name,
            parameter=model.source.parameter,
            dataset=model.source.dataset,
            daily_unit=model.source.daily_unit,
            time_standard=model.source.time_standard,
            retrieved_at=model.source.retrieved_at,
            cached=model.source.cached,
            request_url=model.source.request_url,
            link_key="power_daily",
            link_label="Документация NASA POWER Daily API",
        )
        + _limits_block("seasonal")
        + _sources_block("seasonal")
        + _footer_note()
    )
    return story


def _story_extremes(model: ExtremesResponse) -> list[Any]:
    style = _styles()
    rates = [
        (
            point.year,
            None if point.fraction is None else point.fraction * 365.0,
        )
        for point in model.series
    ]
    fitted = [
        (
            point.year,
            None if point.fitted is None else point.fitted * 365.0,
        )
        for point in model.series
    ]
    incomplete = [point.year for point in model.series if point.events is None]
    story: list[Any] = _masthead("extremes")
    story.append(
        _kv_table(
            [
                ("Широта", _latitude(model.latitude)),
                ("Долгота", _longitude(model.longitude)),
                ("Событие", f"{_EVENT_LABEL[model.event]} ({model.event})"),
                ("Определение", _EVENT_DEFINITION[model.event]),
                (
                    "Порог",
                    f"{_num(model.threshold)} {model.threshold_unit} "
                    f"(оператор {model.operator})",
                ),
                ("Период", f"{model.start_year}–{model.end_year}"),
                ("Сутки", "UTC, календарные сутки POWER"),
            ]
        )
    )
    story.append(Paragraph("Частота событий", style["h2"]))
    story.extend(
        _time_chart(
            [
                _Line(tuple(rates), SERIES_BLUE),
                _Line(tuple(fitted), SERIES_ORANGE, dashed=True, marked=False),
            ],
            caption=(
                "Рис. 1. Число дней событий на 365 суток за год (синяя) и "
                "HAC-прямая по этим частотам (оранжевый пунктир). Неполные годы "
                "не имеют значения и не соединяются."
            ),
        )
    )
    story.append(
        _legend(
            [
                (SERIES_BLUE, "частота, дней на 365 суток"),
                (SERIES_ORANGE, "линейная оценка частоты"),
            ]
        )
    )

    story.append(Paragraph("Статистика тренда частоты", style["h2"]))
    trend = model.trend
    if trend is None:
        story.append(
            _kv_table(
                [
                    ("Тренд", "не рассчитан: менее 5 полных лет"),
                    ("Наклон", "—"),
                    ("95 % интервал", "—"),
                    ("p-значение", "—"),
                ]
            )
        )
    else:
        story.append(
            _kv_table(
                [
                    ("Полных лет", str(trend.years_used)),
                    (
                        "Наклон частоты",
                        f"{_num(trend.per_decade)} дней на 365 суток за 10 лет",
                    ),
                    ("Наклон числа событий", f"{_num(trend.count_per_decade)} событий за 10 лет"),
                    ("95 % интервал (HAC 3 лага)", _ci_text(trend.ci95_low, trend.ci95_high)),
                    ("p-значение (HAC 3 лага)", _p_text(trend.p_value)),
                    ("Значимый (α = 0,05)", _significant_text(trend.significant)),
                    (
                        "Автокорреляция остатков (лаг 1)",
                        "—" if trend.residual_lag1 is None else _num(trend.residual_lag1, 3),
                    ),
                ]
            )
        )
        if trend.sensitivity:
            story.append(Paragraph("Чувствительность к методу и лагам", style["h3"]))
            rows = [
                [
                    "МНК" if item.method == "ols" else "HAC",
                    "—" if item.lags is None else str(item.lags),
                    _ci_text(item.ci95_low, item.ci95_high),
                    _p_text(item.p_value),
                ]
                for item in trend.sensitivity
            ]
            story.append(
                _grid_table(
                    ("Метод", "Лаги, лет", "95 % интервал", "p"),
                    rows,
                    (86, 62, 170, 90),
                    align_right_from=1,
                )
            )
        if trend.p_value is not None and trend.p_value >= 0.05:
            story.append(
                Paragraph(
                    escape(
                        "p ≥ 0,05: недостаточно доказательств изменения частоты; это "
                        "не доказывает постоянство."
                    ),
                    style["small"],
                )
            )

    if incomplete:
        story.append(
            Paragraph(
                escape(
                    "Неполные годы (без значений событий): "
                    f"{_year_ranges(incomplete)}."
                ),
                style["small"],
            )
        )

    story.append(Paragraph("Годовые значения", style["h2"]))
    rows = [
        [
            str(point.year),
            "—" if point.events is None else str(point.events),
            "—" if point.fraction is None else _num(point.fraction * 365.0, 1),
            f"{point.valid_days}/{point.expected_days}",
            str(point.excluded_days),
        ]
        for point in model.series
    ]
    story.append(
        _grid_table(
            ("Год", "Событий", "Дней на 365", "Суток", "Исключено"),
            rows,
            (62, 78, 108, 110, CONTENT_WIDTH - 358),
        )
    )
    story.extend(
        _warnings_block(model.warnings)
        + _source_block(
            name=model.source.name,
            parameter=model.source.parameter,
            dataset=model.source.dataset,
            daily_unit=model.source.daily_unit,
            time_standard=model.source.time_standard,
            retrieved_at=model.source.retrieved_at,
            cached=model.source.cached,
            request_url=model.source.request_url,
            link_key="power_daily",
            link_label="Документация NASA POWER Daily API",
        )
        + _limits_block("extremes")
        + _sources_block("extremes")
        + _footer_note()
    )
    return story


def _story_world_cell(model: ResearchReport) -> list[Any]:
    style = _styles()
    years = [point.year for point in model.series]
    missing = [point.year for point in model.series if point.value is None]
    story: list[Any] = _masthead("world-cell")
    story.append(
        _kv_table(
            [
                ("Широта центра ячейки", _latitude(model.latitude)),
                ("Долгота центра ячейки", _longitude(model.longitude)),
                ("Параметр POWER", model.parameter),
                ("Единица", model.unit),
                ("Период архива", f"{min(years)}–{max(years)}"),
                ("Лет в ряду", f"{len(years) - len(missing)} из {len(years)}"),
                ("Версия архива", model.version),
                (
                    "Сетка",
                    f"{_num(_WORLD_LAT_STEP, 3)}° по широте × "
                    f"{_num(_WORLD_LON_STEP, 3)}° по долготе",
                ),
            ]
        )
    )
    story.append(Paragraph("Годовой ряд ячейки", style["h2"]))
    story.extend(
        _time_chart(
            [
                _Line(
                    tuple((point.year, point.value) for point in model.series),
                    SERIES_BLUE,
                )
            ],
            caption=(
                "Рис. 1. Значения ячейки архива по годам; отчёт воспроизводит "
                "переданный ряд без пересчёта. Пропуски не соединяются."
            ),
        )
    )
    story.append(Paragraph("Статистика ячейки (как передана)", style["h2"]))
    if model.slope is None:
        story.append(
            _kv_table(
                [
                    ("Наклон", "не определён для этой ячейки"),
                    ("95 % интервал", "—"),
                    ("p-значение", "—"),
                    ("q (BY, FDR 0,10)", "—"),
                ]
            )
        )
    else:
        story.append(
            _kv_table(
                [
                    ("Наклон", f"{_num(model.slope)} {_SLOPE_UNIT.get(model.unit, 'за 10 лет')}"),
                    ("95 % интервал (HAC 3 лага)", _ci_text(model.ci_low, model.ci_high)),
                    ("p-значение (HAC 3 лага)", _p_text(model.p)),
                    ("q Бенамини–Йекутиели", _p_text(model.q)),
                    (
                        "Флаг q ≤ 0,10",
                        "—"
                        if model.q is None
                        else ("есть" if model.q <= 0.10 else "нет"),
                    ),
                ]
            )
        )
        if model.q is not None and model.q > 0.10:
            story.append(
                Paragraph(
                    escape(
                        "q > 0,10: после поправки на множественные сравнения "
                        "недостаточно доказательств тренда в этой ячейке; это не "
                        "доказывает отсутствие изменений."
                    ),
                    style["small"],
                )
            )
    if missing:
        story.append(
            Paragraph(
                escape(f"Годы без значения: {_year_ranges(missing)}."),
                style["small"],
            )
        )

    story.append(Paragraph("Значения по годам", style["h2"]))
    rows = [[str(point.year), _num(point.value)] for point in model.series]
    story.append(
        _grid_table(
            ("Год", f"Значение, {model.unit}"),
            rows,
            (62, 120),
        )
    )
    story.extend(
        _limits_block("world-cell")
        + _sources_block("world-cell")
        + _footer_note()
    )
    return story


_STORY_BUILDERS: dict[str, Callable[[Any], list[Any]]] = {
    "trend": _story_trend,
    "compare": _story_compare,
    "anomalies": _story_anomalies,
    "seasonal": _story_seasonal,
    "extremes": _story_extremes,
    "world-cell": _story_world_cell,
}


# --- Document assembly ----------------------------------------------------
def _page_furniture(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 32.0, A4[0] - MARGIN, 32.0)
    canvas.setFont(FONT_NAME, 7.4)
    canvas.setFillColor(MUTED)
    canvas.drawString(
        MARGIN,
        22.0,
        "EarthScope · NASA Space Apps Challenge 2026 · данные NASA POWER",
    )
    canvas.drawRightString(A4[0] - MARGIN, 22.0, f"стр. {canvas.getPageNumber()}")
    canvas.restoreState()


def _metadata(kind: str, model: Any) -> tuple[str, str]:
    if kind == "heatwaves":
        return ("EarthScope: волны жары", f"T2M_MAX; {model.start_year}–{model.end_year}; NASA POWER")
    if kind == "world-cell":
        return (
            "EarthScope: ячейка мировой карты трендов",
            f"NASA POWER {model.parameter}, {model.unit}, архив {model.version}",
        )
    if kind == "compare":
        point = model.point_a
        return (f"EarthScope: {_kind_title(kind)}", f"{_VARIABLE_LABEL[point.variable]}; {point.start_year}–{point.end_year}; NASA POWER")
    variable = _EVENT_LABEL[model.event] if kind == "extremes" else _VARIABLE_LABEL[model.variable]
    return (
        f"EarthScope: {_kind_title(kind)}",
        f"{variable}; {model.start_year}–{model.end_year}; NASA POWER",
    )


def _render_pdf(kind: str, model: Any, story: list[Any]) -> bytes:
    _register_fonts()
    title, subject = _metadata(kind, model)
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=44.0,
        bottomMargin=48.0,
        title=title,
        author="EarthScope",
        subject=subject,
        creator="EarthScope report_pdf",
        producer="ReportLab",
        lang="ru",
        invariant=1,
    )
    document.build(
        story,
        onFirstPage=_page_furniture,
        onLaterPages=_page_furniture,
    )
    return buffer.getvalue()


def render_report(kind: str, payload: dict) -> bytes:
    """Render one PDF research report and return the document bytes.

    ``payload`` must be the serialized response body of the matching route:

    ==============  ==========================================
    ``kind``        validation model
    ==============  ==========================================
    ``trend``       :class:`app.schemas.TrendResponse`
    ``compare``     :class:`app.schemas.CompareResponse`
    ``anomalies``   :class:`app.schemas.AnomalyResponse`
    ``seasonal``    :class:`app.schemas.SeasonalResponse`
    ``extremes``    :class:`app.schemas.ExtremesResponse`
    ``world-cell``  :class:`ResearchReport` (local typed model)
    ==============  ==========================================

    ``heatwaves`` is delegated to :func:`app.heatwave_report.render_heatwave_pdf`
    (validated with :class:`app.heatwaves.HeatwaveResponse`) and is not part of
    :data:`KINDS`.

    Raises
    ------
    InvalidReportKind
        ``kind`` is unknown; the message lists the accepted values.
    InvalidReportPayload
        The mapping fails the model for this kind.  The message names fields,
        never their values.
    ReportFontError
        No Cyrillic TrueType font pair is installed.
    """

    if not isinstance(kind, str):
        raise InvalidReportKind(kind)
    if kind == "heatwaves":
        # Extension owned by app.heatwave_report; it reuses this module's
        # validated-payload helpers and keeps the six core kinds unchanged.
        from .heatwave_report import render_heatwave_pdf

        return render_heatwave_pdf(payload)
    model_class = MODEL_FOR_KIND.get(kind)
    if model_class is None:
        raise InvalidReportKind(kind)
    if not isinstance(payload, Mapping):
        raise InvalidReportPayload(
            kind,
            (
                {
                    "loc": ("payload",),
                    "msg": "a mapping of the serialized response body is required",
                },
            ),
        )
    # The PDF route marks detective narration with a private key.  It is not part
    # of any response model, so it is stripped before strict validation.
    detective = bool(payload.get("_detective"))
    body = {key: value for key, value in payload.items() if key != "_detective"}
    try:
        model = model_class.model_validate(body)
    except ValidationError as exc:
        raise InvalidReportPayload(kind, exc.errors()) from exc
    # Paragraph construction resolves the font family immediately (ps2tt), so the
    # pair must be registered before any story builder runs; _render_pdf() calls
    # this again, and the second call is a no-op thanks to the registration lock.
    _register_fonts()
    story = _STORY_BUILDERS[kind](model)
    if detective:
        # _masthead() emits brand, title, subtitle and rule in that order.
        story[4:4] = _detective_block(kind, model)
    return _render_pdf(kind, model, story)
