import { formatSigned, formatValue } from './format'
import { unitLabel } from './ru'
import type { CompareResponse, TrendResponse } from './types'

const limitations = 'NASA POWER / MERRA-2 даёт оценки для ячеек сетки, а не измерения станции. Тренд описывает выбранный период и не устанавливает причину изменений.'
const ALPHA = 0.05
const alphaLabel = String(ALPHA).replace('.', ',')

function slopeExplanation(data: TrendResponse): string[] {
  const trend = data.trend
  if (!trend) {
    const complete = data.series.filter(point => point.value !== null).length
    return [`Недостаточно данных для тренда: полных лет — ${complete}, нужно не менее пяти. Годы с пропусками исключены без подстановки значений.`]
  }
  const metric = data.variable === 'temperature' ? 'среднегодовая температура' : 'годовая сумма осадков'
  const direction = trend.per_decade > 0 ? 'растёт' : trend.per_decade < 0 ? 'снижается' : 'имеет нулевой наклон'
  // Don't round a value just below 0.05 to “0.050” next to a significance claim.
  const p = trend.p_value < 0.001 ? '< 0,001' : `= ${String(trend.p_value).replace('.', ',')}`
  const significance = trend.p_value < ALPHA
    ? `При пороге ${alphaLabel} модель указывает на статистически значимый тренд при выполнении её предположений.`
    : `При пороге ${alphaLabel} данных недостаточно, чтобы статистически подтвердить тренд. Это не доказывает отсутствия изменений.`
  return [
    `По оценке модели ${metric} ${direction}: ${formatSigned(trend.per_decade, data.unit)} (полных лет: ${trend.years_used}).`,
    `95%-й доверительный интервал для наклона: от ${formatValue(trend.ci95_low, '')} до ${formatValue(trend.ci95_high, '')} ${unitLabel(data.unit)} за десятилетие. Он отражает неопределённость оценки скорости изменения при предположениях модели.`,
    // The API returns exactly equal bounds in its zero-residual convention.
    trend.ci95_low === trend.ci95_high
      ? 'У модели нет оценённого разброса остатков, поэтому интервал вырожден. Обычную интерпретацию p-значения в этом случае применять нельзя.'
      : `p ${p}. ${significance}`,
    'Интервал и p-значение предполагают линейный тренд, независимые остатки с постоянным разбросом и примерно нормальным распределением. Если соседние годы связаны, оценка неопределённости может измениться.',
    ...(trend.years_used < 10 ? ['Ряд короткий: интервал и p-значение особенно чувствительны к предположениям модели.'] : []),
  ]
}

export function interpretTrend(data: TrendResponse): string[] {
  return [...slopeExplanation(data), limitations]
}

export function interpretComparison(data: CompareResponse): string[] {
  const { slope_difference_per_decade: difference, years_used: years } = data.comparison
  const a = data.point_a.trend
  const b = data.point_b.trend
  let speed = 'Для сравнения скорости по модулю нужны оценки наклонов на одних и тех же годах.'
  if (a?.years_used === years && b?.years_used === years) {
    const magnitudeA = Math.abs(a.per_decade)
    const magnitudeB = Math.abs(b.per_decade)
    speed = magnitudeA === magnitudeB
      ? 'Оценки скорости изменения по модулю совпадают.'
      : `По оценке модели показатель меняется быстрее в точке ${magnitudeA > magnitudeB ? 'A' : 'B'}: там наклон больше по модулю, независимо от направления.`
  }
  const comparison = difference === null
    ? `Недостаточно общих полных лет для оценки разницы наклонов: ${years}, нужно не менее пяти.`
    : `Разность оценок наклона A − B: ${formatSigned(difference, data.point_a.unit)} по ${years} общим полным годам. ${difference > 0 ? 'Наклон A выше наклона B' : difference < 0 ? 'Наклон B выше наклона A' : 'Оценки наклонов совпадают'} на этих годах. Здесь сравнивается знак наклона: например, менее сильное снижение тоже даёт более высокий наклон.`
  return [
    comparison,
    'Разность описательная: доверительный интервал и p-значение для неё не рассчитаны. Данные соседних лет и двух точек могут быть связаны; значимость тренда в одной точке не доказывает различия между точками.',
    speed,
    ...slopeExplanation(data.point_a).map(text => `Точка A. ${text}`),
    ...slopeExplanation(data.point_b).map(text => `Точка B. ${text}`),
    limitations,
  ]
}
