import { useEffect, useRef } from 'react'
import Plotly from 'plotly.js-basic-dist-min'
import type { Config, Data, Layout } from 'plotly.js'

import { cn } from '../lib/cn'
import { ru, unitLabel, variableLabel } from '../lib/ru'
import type { SeriesPoint, Variable } from '../lib/types'

const FONT = 'IBM Plex Mono, ui-monospace, SFMono-Regular, Menlo, monospace'
const AXIS = '#ababab'
const GRID = '#343434'
const SERIES = '#acb7c0'
const FIT = '#ebebeb'
const SECOND_SERIES = '#e5a35e'

export interface TrendChartProps {
  series: SeriesPoint[]
  variable: Variable
  unit: string
  className?: string
  comparisonSeries?: SeriesPoint[]
  seriesLabel?: string
  yRange?: [number, number]
  anomaly?: boolean
  observationLabel?: string
}

/**
 * Basic-bundle Plotly scatter. Nulls are sent as `null` so Plotly leaves gaps
 * instead of interpolating across missing years, and no synthetic values are
 * drawn. The plot is re-rendered whenever the data identity changes.
 */
export function TrendChart({ series, variable, unit, className, comparisonSeries, seriesLabel, yRange, anomaly, observationLabel }: TrendChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const element = containerRef.current
    if (!element || series.length === 0) return

    const years = series.map((point) => point.year)
    const values = series.map((point) => point.value)
    const fitted = series.map((point) => point.fitted)
    const displayUnit = unitLabel(unit)

    const traces: Data[] = [
      {
        type: 'scatter',
        mode: 'lines+markers',
        name: `${observationLabel ?? (variable === 'temperature' ? ru.chart.mean : ru.chart.total)} (${displayUnit})`,
        x: years,
        y: values,
        xhoverformat: 'd',
        yhoverformat: ',.2f',
        customdata: series.map((point) => point.valid_days),
        hovertemplate: `%{x}: %{y:,.2f} ${displayUnit}<br>${ru.chart.validDays}: %{customdata}<extra></extra>`,
        line: { color: SERIES, width: 2, shape: 'linear' },
        marker: { color: SERIES, size: 5, line: { color: '#171717', width: 1 } },
        connectgaps: false,
      },
    ]

    if (fitted.some((value) => value !== null)) {
      const fitPoints = series
        .map((point, index) => ({ year: point.year, fitted: fitted[index] }))
        .filter((point): point is { year: number; fitted: number } => point.fitted !== null)

      traces.push({
        type: 'scatter',
        mode: 'lines',
        name: ru.chart.fit,
        x: fitPoints.map((point) => point.year),
        y: fitPoints.map((point) => point.fitted),
        hovertemplate: `${ru.chart.fit} · %{x}: %{y:,.2f} ${displayUnit}<extra></extra>`,
        line: { color: FIT, width: 2, dash: 'dash' },
      })
    }
    if (seriesLabel) {
      for (const trace of traces) trace.name = `${seriesLabel} · ${trace.name}`
    }
    if (comparisonSeries) {
      traces.push({
        type: 'scatter', mode: 'lines+markers', name: 'B · годовые значения',
        x: comparisonSeries.map(point => point.year), y: comparisonSeries.map(point => point.value),
        hovertemplate: `B · %{x}: %{y:,.2f} ${displayUnit}<extra></extra>`,
        line: { color: SECOND_SERIES, width: 2, dash: 'dot' },
        marker: { color: SECOND_SERIES, size: 5, symbol: 'diamond' }, connectgaps: false,
      })
      traces.push({
        type: 'scatter', mode: 'lines', name: `B · ${ru.chart.fit}`,
        x: comparisonSeries.map(point => point.year), y: comparisonSeries.map(point => point.fitted),
        hovertemplate: `B · ${ru.chart.fit} · %{x}: %{y:,.2f} ${displayUnit}<extra></extra>`,
        line: { color: SECOND_SERIES, width: 2, dash: 'dashdot' }, connectgaps: false,
      })
    }

    const layout: Partial<Layout> = {
      margin: { l: 51, r: 16, t: 74, b: 49 },
      autosize: true,
      separators: ', ',
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: FONT, size: 11, color: AXIS },
      showlegend: true,
      legend: {
        orientation: 'h',
        x: 0,
        y: 1.02,
        yanchor: 'bottom',
        font: { size: 11, color: AXIS },
        bgcolor: 'rgba(0,0,0,0)',
      },
      xaxis: {
        title: { text: ru.chart.year, font: { size: 11, color: AXIS }, standoff: 12 },
        tickformat: 'd',
        automargin: true,
        gridcolor: GRID,
        zeroline: false,
        linecolor: GRID,
        tickfont: { size: 10 },
        showspikes: true,
        spikecolor: '#626262',
        spikethickness: 1,
        spikedash: 'dot',
      },
      yaxis: {
        title: { text: `${anomaly ? 'Аномалия, ' : ''}${displayUnit}`, font: { size: 11, color: AXIS }, standoff: 12 },
        range: yRange,
        automargin: true,
        gridcolor: GRID,
        zeroline: false,
        linecolor: GRID,
        tickfont: { size: 10 },
      },
      hoverlabel: {
        bgcolor: '#1e1e1e',
        bordercolor: '#626262',
        font: { family: FONT, size: 11, color: SERIES },
      },
    }

    const config: Partial<Config> = {
      responsive: true,
      displaylogo: false,
      displayModeBar: true,
      modeBarButtons: [['zoom2d', 'pan2d', 'zoomIn2d', 'zoomOut2d', 'resetScale2d', 'toImage']],
      locale: 'ru',
      locales: {
        ru: { dictionary: {
          Zoom: ru.chart.zoom,
          Pan: ru.chart.pan,
          'Zoom in': ru.chart.zoomIn,
          'Zoom out': ru.chart.zoomOut,
          'Reset axes': ru.chart.reset,
          'Download plot as a PNG': ru.chart.image,
        } },
      },
      toImageButtonOptions: { format: 'png', scale: 2, filename: ru.chart.imageName },
    }

    let active = true
    const observer = new ResizeObserver(() => {
      void Promise.resolve(Plotly.Plots.resize(element)).catch(() => {})
    })
    void Plotly.react(element, traces, layout, config).then(() => {
      if (active) observer.observe(element)
    }).catch(() => {})
    return () => {
      active = false
      observer.disconnect()
    }
  }, [series, variable, unit, comparisonSeries, seriesLabel, yRange, anomaly, observationLabel])

  useEffect(() => {
    const element = containerRef.current
    return () => {
      if (element) void Plotly.purge(element)
    }
  }, [])

  return (
    <div
      ref={containerRef}
      role="img"
      aria-label={`${seriesLabel ?? ''}${comparisonSeries ? ' и B' : ''} · ${anomaly ? 'Аномалии · ' : ''}${ru.chart.label(observationLabel ?? variableLabel(variable), unitLabel(unit))}`}
      className={cn('h-full min-w-0 w-full overflow-hidden', className)}
    />
  )
}
