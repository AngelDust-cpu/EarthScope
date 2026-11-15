import { useId, useState } from 'react'
import { downloadCsv } from '../lib/api'
import { heatwavesCsv, heatwavesResultQuery, heatwavesUrl, type HeatwavesResponse } from '../lib/heatwaves'
import { PdfDownloadButton } from './PdfDownloadButton'
import { Button } from './ui/button'
import { Field, Select } from './ui/field'

const fmt = (v: number | null | undefined) => v == null ? '—' : v.toLocaleString('ru-RU', { maximumFractionDigits: 2 })
const metrics = [
  { key: 'count', title: 'Число волн жары', unit: 'эпизодов' },
  { key: 'total_days', title: 'Дни в волнах жары', unit: 'дней' },
  { key: 'max_duration', title: 'Максимальная длительность', unit: 'дней' },
] as const
const panel = 'min-w-0 space-y-3 rounded border border-line bg-card p-4'

export function HeatwavesResult({ data }: { data: HeatwavesResponse }) {
  const id = useId()
  const [year, setYear] = useState('')
  const selected = data.series.find(p => String(p.year) === year) ?? data.series[0]
  const complete = data.series.filter(p => p.count !== null)
  const count = complete.reduce((sum, row) => sum + (row.count ?? 0), 0)
  const days = complete.reduce((sum, row) => sum + (row.total_days ?? 0), 0)
  const longest = complete.reduce((max, row) => Math.max(max, row.max_duration ?? 0), 0)
  const selectedEpisodes = data.episodes.filter(episode => Number(episode.start.slice(0, 4)) === selected?.year)
  return <div className="min-w-0 space-y-4">
    <section className={panel}>
      <h3 className="font-semibold">Волны жары · T2M_MAX &gt; {fmt(data.threshold)} °C · ≥{data.min_duration} суток подряд</h3>
      <p className="text-sm">WGS84: {fmt(data.latitude)}, {fmt(data.longitude)}° · {data.start_year}–{data.end_year}, включительно · UTC</p>
      <p className="text-sm">Эпизод — последовательные сутки строго выше порога. Равенство порогу не учитывается. Полных лет: {complete.length} / {data.series.length}.</p>
      {complete.length > 0 && <dl className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {[
          ['Волн жары', fmt(count)], ['Дни в волнах', `${fmt(days)} дн.`],
          ['Самая длинная', count ? `${fmt(longest)} дн.` : '—'],
          ['Средняя длительность', count ? `${fmt(days / count)} дн.` : '—'],
        ].map(([label, value]) => <div key={label} className="rounded border border-line p-3">
          <dt className="text-xs text-muted">{label}</dt><dd className="text-lg font-semibold">{value}</dd>
        </div>)}
      </dl>}
      <div className="flex flex-wrap items-start gap-2">
        <PdfDownloadButton request={{ url: heatwavesUrl(heatwavesResultQuery(data)) }} />
        {(['annual', 'episodes'] as const).map(kind => <Button key={kind} type="button" variant="outline" size="sm"
          onClick={() => downloadCsv(`EarthScope_heatwaves_${kind}_${data.latitude}_${data.longitude}.csv`, heatwavesCsv(data, kind))}>
          CSV · {kind === 'annual' ? 'годы' : 'эпизоды'}</Button>)}
      </div>
      <p className="text-sm">Поиск непрерывен через 31 декабря. Вся статистика эпизода, включая дни следующего года, относится к году его начала; годовые суммы не равны числу жарких дней внутри календарного года.</p>
      <p className="text-sm text-warning">На внешних границах выбранного окна эпизоды могут быть обрезаны: начало до первого дня или продолжение после последнего неизвестны. Их длительность и число могут быть занижены; это не полная история волн жары.</p>
      {data.warnings.length > 0 && <div role="note" className="space-y-1 text-xs"><p>Предупреждения API (как получены):</p>{data.warnings.map((w, i) => <p className="break-words" key={i}>{w}</p>)}</div>}
    </section>
    <section className={panel}>
      <h3 className="font-semibold">По годам начала эпизодов</h3>
      {data.series.length > 0 ? <>
        <Field htmlFor={`${id}-year`} label="Год для точных значений (UTC)"><Select id={`${id}-year`} value={String(selected?.year)} onChange={e => setYear(e.target.value)}>
          {data.series.map(p => <option key={p.year} value={p.year}>{p.year}{p.count === null ? ' · неполный' : ''}</option>)}
        </Select></Field>
        <p role="status" className="text-sm">{selected?.year}: {selected?.count === null ? 'Неполный год — статистика недоступна.' :
          `${fmt(selected?.count)} эпизодов · ${fmt(selected?.total_days)} дней · максимум ${fmt(selected?.max_duration)} дней.`}</p>
        <div className="grid min-w-0 gap-4 xl:grid-cols-3">
          {metrics.map(({ key, title, unit }) => {
            const max = Math.max(1, ...data.series.map(p => p[key] ?? 0))
            const step = 280 / data.series.length
            return <figure key={key} className="min-w-0">
              <figcaption className="text-sm font-medium">{title}, {unit}</figcaption>
              <svg viewBox="0 0 360 220" role="img" aria-label={`${title} по годам UTC. Точные значения в таблице ниже.`} className="w-full">
                <title>{title}: × — неполный год, точка на оси — ноль</title>
                {[0, .5, 1].map(v => <g key={v}><line x1="52" x2="332" y1={175 - v * 140} y2={175 - v * 140} stroke="currentColor" opacity=".2" /><text x="46" y={179 - v * 140} textAnchor="end" fill="currentColor" fontSize="11">{fmt(v * max)}</text></g>)}
                {data.series.map((p, i) => {
                  const x = 52 + (i + .5) * step, value = p[key]
                  return <g key={p.year}><title>{p.year}: {value === null ? 'неполный год' : `${value} ${unit}`}</title>
                    {value === null ? <text x={x} y="170" textAnchor="middle" fontSize="12" fill="currentColor">×</text> : value === 0 ?
                      <circle cx={x} cy="175" r="2" fill="currentColor" /> :
                      <rect x={x - step * .34} y={175 - 140 * value / max} width={step * .68} height={140 * value / max} fill="currentColor" opacity={p.year === selected?.year ? 1 : .55} />}
                  </g>
                })}
                <text x="52" y="198" fill="currentColor" fontSize="12">{data.series[0]?.year}</text>
                <text x="332" y="198" textAnchor="end" fill="currentColor" fontSize="12">{data.series.at(-1)?.year}</text>
                <text x="192" y="216" textAnchor="middle" fill="currentColor" fontSize="12">Год начала (UTC)</text>
              </svg>
            </figure>
          })}
        </div>
      </> : <p role="status">Годовой ряд пуст.</p>}
      <p className="text-xs text-muted">× / «—» — отсутствующие значения неполных лет, не нули. В полном году без эпизодов число, сумма дней и максимальная длительность — 0; средняя длительность и максимальная Tmax — «—».</p>
      {!complete.length && <p role="status">Нет полных лет для годовых сравнений.</p>}
    </section>
    <section className={panel}>
      <h3 className="font-semibold">Описательные тренды · OLS</h3>
      <p className="text-sm">МНК по годам. Только описательные наклоны и диагностика корреляции остатков с лагом 1. Нет p-значений, доверительных интервалов или вывода о значимости; это не прогноз и не причинный вывод.</p>
      <div className="grid gap-3 md:grid-cols-3">{metrics.map(({ key, title, unit }) => {
        const t = data.trends[key]
        return <div key={key} className="rounded border border-line p-3 text-sm"><h4 className="font-medium">{title}</h4>
          {t ? <><p>{t.per_decade.toLocaleString('ru-RU', { maximumFractionDigits: 3, signDisplay: 'exceptZero' })} {unit}/десятилетие</p><p>Лет в модели: {t.years_used}</p><p>Лаг 1 (безразмерный): {fmt(t.residual_lag1)}</p></> : <p>Оценка недоступна.</p>}</div>
      })}</div>
    </section>
    <section className={panel}>
      <h3 className="font-semibold">Годовая статистика и полнота</h3>
      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Годовая таблица волн жары">
        <table className="w-full text-left text-xs"><caption className="pb-2 text-left">UTC; все показатели эпизода отнесены к году начала. «—» — нет значения.</caption>
          <thead><tr>{['Год', 'Эпизоды', 'Всего дней', 'Макс. длительность, дни', 'Сред. длительность, дни', 'Макс. Tmax, °C', 'Валидные / ожидаемые дни'].map(h => <th key={h} scope="col" className="p-2">{h}</th>)}</tr></thead>
          <tbody>{data.series.map(p => <tr key={p.year} className="border-t border-line"><th scope="row" className="p-2">{p.year}</th>{[p.count, p.total_days, p.max_duration, p.mean_duration, p.max_tmax].map((v, i) => <td key={i} className="p-2">{fmt(v)}</td>)}<td className="p-2">{p.valid_days} / {p.expected_days}</td></tr>)}</tbody>
        </table>
      </div>
    </section>
    <section className={panel}>
      <h3 className="font-semibold">Эпизоды за {selected?.year} · {selectedEpisodes.length}</h3>
      {!selectedEpisodes.length ? <p role="status">В доступных наблюдениях за выбранный год эпизоды не обнаружены.</p> :
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Таблица эпизодов волн жары"><table className="w-full text-left text-xs">
          <caption className="pb-2 text-left">Даты UTC включительно. Длительность не обрезается на 31 декабря. Tmax — суточный максимум температуры на высоте 2 м.</caption>
          <thead><tr>{['Начало (UTC)', 'Конец (UTC)', 'Длительность, дни', 'Макс. Tmax, °C', 'Сред. Tmax, °C', 'Макс. превышение порога, °C'].map(h => <th key={h} scope="col" className="p-2">{h}</th>)}</tr></thead>
          <tbody>{selectedEpisodes.map(p => <tr key={p.start} className="border-t border-line"><th scope="row" className="whitespace-nowrap p-2">{p.start}</th><td className="whitespace-nowrap p-2">{p.end}</td>{[p.duration, p.max_tmax, p.mean_tmax, p.max_excess].map((v, j) => <td key={j} className="p-2">{fmt(v)}</td>)}</tr>)}</tbody>
        </table></div>}
    </section>
    <section className={panel}>
      <h3 className="font-semibold">Источник и происхождение</h3>
      <p className="text-xs text-muted">NASA POWER · T2M_MAX · сеточный продукт, не наблюдение отдельной станции. Метаданные API показаны без подмены.</p>
      <dl className="space-y-1 text-xs">{Object.entries(data.source).map(([key, value]) => <div key={key} className="break-words"><dt className="font-medium">{key}</dt><dd className="break-all">{typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}</dl>
    </section>
  </div>
}
