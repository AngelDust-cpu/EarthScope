"""Heatwave extension for the scientific PDF renderer."""
from reportlab.platypus import KeepTogether, Paragraph

from . import report_pdf as pdf
from .heatwaves import HeatwaveResponse


def render_heatwave_pdf(payload: dict) -> bytes:
    model = HeatwaveResponse.model_validate(payload)
    pdf._register_fonts()
    style = pdf._styles()
    story = []

    def add(text, key="body"):
        story.append(pdf._paragraph(text, style[key]))

    add("EARTHSCOPE · НАУЧНЫЙ ОТЧЁТ", "brand")
    add("Детектив · Волны жары" if payload.get("_detective") else "Волны жары", "title")
    story.append(pdf._kv_table([
        ("Местоположение", f"{pdf._latitude(model.latitude)}, {pdf._longitude(model.longitude)}"),
        ("Период", f"{model.start_year}–{model.end_year} UTC"),
        ("Порог", f"Tmax > {pdf._num(model.threshold)} °C, не менее {model.min_duration} суток подряд"),
        ("NASA POWER", f"{model.source.dataset} · {model.source.parameter} · {model.source.retrieved_at}"),
    ]))
    story.append(pdf._link("power_daily", "Документация NASA POWER Daily", style["small"]))
    complete = [row for row in model.series if row.count is not None]
    if complete:
        count = sum(row.count for row in complete)
        days = sum(row.total_days for row in complete)
        longest = max(row.max_duration for row in complete)
        add(f"Полных лет: {len(complete)}. Эпизодов: {count}. Дней в волнах: {days}. Максимум: {longest} дней. "
            f"Средняя длительность: {pdf._num(days / count if count else None)} дней.")
    else:
        add("Полных лет для сводки нет.")
    for metric, label, unit in (
        ("count", "Число волн", "эпизодов"),
        ("total_days", "Суммарная длительность", "дней"),
        ("max_duration", "Максимальная длительность", "дней"),
    ):
        start = len(story)
        add(label, "h2")
        trend = getattr(model.trends, metric)
        if trend is None:
            add("Тренд недоступен.")
        else:
            add(f"Описательный наклон: {pdf._num(trend.per_decade)} {unit}/десятилетие. "
                f"Лет: {trend.years_used}. Автокорреляция остатков, лаг 1: {pdf._num(trend.residual_lag1)}.")
        points = tuple((row.year, getattr(row, metric)) for row in model.series)
        story.extend(pdf._time_chart([pdf._Line(points, pdf.SERIES_BLUE)], caption=f"{label} по годам, {unit}", zero_line=True))
        story[start:] = [KeepTogether(story[start:])]

    add("Методика и ограничения", "h2")
    add("Пропуск или значение на пороге разрывает эпизод. Волна, продолжающаяся через 31 декабря, относится к году начала. "
        "Неполные годы исключаются из годовых метрик и регрессии. Эпизоды на границах окна могут быть обрезаны. "
        "Порог задаёт пользователь. Наклон OLS описательный, корреляция соседних остатков служит диагностикой. "
        "Значимость, причины и прогноз здесь не оцениваются. NASA POWER представляет сеточный реанализ.")
    add(f"Эпизодов в доступном ряду: {len(model.episodes)}. Полный список и годовые значения доступны в CSV.")
    if model.episodes:
        add("Последние эпизоды", "h2")
        for event in model.episodes[-12:]:
            add(f"{event.start} — {event.end} · {event.duration} дней · Tmax {pdf._num(event.max_tmax)} °C", "small")
    return pdf._render_pdf("heatwaves", model, story)
