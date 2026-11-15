import { useEffect, useRef, useState, type ReactNode } from 'react'
import { downloadCsv } from '../lib/api'
import { completeAnnualMean, fetchProfileCsv, profileKeys, profileLabels, profileQuery, profileUrl, type ProfileResponse } from '../lib/profile'
import { formatCoordinate, formatValue } from '../lib/format'
import { displayError } from '../lib/ru'
import type { Coverage, TrendResponse } from '../lib/types'
import { AnomalyResult, SeasonalResult } from './ClimateResults'
import { DashboardBody } from './DashboardBody'
import { ExtremesResult } from './ExtremesResult'
import { PdfDownloadButton } from './PdfDownloadButton'
import { Button } from './ui/button'

const panel = 'min-w-0 space-y-2 rounded border border-line bg-card p-4'
const n = (value: number | null) => value === null ? '—' : formatCoordinate(value, 2)
const pValue = (value: number | null) => value === null ? '—' : value < 0.001 ? '< 0,001' : value.toFixed(3).replace('.', ',')

function AnnualSummary({ data }: { data: TrendResponse }) {
  const { mean, count } = completeAnnualMean(data)
  const t = data.trend
  const unit = data.variable === 'temperature' ? '°C' : 'мм'
  return <div className="space-y-2 text-sm">
    <p>{data.variable === 'temperature' ? 'Среднее годовых температур' : 'Средняя годовая сумма'}: <strong>{formatValue(mean, data.unit)}</strong> · полных лет {count}/{data.end_year - data.start_year + 1}.</p>
    {t ? <><p>Тренд МНК: <strong>{n(t.per_decade)} {unit} за десятилетие</strong> · 95% ДИ [{n(t.ci95_low)}; {n(t.ci95_high)}] · p = {pValue(t.p_value)} · лет в модели: {t.years_used}.</p>
      <p className="text-xs text-muted">{t.significant ? 'Модель указывает на ненулевой тренд при α = 0,05.' : 'Изменение не подтверждено при α = 0,05; это не доказательство отсутствия изменений.'}</p></>
      : <p>Недостаточно полных лет для тренда.</p>}
  </div>
}
function Disclosure({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return <details className={panel} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer rounded py-2 font-medium focus-visible:outline-2 focus-visible:outline-focus">{title} · графики, таблицы и источник</summary>
    {open && <div className="min-w-0 pt-3">{children}</div>}
  </details>
}
export function ProfileResults({ data, coverage }: { data: ProfileResponse; coverage: Coverage | null }) {
  const request = useRef<AbortController | null>(null)
  const [csvLoading, setCsvLoading] = useState(false)
  const [csvMessage, setCsvMessage] = useState('')
  const [csvError, setCsvError] = useState(false)
  useEffect(() => () => { request.current?.abort(); request.current = null }, [])
  async function exportCsv() {
    if (request.current) return
    const controller = new AbortController(); request.current = controller
    setCsvLoading(true); setCsvError(false); setCsvMessage('Подготовка CSV…')
    try {
      const csv = await fetchProfileCsv(profileQuery(data), controller.signal)
      if (controller.signal.aborted || request.current !== controller) return
      downloadCsv('EarthScope_climate_profile.csv', csv)
      setCsvMessage('CSV с единицами и источниками передан браузеру.')
    } catch (error) {
      if (controller.signal.aborted || request.current !== controller) return
      setCsvError(true); setCsvMessage(displayError(error instanceof Error ? error : new Error(String(error))))
    } finally { if (request.current === controller) { request.current = null; setCsvLoading(false) } }
  }
  const s = data.sections
  const annual = (result: TrendResponse) => <DashboardBody coverage={coverage} state={{ data: result, error: null, loading: false }} metaDescription="NASA POWER · сеточные данные · полные календарные годы UTC" onRetry={() => {}} />
  return <div className="min-w-0 space-y-4">
    <section className={panel}>
      <h3 className="text-lg font-semibold">Что удалось выяснить</h3>
      <p className="text-sm">{data.start_year}–{data.end_year} UTC · WGS84: {formatCoordinate(data.latitude)}, {formatCoordinate(data.longitude)}° · база {data.baseline_start_year}–{data.baseline_end_year}</p>
      <p className="text-xs text-muted">Фиксированные пороги: Tmax &gt; {data.thresholds.hot_days} °C; Tmin &lt; {data.thresholds.cold_nights} °C; осадки &gt; {data.thresholds.heavy_rain} мм/сут.</p>
      <p className="text-sm">NASA POWER — сеточный продукт, не измерение метеостанции и не прогноз. Средние ниже рассчитаны только по полным годам; пропуски не заменяются нулями.</p>
      <div className="flex flex-wrap items-start gap-2 pt-2">
        <PdfDownloadButton request={{ url: profileUrl(profileQuery(data)) }} label="Скачать единый PDF-профиль" />
        <Button type="button" variant="outline" size="sm" disabled={csvLoading} onClick={() => void exportCsv()}>{csvLoading ? 'Подготовка CSV…' : 'Скачать единый CSV'}</Button>
        {csvLoading && <Button type="button" variant="outline" size="sm" onClick={() => { request.current?.abort(); request.current = null; setCsvLoading(false); setCsvError(false); setCsvMessage('Загрузка CSV отменена.') }}>Отменить CSV</Button>}
      </div>
      <p role={csvError ? 'alert' : 'status'} className="text-xs text-muted">{csvMessage}</p>
      <p className="text-xs text-muted">Экспорт повторно рассчитывает профиль с теми же параметрами по доступным данным и кэшу NASA POWER. При обновлении источника значения могут отличаться от показанных. В файле сохраняются источник и время загрузки.</p>
      {data.summary.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm">{data.summary.map((text, i) => <li key={i}>{text}</li>)}</ul>}
    </section>
    <div className="grid min-w-0 gap-3 md:grid-cols-2">
      {(['temperature', 'precipitation'] as const).map(key => <section className={panel} key={key}>
        <h4 className="font-semibold">{profileLabels[key]}</h4>
        {s[key].status === 'ok' && s[key].data ? <AnnualSummary data={s[key].data} /> : <p className="text-sm">{s[key].status === 'error' ? 'Ошибка раздела.' : 'Раздел недоступен.'} {s[key].message}</p>}
      </section>)}
    </div>
    <div className="grid min-w-0 gap-3 md:grid-cols-2">
      <section className={panel}>
        <h4 className="font-semibold">Аномалии температуры</h4>
        {s.anomalies.data ? (() => {
          const a = s.anomalies.data
          const rows = a.series.filter(row => row.anomaly !== null)
          const high = rows.length ? Math.max(...rows.map(row => row.anomaly!)) : null
          const low = rows.length ? Math.min(...rows.map(row => row.anomaly!)) : null
          const years = (value: number) => rows.filter(row => row.anomaly === value).map(row => row.year).join(', ')
          return <div className="space-y-2 text-sm"><p>База {a.baseline_start_year}–{a.baseline_end_year}, средняя температура {formatValue(a.baseline_mean, a.unit)}. Полных базовых лет {a.baseline_complete_years}.</p>
            {high !== null && low !== null ? <><p>Самые тёплые годы среди доступных: {years(high)} ({n(high)} °C относительно базы).</p><p>Самые холодные: {years(low)} ({n(low)} °C относительно базы).</p></> : <p>Для отклонений недостаточно полной базы или годовых значений.</p>}</div>
        })() : <p className="text-sm">{s.anomalies.message}</p>}
      </section>
      <section className={panel}>
        <h4 className="font-semibold">Сезонные изменения температуры</h4>
        {s.seasonal.data ? (() => {
          const names: Record<string, string> = { winter: 'Зима', spring: 'Весна', summer: 'Лето', autumn: 'Осень' }
          const periods = s.seasonal.data.periods
          const ranked = periods.filter(p => p.trend).sort((a, b) => Math.abs(b.trend!.per_decade) - Math.abs(a.trend!.per_decade))
          const leading = ranked[0]
          return <div className="space-y-2 text-sm">{periods.map(p => <p key={p.period}>{names[p.period] ?? p.period}: {p.trend ? `${n(p.trend.per_decade)} °C за десятилетие, полных лет ${p.trend.years_used}, ${p.trend.significant ? 'поддержка по МНК при α = 0,05' : 'поддержка по МНК при α = 0,05 не получена'}` : 'недостаточно полных лет'}.</p>)}
            {leading && <p>Наибольшая по модулю оценка: {names[leading.period] ?? leading.period}. Различия наклонов между сезонами статистически не проверялись. Наборы полных лет могут различаться.</p>}</div>
        })() : <p className="text-sm">{s.seasonal.message}</p>}
      </section>
    </div>
    <section className={panel}>
      <h4 className="font-semibold">Пороговые события и ограничения</h4>
      <ul className="space-y-2 text-sm">
        {(['hot_days', 'cold_nights', 'heavy_rain'] as const).map(key => {
          const section = s[key], t = section.data?.trend
          const counts = section.data?.series.flatMap(row => row.events === null ? [] : [row.events]) ?? []
          const meanCount = counts.length ? counts.reduce((sum, value) => sum + value, 0) / counts.length : null
          return <li key={key}><strong>{profileLabels[key]}:</strong> {section.status !== 'ok' ? `${section.status === 'error' ? 'Ошибка раздела' : 'Недоступно'}. ${section.message ?? ''}`
            : `в среднем ${n(meanCount)} дней в год по ${counts.length} полным годам. ${t ? `Тренд ${n(t.per_decade)} дня на 365 дней за десятилетие; тренд годового числа ${n(t.count_per_decade)} дней за десятилетие; 95% ДИ нормированной частоты [${n(t.ci95_low)}; ${n(t.ci95_high)}]; p = ${t.p_value === null ? '—' : t.p_value}; полных лет ${t.years_used}.` : 'Недостаточно полных лет для тренда.'}`}</li>
        })}
        <li><strong>Волны жары: недоступны.</strong> Модуль не подключён. Жаркие дни по порогу не являются волнами жары. {s.heatwaves.message}</li>
      </ul>
      <p className="text-xs text-muted">Пороги абсолютные, не локальные процентили. ДИ и p зависят от предположений модели; отдельные тренды не доказывают различий между сезонами и не устанавливают причины изменений. Неподтверждённый тренд не означает отсутствия изменений.</p>
      {data.limitations.length > 0 && <details><summary className="cursor-pointer py-2 text-sm focus-visible:outline-2 focus-visible:outline-focus">Все ограничения сервиса ({data.limitations.length})</summary><ul className="list-disc space-y-1 pl-5 text-sm">{data.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></details>}
    </section>
    {profileKeys.filter(key => key !== 'heatwaves').map(key => {
      const section = s[key]
      if (section.status !== 'ok' || !section.data) return key === 'temperature' || key === 'precipitation' || key === 'hot_days' || key === 'cold_nights' || key === 'heavy_rain' ? null
        : <p key={key} className={panel}>{profileLabels[key]}: {section.status === 'error' ? 'ошибка раздела' : 'недоступно'}. {section.message}</p>
      return <Disclosure key={key} title={profileLabels[key]}>
        {key === 'temperature' && s.temperature.data && annual(s.temperature.data)}
        {key === 'precipitation' && s.precipitation.data && annual(s.precipitation.data)}
        {key === 'anomalies' && s.anomalies.data && <AnomalyResult data={s.anomalies.data} />}
        {key === 'seasonal' && s.seasonal.data && <SeasonalResult data={s.seasonal.data} />}
        {key === 'hot_days' && s.hot_days.data && <ExtremesResult data={s.hot_days.data} />}
        {key === 'cold_nights' && s.cold_nights.data && <ExtremesResult data={s.cold_nights.data} />}
        {key === 'heavy_rain' && s.heavy_rain.data && <ExtremesResult data={s.heavy_rain.data} />}
      </Disclosure>
    })}
  </div>
}
