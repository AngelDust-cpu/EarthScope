import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

// Test the real interpreter with explicitly synthetic API fixtures.
const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../src/lib/detective.ts', import.meta.url))],
  bundle: true, format: 'esm', platform: 'node', write: false,
})
const { interpretTrend, interpretComparison } = await import(
  'data:text/javascript;base64,' + Buffer.from(bundle.outputFiles[0].contents).toString('base64')
)

const fixtures = (p, slope = 0.4) => ({
  variable: 'temperature', unit: '°C',
  trend: { years_used: 36, per_decade: slope, ci95_low: 0.001, ci95_high: 0.8, p_value: p },
  series: [{ value: 1 }, { value: null }],
})

test('strict p threshold and explicit distinction from no change', () => {
  assert.match(interpretTrend(fixtures(0.049999)).join(' '), /0,049999.*статистически значимый/)
  for (const p of [0.05, 0.050001, 1]) {
    assert.match(interpretTrend(fixtures(p)).join(' '), /недостаточно.*не доказывает отсутствия/)
  }
})

test('signed effect and degenerate interval', () => {
  assert.match(interpretTrend(fixtures(0.2, -0.4)).join(' '), /снижается.*−0,400 °C/)
  const flat = fixtures(1, 0)
  flat.trend.ci95_low = flat.trend.ci95_high = 0
  assert.match(interpretTrend(flat).join(' '), /нулевой наклон.*интервал вырожден/)
})

test('missing observations do not yield a trend or a difference', () => {
  const point = { ...fixtures(1), trend: null }
  assert.match(interpretTrend(point).join(' '), /полных лет — 1/)
  const text = interpretComparison({
    point_a: point, point_b: point, comparison: { slope_difference_per_decade: null, years_used: 1 },
  }).join(' ')
  assert.match(text, /Недостаточно общих полных лет.*доверительный интервал и p-значение для неё не рассчитаны/)
})

test('paired comparison reports sign and avoids claiming significance', () => {
  const text = interpretComparison({
    point_a: fixtures(0.01, -0.4), point_b: fixtures(0.8, -0.3), comparison: { slope_difference_per_decade: -0.1, years_used: 36 },
  }).join(' ')
  assert.match(text, /−0,100 °C.*Наклон B выше.*Разность описательная/)
  assert.match(text, /меняется быстрее в точке A/)
})
