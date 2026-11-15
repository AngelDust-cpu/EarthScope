import assert from 'node:assert/strict'
import { test } from 'node:test'
import { registerHooks, stripTypeScriptTypes } from 'node:module'
import { readFileSync } from 'node:fs'

// Dependency-free Node 24 harness. Load the actual TS client; emulate only Vite's
// build-time env replacement. All response fixtures below are SIMULATION only.
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith('.') && context.parentURL?.includes('/src/') && !specifier.endsWith('.ts'))
      return next(`${specifier}.ts`, context)
    return next(specifier, context)
  },
  load(url, context, next) {
    if (url.endsWith('.ts') && url.includes('/src/')) {
      const source = readFileSync(new URL(url), 'utf8').replaceAll('import.meta.env.VITE_API_BASE_URL', 'undefined')
      return { format: 'module', source: stripTypeScriptTypes(source), shortCircuit: true }
    }
    return next(url, context)
  },
})
const { completeAnnualMean, validProfileQuery, profileUrl, fetchProfile, fetchProfileCsv, profileQuery, profileKeys } = await import('../src/lib/profile.ts')
const { fetchPdf } = await import('../src/lib/pdf.ts')
const query = { latitude: 42.87, longitude: 74.6, start_year: 1990, end_year: 2025,
  baseline_start_year: 1991, baseline_end_year: 2020, hot_threshold: 30, cold_threshold: 0, rain_threshold: 20 }
const coverage = { min_year: 1981, max_year: 2025, min_years: 5 }
const partial = () => ({ latitude: query.latitude, longitude: query.longitude, start_year: 1990, end_year: 2025,
  baseline_start_year: 1991, baseline_end_year: 2020, thresholds: { hot_days: 30, cold_nights: 0, heavy_rain: 20 },
  sections: Object.fromEntries(profileKeys.map(key => [key, { status: key === 'heatwaves' ? 'unavailable' : 'error', data: null, message: 'SIMULATION: unavailable' }])),
  summary: [], limitations: ['SIMULATION fixture; not runtime observations'] })

test('exact unified endpoints and every editable input, including zero cold threshold', () => {
  const url = new URL(profileUrl(query), 'http://simulation.invalid')
  assert.equal(url.pathname, '/api/climate-profile')
  assert.equal(url.searchParams.size, 9)
  for (const [key, value] of Object.entries(query)) assert.equal(url.searchParams.get(key), String(value))
  assert.match(profileUrl(query, true), /\/api\/climate-profile\.csv\?/)
  assert.deepEqual(profileQuery(partial()), query)
})
test('coverage, complete span, baseline and thresholds enforce bounds', () => {
  assert.equal(validProfileQuery(query, coverage), true)
  for (const edit of [{ latitude: 91 }, { longitude: NaN }, { start_year: 2023 }, { end_year: 2026 },
    { baseline_start_year: 2021 }, { baseline_start_year: 1980 }, { baseline_end_year: 2026 },
    { hot_threshold: 101 }, { cold_threshold: -101 }, { rain_threshold: -1 }, { rain_threshold: 2001 }, { start_year: 1990.5 }])
    assert.equal(validProfileQuery({ ...query, ...edit }, coverage), false, JSON.stringify(edit))
  assert.equal(validProfileQuery({ ...query, baseline_start_year: 1981, baseline_end_year: 1981, rain_threshold: 0 }, coverage), true)
})
test('mean uses only complete finite annual values and preserves no-data null', () => {
  const series = [
    { value: 0, valid_days: 365, expected_days: 365 }, { value: 10, valid_days: 366, expected_days: 366 },
    { value: 1000, valid_days: 364, expected_days: 365 }, { value: null, valid_days: 0, expected_days: 365 },
  ]
  assert.deepEqual(completeAnnualMean({ series }), { mean: 5, count: 2 })
  assert.deepEqual(completeAnnualMean({ series: series.slice(2) }), { mean: null, count: 0 })
})
test('partial sections and unavailable heatwaves survive without zero fabrication', async () => {
  const original = globalThis.fetch
  const signal = new AbortController().signal
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(options.headers.Accept, 'application/json'); assert.equal(options.signal, signal)
      return Response.json(partial())
    }
    const data = await fetchProfile(query, { signal })
    assert.equal(data.sections.heatwaves.status, 'unavailable')
    assert.equal(data.sections.temperature.data, null)
    globalThis.fetch = async () => Response.json({ ...partial(), latitude: 1 })
    await assert.rejects(fetchProfile(query, { signal }), error => error.code === 'INVALID_RESPONSE')
    const malformed = partial(); delete malformed.sections.seasonal
    globalThis.fetch = async () => Response.json(malformed)
    await assert.rejects(fetchProfile(query, { signal }), error => error.code === 'INVALID_RESPONSE')
    globalThis.fetch = async () => Response.json({ error: { code: 'SOURCE_UNAVAILABLE', message: 'SIMULATION failure' } }, { status: 503 })
    await assert.rejects(fetchProfile(query, { signal }), error => error.status === 503 && error.code === 'SOURCE_UNAVAILABLE' && error.message === 'SIMULATION failure')
  } finally { globalThis.fetch = original }
})
test('CSV transport uses unified endpoint, propagates signal and rejects fake/empty downloads', async () => {
  const original = globalThis.fetch
  const signal = new AbortController().signal
  try {
    globalThis.fetch = async (url, options) => {
      assert.match(url, /climate-profile\.csv\?/); assert.equal(options.signal, signal); assert.equal(options.headers.Accept, 'text/csv')
      return new Response('SIMULATION,unit\n0,mm', { headers: { 'Content-Type': 'text/csv; charset=utf-8' } })
    }
    assert.match(await fetchProfileCsv(query, signal), /SIMULATION/)
    for (const response of [Response.json({}), new Response('', { headers: { 'Content-Type': 'text/csv' } })]) {
      globalThis.fetch = async () => response
      await assert.rejects(fetchProfileCsv(query, signal), error => error.code === 'INVALID_RESPONSE')
    }
  } finally { globalThis.fetch = original }
})
test('existing PDF transport negotiates one profile report with analyzed inputs and Detective context', async () => {
  const original = globalThis.fetch
  const signal = new AbortController().signal
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, profileUrl(profileQuery(partial())))
      assert.equal(options.method, 'GET'); assert.equal(options.signal, signal)
      assert.equal(options.headers.Accept, 'application/pdf')
      assert.equal(options.headers['X-EarthScope-Report'], 'detective')
      return new Response('%PDF-1.4 SIMULATION transport fixture, not a real report', {
        headers: { 'Content-Type': 'application/pdf', 'Content-Disposition': 'attachment; filename="profile.pdf"' },
      })
    }
    const result = await fetchPdf({ url: profileUrl(profileQuery(partial())) }, signal, true)
    assert.ok(result.blob.size > 0); assert.match(result.filename, /\.pdf$/)
    globalThis.fetch = async () => Response.json({ error: { message: 'SIMULATION PDF unavailable' } }, { status: 503 })
    await assert.rejects(fetchPdf({ url: profileUrl(query) }, signal), /SIMULATION PDF unavailable/)
  } finally { globalThis.fetch = original }
})
test('profile request forwards an already aborted signal without disguising cancellation', async () => {
  const original = globalThis.fetch
  const controller = new AbortController(); controller.abort()
  try {
    globalThis.fetch = async (url, options) => { options.signal.throwIfAborted() }
    await assert.rejects(fetchProfile(query, { signal: controller.signal }), error => error.name === 'AbortError')
  } finally { globalThis.fetch = original }
})
