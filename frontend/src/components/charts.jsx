import { useSyncExternalStore } from 'react'
import {
  BarController, BarElement, CategoryScale, Chart as ChartJS, Legend, LinearScale, LineController, LineElement,
  PointElement, Tooltip,
} from 'chart.js'
import { Chart } from 'react-chartjs-2'

// Mixed charts use the generic <Chart>, so the bar and line controllers must be registered explicitly.
ChartJS.register(BarController, BarElement, CategoryScale, LinearScale, LineController, LineElement, PointElement, Tooltip, Legend)

// Validated reference palette: series slots 1-2 plus ink/grid per mode.
const PALETTE = {
  light: { s1: '#2a78d6', s2: '#eb6834', trend: '#52514e', ink: '#0b0b0b', muted: '#898781', grid: '#e1e0d9', surface: '#ffffff' },
  dark: { s1: '#3987e5', s2: '#d95926', trend: '#c3c2b7', ink: '#f0efec', muted: '#898781', grid: '#2c2c2a', surface: '#1a1a19' },
}

const darkQuery = window.matchMedia('(prefers-color-scheme: dark)')
const subscribe = (cb) => {
  darkQuery.addEventListener('change', cb)
  return () => darkQuery.removeEventListener('change', cb)
}
function usePalette() {
  const dark = useSyncExternalStore(subscribe, () => darkQuery.matches)
  return PALETTE[dark ? 'dark' : 'light']
}

/** y = intercept + slope * year for the OLS fit, or null when there is no trend. */
function trendLine(years, trend) {
  if (!trend?.sufficient) return null
  const perYear = trend.ols.slope_per_decade / 10
  return years.map((y) => Math.round((trend.ols.intercept + perYear * y) * 100) / 100)
}

function baseOptions(c, yTitle) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { position: 'top', align: 'start', labels: { color: c.ink, boxWidth: 10, boxHeight: 10, padding: 14, sort: (a, b) => a.datasetIndex - b.datasetIndex } },
      tooltip: {
        backgroundColor: c.surface, titleColor: c.ink, bodyColor: c.ink, borderColor: c.grid, borderWidth: 1,
        padding: 10, cornerRadius: 8, boxPadding: 4, usePointStyle: true, titleFont: { weight: '600' },
      },
    },
    scales: {
      x: { ticks: { color: c.muted, maxRotation: 0, autoSkipPadding: 12 }, grid: { display: false }, border: { color: c.grid } },
      y: {
        beginAtZero: true,
        title: { display: true, text: yTitle, color: c.muted },
        ticks: { color: c.muted, precision: 0 },
        grid: { color: c.grid },
        border: { display: false },
      },
    },
  }
}

const lineStyle = (color) => ({
  type: 'line', borderColor: color, backgroundColor: color, borderWidth: 2,
  pointRadius: 0, pointHoverRadius: 4, spanGaps: false, tension: 0,
})

/** Hazard days per year (columns) with 5-year rolling mean and linear trend. */
export function HazardDaysChart({ result }) {
  const c = usePalette()
  const years = result.yearly.map((r) => r.year)
  const label = result.definition.label.toLowerCase()
  const trend = trendLine(years, result.trends.hazard_days)
  const datasets = [
    {
      type: 'bar', label: `${result.definition.label} days`, data: result.yearly.map((r) => r.hazard_days),
      backgroundColor: c.s1, maxBarThickness: 24, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: 'start', order: 3,
    },
    { ...lineStyle(c.s2), label: '5-year rolling mean', data: result.yearly.map((r) => r.hazard_days_5yr), order: 1 },
  ]
  if (trend) datasets.push({ ...lineStyle(c.trend), label: 'Linear trend', data: trend, order: 2 })
  return (
    <div className="chart tall" role="img" aria-label={`${label} days per year, ${years[0]} to ${years.at(-1)}`}>
      <Chart type="bar" data={{ labels: years, datasets }} options={baseOptions(c, 'days per year')} />
    </div>
  )
}

/** One small-multiple panel: a yearly metric and its linear trend. */
export function MetricChart({ result, metric, yTitle, decimals = 0 }) {
  const c = usePalette()
  const years = result.yearly.map((r) => r.year)
  const trend = trendLine(years, result.trends[metric])
  const datasets = [{
    ...lineStyle(c.s1), label: yTitle, data: result.yearly.map((r) => r[metric]),
    pointRadius: 2.5, pointBorderColor: c.surface, pointBorderWidth: 1,
  }]
  if (trend) datasets.push({ ...lineStyle(c.trend), label: 'Linear trend', data: trend })
  const options = baseOptions(c, yTitle)
  options.plugins.legend.display = false // shared legend above the grid
  options.scales.y.title.display = false  // the panel title names the unit
  options.scales.y.beginAtZero = metric !== 'annual_max'
  options.scales.y.ticks.precision = decimals
  return (
    <div className="chart" role="img" aria-label={`${yTitle} per year`}>
      <Chart type="line" data={{ labels: years, datasets }} options={options} />
    </div>
  )
}

export function SharedLegend() {
  const c = usePalette()
  return (
    <p className="legend">
      <span><i style={{ background: c.s1 }} /> Yearly value</span>
      <span><i style={{ background: c.trend }} /> Linear trend (OLS)</span>
    </p>
  )
}
