import { useId, useRef, useState } from 'react'
import { analysisPdfRequest } from '../lib/pdf'
import { PdfDownloadButton } from './PdfDownloadButton'
import { downloadCsv } from '../lib/api'
import { extremeEvents, extremesCsv, type ExtremesResponse } from '../lib/extremes'
import { Button } from './ui/button'
import { ExtremesRateChart } from './ExtremesRateChart'

const number = (v: number | null | undefined, digits = 2) => v == null ? '—' : v.toLocaleString('ru-RU', { maximumFractionDigits: digits })
const pLabel = (v: number | null) => v === null ? '—' : v < 0.00001 ? '< 0,00001' : `= ${Number(v.toFixed(5)).toString().replace('.', ',')}`
const plural = (n: number, one: string, few: string, many: string) => {
  const value = Math.abs(Math.trunc(n)), lastTwo = value % 100, last = value % 10
  return lastTwo >= 11 && lastTwo <= 14 ? many : last === 1 ? one : last >= 2 && last <= 4 ? few : many
}
const days = (n: number) => `${number(n, 0)} ${plural(n, 'день', 'дня', 'дней')}`
const panel = 'min-w-0 space-y-3 rounded border border-line bg-card p-4'

/** Shared standalone / Detective presentation; all values come from the submitted response. */
export function ExtremesResult({ data }: { data: ExtremesResponse }) {
  const id = useId()
  const [selectedYear, setSelectedYear] = useState<number | null>(null)
  const [focusYear, setFocusYear] = useState<number | null>(data.series[0]?.year ?? null)
  const yearRefs = useRef(new Map<number, SVGGElement>())
  const event = extremeEvents[data.event], t = data.trend
  const complete = data.series.filter(p => p.events !== null)
  const max = Math.max(1, ...complete.map(p => p.events!))
  const meanEvents = complete.length ? complete.reduce((sum, p) => sum + p.events!, 0) / complete.length : null
  // Integer axis geometry only; the source counts and scientific results are unchanged.
  const tickStep = Math.max(1, Math.ceil(max / 4))
  const axisMax = tickStep * 4
  // Keep an adequate touch target for each year, with a local horizontal scroller on narrow screens.
  const chartWidth = Math.max(480, 80 + data.series.length * 28)
  const width = (chartWidth - 80) / Math.max(1, data.series.length)
  const selected = data.series.find(p => p.year === selectedYear) ?? data.series[0]
  const activeFocusYear = data.series.some(p => p.year === focusYear) ? focusYear : selected?.year
  const showAllLabels = data.series.length <= 12
  return <div className="min-w-0 space-y-4">
    <section className={panel}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold">{event.label}: {event.parameter} {data.operator} {number(data.threshold)} {event.unit}</h3>
        <PdfDownloadButton request={analysisPdfRequest(data)} />
        <Button type="button" variant="outline" onClick={() => downloadCsv(`EarthScope_extremes_${data.latitude}_${data.longitude}.csv`, extremesCsv(data))}>Скачать CSV</Button>
      </div>
      <p className="text-sm">WGS84: {number(data.latitude, 5)}, {number(data.longitude, 5)}° · {data.start_year}–{data.end_year}, включительно · UTC</p>
      <p className="text-xs text-muted">Абсолютный порог; равенство не учитывается. {data.event === 'cold_nights' && 'Это минимум за сутки UTC, не отдельное ночное наблюдение.'}</p>
      <div className="grid gap-3 sm:grid-cols-3">
        {[
          ['Полные годы', `${complete.length} / ${data.series.length}`],
          ['Число событий за полный год: диапазон', complete.length ? `${Math.min(...complete.map(p => p.events!))}–${Math.max(...complete.map(p => p.events!))} дней` : 'Нет полных лет'],
          ['Доля дней за полный год: диапазон', complete.length ? `${number(100 * Math.min(...complete.map(p => p.fraction!)))}–${number(100 * Math.max(...complete.map(p => p.fraction!)))} %` : '—'],
        ].map(([label, value]) => <div key={label} className="rounded border border-line bg-panel p-3"><p className="text-xs text-muted">{label}</p><p className="mt-1 font-semibold">{value}</p></div>)}
      </div>
      <p id={`${id}-instructions`} className="text-xs text-muted">Выберите год нажатием или клавишами: Tab, стрелки для перехода, Enter / Пробел для выбора. Точное значение показано в карточке; все годы доступны в таблице.</p>
      <div className="grid min-w-0 gap-2 sm:grid-cols-3" aria-label="Основные показатели экстремальных событий">
        <div id={`${id}-selection`} className="min-w-0 rounded border border-line bg-panel p-3" role="status" aria-live="polite" aria-atomic="true">
          <p className="text-xs text-muted">Количество дней · {selectedYear === null ? 'первый год периода' : 'выбранный год'} ({selected?.year ?? 'не выбран'}, UTC)</p>
          <p className="mt-1 font-semibold">{!selected ? 'Нет данных' : selected.events === null ? 'Неполный год' : days(selected.events)}</p>
          {selected?.events === null && <p className="mt-1 text-xs text-muted">Число не рассчитано: {selected.valid_days} / {selected.expected_days} валидных дней.</p>}
        </div>
        <div className="min-w-0 rounded border border-line bg-panel p-3">
          <p className="text-xs text-muted">Среднее за полный год</p>
          <p className="mt-1 font-semibold">{meanEvents === null ? 'Недоступно' : `${number(meanEvents)} дн./год`}</p>
          <p className="mt-1 text-xs text-muted">За {complete.length} {plural(complete.length, 'полный год', 'полных года', 'полных лет')} периода.</p>
        </div>
        <div className="min-w-0 rounded border border-line bg-panel p-3">
          <p className="text-xs text-muted">Изменение числа дней за десятилетие</p>
          <p className="mt-1 font-semibold">{t ? `${number(t.count_per_decade)} дн./десятилетие` : 'Недоступно'}</p>
          <p className="mt-1 text-xs text-muted">{!t ? 'Нужно не менее 5 полных лет.' : t.p_value === null ? 'Описательная оценка; статистический вывод недоступен.' : t.significant ? 'Изменение статистически значимо по HAC(3), α = 0,05.' : 'Изменение не подтверждено по HAC(3), α = 0,05.'}</p>
        </div>
      </div>
      <div className="-mx-1 max-w-full overflow-x-auto pb-1" role="region" aria-label="График числа дней по годам" tabIndex={0}>
      <svg viewBox={`0 0 ${chartWidth} 290`} role="radiogroup" aria-label="Выбор года на графике" aria-labelledby={`${id}-chart`} aria-describedby={`${id}-instructions`} className="block h-auto w-full" style={{ minWidth: `${chartWidth}px` }}>
        <title id={`${id}-chart`}>Число дней с событием по годам UTC. Пропуски не заменены нулями.</title>
        <text x="12" y="20" fill="currentColor" fontSize="14">Количество дней</text>
        {[0, 1, 2, 3, 4].map(i => {
          const y = 230 - i * 44
          return <g key={i}>
            <line x1="56" x2={chartWidth - 24} y1={y} y2={y} stroke="currentColor" strokeOpacity={i === 0 ? 1 : .15} />
            <text x="48" y={y + 4} textAnchor="end" fill="currentColor" fontSize="13">{number(i * tickStep, 0)}</text>
          </g>
        })}
        <line x1="56" x2="56" y1="54" y2="230" stroke="currentColor" />
        {data.series.map((p, i) => {
          const x = 56 + width * (i + .5)
          const height = p.events === null ? 0 : 176 * p.events / axisMax
          const active = p.year === selected?.year
          const label = p.events === null ? '×' : number(p.events, 0)
          return <g key={p.year} ref={node => { if (node) yearRefs.current.set(p.year, node); else yearRefs.current.delete(p.year) }} role="radio" tabIndex={p.year === activeFocusYear ? 0 : -1} aria-checked={active}
            aria-label={`${p.year}: ${p.events === null ? 'неполный год, число дней не рассчитано' : days(p.events)}`}
            aria-controls={`${id}-selection`} className="group cursor-pointer focus-visible:outline-none"
            onFocus={() => setFocusYear(p.year)}
            onClick={() => { setFocusYear(p.year); setSelectedYear(p.year) }}
            onKeyDown={e => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                setSelectedYear(p.year)
              } else if (['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp'].includes(e.key)) {
                e.preventDefault()
                const delta = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1
                const next = (i + delta + data.series.length) % data.series.length
                const nextYear = data.series[next]?.year
                if (nextYear !== undefined) { setFocusYear(nextYear); yearRefs.current.get(nextYear)?.focus() }
              }
            }}>
            <title>{p.year}: {p.events === null ? 'неполный год' : days(p.events)}</title>
            <rect x={56 + width * i + 2} y="38" width={Math.min(width - 4, 24)} height="198"
              fill="transparent" stroke={active ? 'currentColor' : 'transparent'} strokeDasharray="3 2"
              className="group-focus-visible:stroke-current" />
            {p.events === null
              ? <text x={x} y="225" textAnchor="middle" fill="currentColor" fontSize="14" pointerEvents="none">×</text>
              : p.events === 0
                ? <circle cx={x} cy="230" r="2.5" fill="currentColor" opacity={active ? 1 : .6} pointerEvents="none" />
                : <rect x={56 + width * (i + .18)} y={230 - height} width={width * .64}
                  height={Math.max(1, height)} fill="currentColor" opacity={active ? 1 : .6} pointerEvents="none" />}
            {(showAllLabels || active) && <text data-year-label={p.year} x={x} y={p.events === null ? 208 : 222 - height}
              textAnchor="middle" fill="currentColor" fontSize="12" fontWeight={active ? 'bold' : 'normal'} pointerEvents="none">{label}</text>}
          </g>
        })}
        <text x="56" y="256" fill="currentColor" fontSize="13">{data.series[0]?.year}</text>
        <text x={chartWidth / 2} y="281" textAnchor="middle" fill="currentColor" fontSize="14">Год (UTC)</text>
        {data.series.length > 1 && <text x={chartWidth - 24} y="256" textAnchor="end" fill="currentColor" fontSize="13">{data.series.at(-1)?.year}</text>}
      </svg>
      </div>
      {!complete.length && <p role="status">Нет полных лет: число событий и доля не рассчитаны. Диагностика доступна ниже.</p>}
      <p className="text-xs text-muted">× — неполный год. Ноль в полном году — реальный результат. Линия тренда не наложена на сырые числа дней.</p>
    </section>
    <ExtremesRateChart data={data} />
    <section className={panel}>
      <h3 className="font-semibold">Тренд и неопределённость</h3>
      {t ? <>
        <p className="text-sm">Основной наклон: <strong>{number(t.per_decade)} дн. событий на 365 дней за десятилетие</strong>. Наклон сырых годовых чисел: {number(t.count_per_decade)} дн./десятилетие. Полных лет в модели: {t.years_used}.</p>
        <p className="text-sm">95% ДИ HAC(3): [{number(t.ci95_low)}; {number(t.ci95_high)}] · p {pLabel(t.p_value)} · {t.significant === null ? 'Вывод о значимости недоступен' : t.significant ? 'Тренд статистически значим при α = 0,05 по HAC(3)' : 'HAC(3) не подтверждает тренд при α = 0,05; это не доказывает отсутствия изменений'}.</p>
        {t.p_value !== null && t.sensitivity.some(s => (s.p_value < 0.05) !== t.significant) &&
          <p className="text-sm text-warning">Вывод при пороге 0,05 меняется в зависимости от метода. Интерпретируйте его с осторожностью.</p>}
        <p className="text-sm">Корреляция остатков с лагом 1: {number(t.residual_lag1)} (только соседние календарные годы).</p>
        {t.p_value === null && <p className="text-xs">Показаны только описательные наклоны: условия статистического вывода не выполнены. «—» не означает ноль или отсутствие изменений.</p>}
        {t.sensitivity.length > 0 && <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Чувствительность вывода">
          <table className="w-full text-left text-xs"><caption className="pb-2 text-left">Чувствительность 95% ДИ и p к оценке ковариации; единицы ДИ — дни на 365 дней за десятилетие.</caption>
            <thead><tr>{['Метод', 'Нижняя ДИ', 'Верхняя ДИ', 'p'].map(h => <th scope="col" key={h} className="p-2">{h}</th>)}</tr></thead>
            <tbody>{t.sensitivity.map((s, i) => <tr key={i} className="border-t border-line"><th scope="row" className="p-2">{s.method === 'ols' ? 'МНК (OLS)' : `HAC(${s.lags})`}</th><td className="p-2">{number(s.ci95_low)}</td><td className="p-2">{number(s.ci95_high)}</td><td className="p-2">{pLabel(s.p_value)}</td></tr>)}</tbody>
          </table>
        </div>}
      </> : <p className="text-sm">Тренд недоступен: нужно не менее 5 полных лет и недегенеративная модель.</p>}
      <details><summary className="cursor-pointer p-2 text-sm focus-visible:outline-2 focus-visible:outline-focus">Метод и ограничения</summary>
        <p className="text-xs leading-relaxed">МНК со свободным членом по фактическим календарным годам для 365 × события / валидные дни. Сырые числа моделируются отдельно. Вывод — только при ≥20 последовательных полных годах, непостоянном ряде, недегенеративных остатках и конечной положительной ковариации. Основная оценка: Newey–West HAC(3), веса Бартлетта, поправка n/(n−2), приближённое t(n−2). Чувствительность: OLS, HAC(1/3/5). Это не причинный вывод и не прогноз повторяемости. Поправки на многократное исследование нет. Порог не является относительным климатологическим экстремумом.</p>
      </details>
    </section>
    <section className={panel}>
      <h3 className="font-semibold">Годовые значения и диагностика</h3>
      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Таблица годовых экстремумов">
        <table className="w-full text-left text-xs">
          <caption className="mb-2 text-left text-muted">Только полные 365/366 суток UTC дают число и долю. «—» — пропуск, не ноль. Расчёт модели нормирован на 365 дней, это не сырое число событий.</caption>
          <thead><tr>{['Год', 'События, дни', 'Доля, %', 'Валидные / ожидаемые дни', 'Исключено дней', 'Модель, дни на 365 дней'].map(h => <th scope="col" className="p-2" key={h}>{h}</th>)}</tr></thead>
          <tbody>{data.series.map(p => <tr key={p.year} className="border-t border-line"><th scope="row" className="p-2">{p.year}</th><td className="p-2">{p.events === null ? '—' : days(p.events)}</td><td className="p-2">{number(p.fraction === null ? null : 100 * p.fraction)}</td><td className="p-2">{p.valid_days} / {p.expected_days}</td><td className="p-2">{p.excluded_days}</td><td className="p-2">{number(p.fitted)}</td></tr>)}</tbody>
        </table>
      </div>
    </section>
    <section className={panel}>
      <h3 className="font-semibold">Источник и происхождение</h3>
      <p className="text-sm">{data.source.name} · {data.source.dataset} · {data.source.parameter} · {data.source.daily_unit} · {data.source.time_standard}</p>
      <p className="text-xs">Получено: {data.source.retrieved_at} · {data.source.cached ? 'Из кэша' : 'От источника'}</p>
      <p className="break-all text-xs">{data.source.request_url}</p>
      <p className="text-xs text-muted">Рассчитанные агрегаты сеточного продукта NASA POWER, не наблюдения отдельной станции и не прогноз. Суточные значения и абсолютные пороги могут иметь разный локальный смысл.</p>
      {data.warnings.length > 0 && <div role="note" className="space-y-1 text-xs"><p>Предупреждения API (как получены):</p>{data.warnings.map((w, i) => <p className="break-words" key={i}>{w}</p>)}</div>}
    </section>
  </div>
}
