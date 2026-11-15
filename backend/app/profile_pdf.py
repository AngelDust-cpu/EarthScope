"""PDF export for the EarthScope 2.8 climate profile.

Composition
-----------
One request produces one document:

1. a cover/summary page rendered here with ReportLab from the *validated*
   :class:`app.profile_schemas.ClimateProfileResponse` (parameters, section
   statuses, summary, limitations, provenance, dependency notes);
2. for every ``ok`` section, the existing report of that section, produced by
   :func:`app.report_pdf.render_report` (``trend``, ``anomalies``, ``seasonal``,
   ``extremes``) from the same validated payload;
3. :mod:`pypdf` merges cover and section reports into a single PDF.

``pypdf`` is an *optional runtime* dependency: ``pyproject.toml`` lists it only
in the dev group.  When it is missing, or when merging fails, the profile is
rendered as one ReportLab document from the same validated sections.  The
document states which route was taken, so the dependency is visible instead of
being hidden.

Reuse, not reimplementation
---------------------------
Fonts, paragraph styles, key/value tables, grid tables, bullets, page furniture
and the vector line chart come from :mod:`app.report_pdf`.  This module does not
copy or edit ``report_pdf.py`` (another workstream owns it); it only asks that
module for those hooks and fails with :class:`ReportEngineUnavailable` if the
hooks disappear, so an API drift becomes an explicit HTTP 503 instead of a
silently wrong report.

Nothing is recomputed: every number in this document comes from the validated
section DTOs, and every appended section report revalidates its payload through
the model that guards the matching API route.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Sequence
from typing import Any

from reportlab.platypus import HRFlowable, SimpleDocTemplate

from .profile_schemas import PROFILE_SECTIONS, ClimateProfileResponse
from .schemas import AnomalyResponse, ExtremesResponse, SeasonalResponse, SourceInfo, TrendResponse

logger = logging.getLogger("earthscope.profile.pdf")

__all__ = [
    "ProfilePdfError",
    "ReportEngineUnavailable",
    "render_profile_pdf",
]

#: Hooks this module needs from the existing renderer.  Missing hooks are a
#: hard error: rendering a report with different primitives would duplicate the
#: PDF workstream instead of reusing it.
_REQUIRED_HOOKS: tuple[str, ...] = (
    "_Line",
    "_bullets",
    "_grid_table",
    "_kv_table",
    "_page_furniture",
    "_paragraph",
    "_register_fonts",
    "_styles",
    "_time_chart",
    "render_report",
    "ACCENT",
    "A4",
    "CONTENT_WIDTH",
    "MARGIN",
    "SERIES_BLUE",
    "SERIES_FIT",
    "SERIES_PALETTE",
)

_KIND_BY_SECTION: dict[str, str] = {
    "temperature": "trend",
    "precipitation": "trend",
    "anomalies": "anomalies",
    "seasonal": "seasonal",
    "hot_days": "extremes",
    "cold_nights": "extremes",
    "heavy_rain": "extremes",
}

_SECTION_LABELS: dict[str, str] = {
    "temperature": "Температура (T2M)",
    "precipitation": "Осадки (PRECTOTCORR)",
    "anomalies": "Аномалии температуры",
    "seasonal": "Сезоны",
    "hot_days": "Жаркие дни (T2M_MAX)",
    "cold_nights": "Холодные ночи (T2M_MIN)",
    "heavy_rain": "Сильный дождь (PRECTOTCORR)",
    "heatwaves": "Волны жары",
}

_STATUS_LABELS: dict[str, str] = {
    "ok": "рассчитан",
    "error": "ошибка",
    "unavailable": "недоступен",
}

_HEATWAVE_NOTE = (
    "Волны жары не рассчитываются: в этой версии нет модуля heatwaves и его "
    "согласованного контракта; раздел показан как «недоступен» и не подменяется."
)


class ProfilePdfError(RuntimeError):
    """The profile PDF could not be produced."""


class ReportEngineUnavailable(ProfilePdfError):
    """The existing ReportLab renderer does not expose the required hooks."""


# --- Dependencies ---------------------------------------------------------
def _hooks() -> Any:
    """Return :mod:`app.report_pdf` after checking the hooks this module uses."""

    try:
        from . import report_pdf
    except Exception as exc:  # pragma: no cover - defensive import guard
        raise ReportEngineUnavailable(
            "Модуль report_pdf недоступен: PDF-профиль не собран."
        ) from exc
    missing = [name for name in _REQUIRED_HOOKS if not hasattr(report_pdf, name)]
    if missing:
        raise ReportEngineUnavailable(
            "Модуль report_pdf не предоставляет нужные хуки ("
            + ", ".join(sorted(missing))
            + "): PDF-профиль не собран."
        )
    return report_pdf


def _pypdf_module() -> Any | None:
    """Return :mod:`pypdf` when it is importable at runtime, else ``None``."""

    try:
        import pypdf
    except ModuleNotFoundError:
        return None
    except Exception:  # pragma: no cover - defensive: a broken install
        logger.warning("pypdf could not be imported", exc_info=True)
        return None
    return pypdf


# --- Section reports ------------------------------------------------------
def _section_reports(hooks: Any, profile: ClimateProfileResponse) -> tuple[list[bytes], list[str]]:
    """Render one existing report per successful section.

    A section whose report fails is skipped (and named in the document) so the
    rest of the profile is still delivered; the validated status and message of
    that section remain visible on the cover.
    """

    reports: list[bytes] = []
    skipped: list[str] = []
    for name in PROFILE_SECTIONS:
        section = getattr(profile.sections, name)
        kind = _KIND_BY_SECTION.get(name)
        data = section.data if section.status == "ok" else None
        if kind is None or data is None:
            continue
        try:
            payload = data.model_dump(mode="json")
            reports.append(hooks.render_report(kind, payload))
        except Exception as exc:
            logger.warning("Climate profile section %s PDF failed: %s", name, exc)
            skipped.append(name)
    return reports, skipped


def _merge(pypdf: Any, documents: Sequence[bytes]) -> bytes | None:
    """Merge rendered PDFs; ``None`` when the merge is not usable."""

    try:
        writer = pypdf.PdfWriter()
        for body in documents:
            if not body.startswith(b"%PDF-"):
                return None
            reader = pypdf.PdfReader(io.BytesIO(body))
            if reader.is_encrypted:
                return None
            for page in reader.pages:
                writer.add_page(page)
        buffer = io.BytesIO()
        writer.write(buffer)
        merged = buffer.getvalue()
    except Exception:
        logger.warning("pypdf could not merge the climate profile reports", exc_info=True)
        return None
    return merged if merged.startswith(b"%PDF-") else None


# --- Story ----------------------------------------------------------------
def _clamp(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _p_text(value: float) -> str:
    return "< 0,001" if value < 0.001 else f"{value:.3f}".replace(".", ",")


def _ru(value: float, digits: int = 2, *, signed: bool = False) -> str:
    text = f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"
    return text.replace(".", ",")


def _sources(profile: ClimateProfileResponse) -> list[SourceInfo]:
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


def _masthead(profile: ClimateProfileResponse, hooks: Any, style: Any) -> list[Any]:
    subtitle = (
        f"{profile.latitude:.4f}°, {profile.longitude:.4f}°; "
        f"{profile.start_year}–{profile.end_year}; база "
        f"{profile.baseline_start_year}–{profile.baseline_end_year}"
    )
    return [
        hooks._paragraph(
            "EARTHSCOPE · NASA SPACE APPS CHALLENGE 2026", style["brand"]
        ),
        hooks._paragraph("Климатический профиль точки", style["title"]),
        hooks._paragraph(subtitle, style["subtitle"]),
        HRFlowable(
            width="100%",
            thickness=1.1,
            color=hooks.ACCENT,
            spaceBefore=3,
            spaceAfter=9,
        ),
    ]


def _parameters_table(profile: ClimateProfileResponse, hooks: Any) -> Any:
    thresholds = profile.thresholds
    return hooks._kv_table(
        [
            ("Широта", f"{profile.latitude:.4f}°"),
            ("Долгота", f"{profile.longitude:.4f}°"),
            ("Период", f"{profile.start_year}–{profile.end_year}"),
            (
                "База аномалий",
                f"{profile.baseline_start_year}–{profile.baseline_end_year}",
            ),
            ("Жаркие дни", f"T2M_MAX > {thresholds.hot_days:g} °C"),
            ("Холодные ночи", f"T2M_MIN < {thresholds.cold_nights:g} °C"),
            ("Сильный дождь", f"PRECTOTCORR > {thresholds.heavy_rain:g} мм/сут"),
            (
                "Разделов рассчитано",
                f"{profile.sections.ok_network_count} из "
                f"{len(PROFILE_SECTIONS) - 1}",
            ),
        ]
    )


def _sections_table(profile: ClimateProfileResponse, hooks: Any) -> Any:
    rows = []
    for name in PROFILE_SECTIONS:
        section = getattr(profile.sections, name)
        message = section.message if section.message else "—"
        rows.append(
            [
                _SECTION_LABELS[name],
                _STATUS_LABELS[section.status],
                _clamp(message),
            ]
        )
    return hooks._grid_table(
        ("Раздел", "Статус", "Сообщение"),
        rows,
        (144, 66, hooks.CONTENT_WIDTH - 210),
    )


def _trend_table(trend: Any, unit: str, hooks: Any, style: Any) -> Any:
    if trend is None:
        return hooks._paragraph(
            "Тренд не рассчитан: полных лет меньше минимально требуемого "
            "числа; пропуски не заполняются.",
            style["small"],
        )
    return hooks._kv_table(
        [
            ("Наклон за 10 лет", f"{_ru(trend.per_decade, signed=True)} {unit}"),
            (
                "95 % интервал",
                f"{_ru(trend.ci95_low, signed=True)} … "
                f"{_ru(trend.ci95_high, signed=True)} {unit}",
            ),
            ("p-значение", _p_text(trend.p_value)),
            ("R²", _ru(trend.r_squared)),
            ("Полных лет", str(trend.years_used)),
            (
                "α = 0,05",
                "тренд значим" if trend.significant else "тренд не значим",
            ),
        ]
    )


def _trend_detail(
    model: TrendResponse, label: str, hooks: Any, style: Any
) -> list[Any]:
    values = hooks._Line(
        tuple((point.year, point.value) for point in model.series),
        hooks.SERIES_BLUE,
    )
    fitted = hooks._Line(
        tuple((point.year, point.fitted) for point in model.series),
        hooks.SERIES_FIT,
        dashed=True,
        marked=False,
    )
    story: list[Any] = [
        hooks._paragraph(f"{label}, {model.unit}", style["h2"])
    ]
    story.extend(
        hooks._time_chart(
            (values, fitted),
            caption=(
                "Годовые значения и МНК-прямая; пунктир — оценка только по "
                "полным годам. Полные ряды — в JSON-ответе и CSV."
            ),
        )
    )
    story.append(_trend_table(model.trend, model.unit, hooks, style))
    return story


def _anomaly_detail(
    model: AnomalyResponse, hooks: Any, style: Any
) -> list[Any]:
    values = hooks._Line(
        tuple((point.year, point.value) for point in model.series),
        hooks.SERIES_BLUE,
    )
    anomalies = hooks._Line(
        tuple((point.year, point.anomaly) for point in model.series),
        hooks.SERIES_PALETTE[1],
    )
    story: list[Any] = [
        hooks._paragraph("Аномалии температуры, °C", style["h2"])
    ]
    story.extend(
        hooks._time_chart(
            (values, anomalies),
            caption=(
                "Годовые значения и аномалии к базовому среднему; нулевая линия "
                "соответствует базе. Неполные годы остаются пропусками."
            ),
            zero_line=True,
        )
    )
    story.append(
        hooks._kv_table(
            [
                (
                    "База",
                    f"{model.baseline_start_year}–{model.baseline_end_year}",
                ),
                (
                    "Среднее базы",
                    "не рассчитано"
                    if model.baseline_mean is None
                    else f"{model.baseline_mean:.2f} {model.unit}",
                ),
                ("Полных базовых лет", str(model.baseline_complete_years)),
            ]
        )
    )
    return story


def _seasonal_detail(
    model: SeasonalResponse, hooks: Any, style: Any
) -> list[Any]:
    lines = [
        hooks._Line(
            tuple((point.year, point.value) for point in entry.series),
            hooks.SERIES_PALETTE[index % len(hooks.SERIES_PALETTE)],
        )
        for index, entry in enumerate(model.periods)
    ]
    story: list[Any] = [
        hooks._paragraph(f"Сезоны температуры, {model.unit}", style["h2"])
    ]
    story.extend(
        hooks._time_chart(
            lines,
            caption=(
                "Сезонные средние по годам (DJF, MAM, JJA, SON); зимний сезон "
                "помечен годом января."
            ),
        )
    )
    rows = []
    for entry in model.periods:
        trend = entry.trend
        complete = sum(1 for point in entry.series if point.value is not None)
        rows.append(
            [
                entry.period,
                f"{complete} из {len(entry.series)}",
                "—" if trend is None else _ru(trend.per_decade, signed=True),
                "—"
                if trend is None
                else f"{_ru(trend.ci95_low, signed=True)} … "
                f"{_ru(trend.ci95_high, signed=True)}",
                "—" if trend is None else _p_text(trend.p_value),
            ]
        )
    story.append(
        hooks._grid_table(
            (
                "Сезон",
                "Полных окон",
                "Наклон за 10 лет",
                "95 % интервал",
                "p",
            ),
            rows,
            (76, 66, 96, 132, hooks.CONTENT_WIDTH - 370),
        )
    )
    story.append(
        hooks._paragraph(
            "Ранжирование сезонных наклонов не является формальным тестом "
            "различий между сезонами.",
            style["small"],
        )
    )
    return story


def _extremes_detail(
    model: ExtremesResponse, label: str, hooks: Any, style: Any
) -> list[Any]:
    rates = hooks._Line(
        tuple(
            (
                point.year,
                None if point.fraction is None else point.fraction * 365.0,
            )
            for point in model.series
        ),
        hooks.SERIES_PALETTE[2],
    )
    fitted = hooks._Line(
        tuple(
            (point.year, point.fitted)
            for point in model.series
        ),
        hooks.SERIES_FIT,
        dashed=True,
        marked=False,
    )
    story: list[Any] = [
        hooks._paragraph(
            f"{label}: {model.threshold:g} {model.threshold_unit} "
            f"(оператор {model.operator})",
            style["h2"],
        )
    ]
    story.extend(
        hooks._time_chart(
            (rates, fitted),
            caption=(
                "Дни событий, нормированные на 365 дней года, и МНК-прямая. "
                "Равенство порогу событием не считается."
            ),
        )
    )
    trend = model.trend
    rows = [
        ("Порог", f"{model.threshold:g} {model.threshold_unit}"),
    ]
    if trend is None:
        rows.append(("Тренд", "не рассчитан"))
    else:
        rows.extend(
            [
                (
                    "Наклон за 10 лет",
                    f"{_ru(trend.per_decade, signed=True)} дн./365 дн.",
                ),
            ]
        )
        if trend.ci95_low is not None and trend.ci95_high is not None:
            rows.append(
                (
                    "95 % интервал",
                    f"{_ru(trend.ci95_low, signed=True)} … "
                    f"{_ru(trend.ci95_high, signed=True)}",
                )
            )
        if trend.p_value is not None:
            rows.append(("p-значение", _p_text(trend.p_value)))
        rows.append(("Полных лет", str(trend.years_used)))
    story.append(hooks._kv_table(rows))
    return story


def _ok_section_names(profile: ClimateProfileResponse) -> list[str]:
    return [
        name
        for name in PROFILE_SECTIONS
        if getattr(profile.sections, name).status == "ok"
        and getattr(profile.sections, name).data is not None
    ]


def _details(
    profile: ClimateProfileResponse,
    hooks: Any,
    style: Any,
    names: Sequence[str],
) -> list[Any]:
    story: list[Any] = [hooks._paragraph("Детали разделов", style["h2"])]
    for name in names:
        if name not in _KIND_BY_SECTION:
            continue
        section = getattr(profile.sections, name)
        data = section.data if section.status == "ok" else None
        if data is None:
            continue
        label = _SECTION_LABELS[name]
        if name in ("temperature", "precipitation"):
            story.extend(_trend_detail(data, label, hooks, style))
        elif name == "anomalies":
            story.extend(_anomaly_detail(data, hooks, style))
        elif name == "seasonal":
            story.extend(_seasonal_detail(data, hooks, style))
        else:
            story.extend(_extremes_detail(data, label, hooks, style))
    return story


def _provenance(
    profile: ClimateProfileResponse, hooks: Any, style: Any
) -> list[Any]:
    sources = _sources(profile)
    if not sources:
        return []
    story: list[Any] = [
        hooks._paragraph("Данные и источник", style["h2"]),
        hooks._paragraph(
            "NASA POWER Daily Point API, стандарт времени UTC. Ниже — "
            "метаданные каждого успешно рассчитанного раздела.",
            style["small"],
        ),
    ]
    rows = [
        [
            source.parameter,
            source.dataset,
            source.daily_unit,
            source.retrieved_at,
            "да" if source.cached else "нет",
        ]
        for source in sources
    ]
    story.append(
        hooks._grid_table(
            (
                "Параметр",
                "Набор данных",
                "Единица",
                "Получено (UTC)",
                "Кэш",
            ),
            rows,
            (58, hooks.CONTENT_WIDTH - 340, 52, 190, 40),
        )
    )
    for source in sources:
        story.append(
            hooks._paragraph(
                f"Запрос {source.parameter} (текст, без перехода по ссылке): "
                f"{_clamp(source.request_url)}",
                style["mono"],
            )
        )
    return story


def _story(
    profile: ClimateProfileResponse,
    hooks: Any,
    *,
    detail_sections: Sequence[str],
    notes: Sequence[str],
) -> list[Any]:
    style = hooks._styles()
    story: list[Any] = _masthead(profile, hooks, style)
    story.append(_parameters_table(profile, hooks))
    story.append(hooks._paragraph("Сводка", style["h2"]))
    story.extend(hooks._bullets(profile.summary))
    story.append(hooks._paragraph("Ограничения и допущения", style["h2"]))
    story.extend(hooks._bullets(profile.limitations))
    story.append(hooks._paragraph("Разделы", style["h2"]))
    story.append(_sections_table(profile, hooks))
    if detail_sections:
        story.extend(_details(profile, hooks, style, detail_sections))
    story.extend(_provenance(profile, hooks, style))
    story.append(hooks._paragraph("Зависимости и состав отчёта", style["h2"]))
    story.extend(hooks._bullets(list(notes)))
    if hasattr(hooks, "_footer_note"):
        story.extend(hooks._footer_note())
    return story


def _document(
    profile: ClimateProfileResponse,
    hooks: Any,
    *,
    detail_sections: Sequence[str],
    notes: Sequence[str],
) -> bytes:
    hooks._register_fonts()
    story = _story(
        profile,
        hooks,
        detail_sections=detail_sections,
        notes=notes,
    )
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=hooks.A4,
        leftMargin=hooks.MARGIN,
        rightMargin=hooks.MARGIN,
        topMargin=44.0,
        bottomMargin=48.0,
        title="EarthScope: климатический профиль точки",
        author="EarthScope",
        subject=(
            f"NASA POWER; {profile.start_year}–{profile.end_year}; база "
            f"{profile.baseline_start_year}–{profile.baseline_end_year}"
        ),
        creator="EarthScope profile_pdf",
        producer="ReportLab",
        lang="ru",
        invariant=1,
    )
    document.build(
        story,
        onFirstPage=hooks._page_furniture,
        onLaterPages=hooks._page_furniture,
    )
    return buffer.getvalue()


def _composed_notes(
    count: int, skipped: Sequence[str], version: str
) -> list[str]:
    suffix = f" {version}" if version else ""
    notes = [
        f"Отчёт собран из обложки ReportLab и {count} отчётов разделов, "
        f"сформированных существующим render_report, и объединён pypdf"
        f"{suffix}. Повторной загрузки данных нет.",
        "Полные годовые ряды и точные единицы доступны в JSON-ответе и в "
        "CSV-экспорте профиля; PDF показывает сводку и графики.",
    ]
    if skipped:
        notes.append(
            "Для разделов "
            + ", ".join(_SECTION_LABELS[name] for name in skipped)
            + " готовый отчёт сформировать не удалось: их проверенные данные "
            "показаны в этом документе (графики и таблицы ReportLab), статус и "
            "сообщение — в таблице разделов."
        )
    notes.append(_HEATWAVE_NOTE)
    return notes


def _single_notes(pypdf_available: bool, detail: str = "") -> list[str]:
    if pypdf_available:
        reason = (
            "pypdf не смог объединить отчёты разделов, поэтому профиль собран "
            "одним документом ReportLab"
        )
    else:
        reason = (
            "pypdf не установлен (необязательная зависимость, только dev-группа "
            "pyproject.toml), поэтому профиль собран одним документом ReportLab"
        )
    notes = [
        f"{reason} из тех же проверенных данных. Повторной загрузки данных нет.",
        "Полные годовые ряды и точные единицы доступны в JSON-ответе и в "
        "CSV-экспорте профиля; PDF показывает сводку и графики.",
    ]
    if detail:
        notes.append(detail)
    notes.append(_HEATWAVE_NOTE)
    return notes


def render_profile_pdf(profile: ClimateProfileResponse) -> bytes:
    """Render the complete climate profile as one PDF document.

    :class:`ClimateProfileResponse` is already validated, so this function only
    lays out numbers it receives.  It reuses :mod:`app.report_pdf` for fonts,
    tables, page furniture and charts, and the optional :mod:`pypdf` to merge
    the per-section reports.
    """

    hooks = _hooks()
    # The shared renderer builds paragraphs while composing a story, and those
    # paragraphs need the metric-compatible font pair to exist already, so the
    # profile registers the shared fonts before composing section reports.
    hooks._register_fonts()
    pypdf = _pypdf_module()

    if pypdf is not None:
        reports, skipped = _section_reports(hooks, profile)
        if reports:
            cover = _document(
                profile,
                hooks,
                # A section whose own report could not be rendered is drawn
                # here instead, so the merged document still shows its data.
                detail_sections=skipped,
                notes=_composed_notes(
                    len(reports),
                    skipped,
                    str(getattr(pypdf, "__version__", "")),
                ),
            )
            merged = _merge(pypdf, [cover, *reports])
            if merged is not None:
                return merged
            logger.warning(
                "pypdf merge produced no usable profile document; "
                "falling back to a single ReportLab report"
            )
            return _document(
                profile,
                hooks,
                detail_sections=_ok_section_names(profile),
                notes=_single_notes(pypdf_available=True),
            )
        return _document(
            profile,
            hooks,
            detail_sections=_ok_section_names(profile),
            notes=_single_notes(
                pypdf_available=True,
                detail=(
                    "Отчёты разделов не удалось сформировать: "
                    + (", ".join(_SECTION_LABELS[name] for name in skipped) or "нет")
                    + ". Показаны только проверенные данные и их статусы."
                ),
            ),
        )

    return _document(
        profile,
        hooks,
        detail_sections=_ok_section_names(profile),
        notes=_single_notes(pypdf_available=False),
    )
