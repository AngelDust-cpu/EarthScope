import { downloadCsv } from '../lib/api'
import { regionalSeriesCsv, signed, trendUnit, type RegionalCell, type RegionalMapResponse } from '../lib/regionalMap'

const stat = (value: number | null | undefined) => value == null ? 'недоступно' : value.toLocaleString('ru-RU', { maximumSignificantDigits: 4 })

export function RegionalCellDetails({ data, cell, onExploreCell }: {
  data: RegionalMapResponse; cell: RegionalCell; onExploreCell?: (cell: RegionalCell) => void
}) {
  const p = cell.properties, m = data.metadata, t = p.trend
  const unit = m.unit === 'mm' ? 'мм' : m.unit
  const finite = p.annual.filter(a => a.value !== null)
  const values = finite.map(a => a.value!)
  const low = Math.min(...values), high = Math.max(...values)
  const x = (year: number) => 55 + (year - m.start_year) / Math.max(1, m.end_year - m.start_year) * 510
  const y = (v: number) => 150 - (v - low) / (high - low || 1) * 115
  // Each missing year starts a new segment; never interpolate across gaps.
  let previous: number | null = null
  const path = p.annual.map(a => {
    if (a.value === null) { previous = null; return '' }
    const command = previous !== null && a.year === previous + 1 ? 'L' : 'M'
    previous = a.year
    return `${command}${x(a.year)},${y(a.value)}`
  }).join(' ')
  return <section aria-label="Данные выбранной ячейки" className="rtm-details">
    <h3>{p.latitude.toFixed(3)}° с. ш., {p.longitude.toFixed(3)}° в. д.</h3>
    <p>{m.start_year}–{m.end_year} · {m.variable === 'temperature' ? 'Среднегодовая температура' : 'Годовая сумма осадков'} ({unit})</p>
    <p className="rtm-primary-value">{t ? `${signed(t.per_decade)} ${trendUnit(m.unit)}` : 'Тренд недоступен'}</p>
    <dl>
      <dt>95% ДИ · HAC(3)</dt><dd>{t?.ci95_low != null && t.ci95_high != null ? `${signed(t.ci95_low)} … ${signed(t.ci95_high)} ${trendUnit(m.unit)}` : 'недоступен'}</dd>
      <dt>p (HAC 3) / q (BY)</dt><dd>{stat(t?.p_value)} / {stat(t?.q_value_by)}</dd>
      <dt>Валидные годы</dt><dd>{p.valid_years} из {p.expected_years}</dd>
    </dl>
    <p>NASA POWER · {m.source.dataset} · {m.parameter} · {m.source.time_standard}</p>
    {onExploreCell && <button type="button" onClick={() => onExploreCell(cell)}>Исследовать эту точку</button>}
    {onExploreCell && <p className="rtm-source-note">Точечный анализ использует продукт Daily UTC. Значения могут отличаться от регионального слоя LST.</p>}
    {values.length ? <svg viewBox="0 0 600 200" role="img" aria-label={`Годовой ряд в ${m.unit}; пропуски не соединены. Точные значения в таблице ниже.`}>
      <text x="5" y="20" fill="currentColor" fontSize="12">{m.unit}</text>
      <text x="5" y="40" fill="currentColor" fontSize="11">{stat(high)}</text>
      <text x="5" y="154" fill="currentColor" fontSize="11">{stat(low)}</text>
      <path d="M55 30V160H565" stroke="#677280" fill="none" />
      <path d={path} fill="none" stroke="#8cb7d5" strokeWidth="2" />
      {finite.map(a => <circle key={a.year} cx={x(a.year)} cy={y(a.value!)} r="2.5" fill="#8cb7d5" />)}
      <text x="55" y="185" fill="currentColor" fontSize="12">{m.start_year}</text>
      <text x="535" y="185" fill="currentColor" fontSize="12">{m.end_year}</text>
    </svg> : <p>Нет полных годовых значений.</p>}
    <details><summary>Источник и годовые значения</summary>
    <dl>
      <dt>Максимальный пропуск</dt><dd>{p.max_gap_years} лет</dd>
      <dt>Качество</dt><dd>{({ complete: 'полные данные', incomplete: 'есть пропуски', insufficient_years: 'недостаточно лет' })[p.quality]}</dd>
      <dt>p: чувствительность HAC</dt><dd>{t?.hac_sensitivity.length ? t.hac_sensitivity.map(s => `лаг ${s.lags}: ${stat(s.p_value)}`).join('; ') : 'недоступна'}</dd>
    </dl>
    <p>Источник: {m.source.name}, {m.source.dataset}, API {m.source.api_version}; {m.parameter}, {m.source.time_standard}, {m.source.community}.
      {' '}Получено: {m.source.retrieved_at}. Кэш данных: {m.source.cached ? 'да' : 'нет'}; анализа: {m.source.analysis_cached ? 'да' : 'нет'}.</p>
    <p>Температура взята из годового поля NASA. Осадки рассчитаны как сумма месячных суточных интенсивностей, умноженных на число дней месяца.</p>
      <ul>{m.source.request_urls.map(url => <li key={url}><a href={url} target="_blank" rel="noreferrer">{url}</a></li>)}</ul>
      <table><caption>Годовой ряд, {m.unit}</caption><thead><tr><th>Год</th><th>Значение</th><th>Месяцев</th></tr></thead>
        <tbody>{p.annual.map(a => <tr key={a.year}><td>{a.year}</td><td>{stat(a.value)}</td><td>{a.valid_months}/12</td></tr>)}</tbody></table>
    </details>
    <div className="rtm-actions">
      <button type="button" onClick={() => downloadCsv(`regional-series-${p.cell_id}.csv`, regionalSeriesCsv(data, cell))}>CSV годового ряда</button>
    </div>
  </section>
}
