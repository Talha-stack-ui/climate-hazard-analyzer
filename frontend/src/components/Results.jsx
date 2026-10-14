import { HazardDaysChart, MetricChart, SharedLegend } from './charts'

const fmt = (v, digits = 1) => (v === null || v === undefined ? '–' : Number(v).toFixed(digits))
const pValue = (p) => (p < 0.001 ? '< 0.001' : fmt(p, 3))
const pText = (p) => (p < 0.001 ? 'p < 0.001' : `p = ${fmt(p, 3)}`)
const signed = (v, digits = 2) => (v > 0 ? '+' : '') + fmt(v, digits)

const CSV_COLUMNS = ['year', 'events', 'hazard_days', 'longest_event', 'mean_intensity', 'annual_max',
  'hazard_days_5yr', 'events_5yr', 'coverage', 'included']

function downloadCsv(result, regionLabel) {
  const d = result.definition
  const meta = [
    `# ${d.label} analysis - ${regionLabel}`,
    `# Definition: ${d.text}`,
    `# Units: intensity = mean ${d.units} above threshold; annual_max in ${d.units}`,
    `# Headline: ${result.insights.headline}`,
    `# Source: ${result.data_quality.source}`,
  ]
  const rows = result.yearly.map((r) => CSV_COLUMNS.map((c) => r[c] ?? '').join(','))
  const quoted = meta.map((m) => `"${m.replaceAll('"', '""')}"`)
  const blob = new Blob([[...quoted, CSV_COLUMNS.join(','), ...rows].join('\n')], { type: 'text/csv' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${d.hazard}_${result.query.start_year}-${result.query.end_year}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

export default function Results({ result, regionLabel }) {
  const d = result.definition
  const days = result.trends.hazard_days
  const q = result.data_quality
  const intensityLabel = d.variable === 'tmax' ? 'Mean excess (°C)' : 'Mean excess (mm)'
  const maxLabel = d.variable === 'tmax' ? 'Hottest day (°C)' : 'Wettest day (mm)'

  return (
    <section className="results" aria-live="polite">
      <header className="card hero">
        <div className="hero-top">
          <p className="eyebrow">
            <span>{d.label}</span><span>{regionLabel}</span><span>{result.query.start_year}–{result.query.end_year}</span>
          </p>
          <div className="actions no-print">
            <button type="button" className="ghost" onClick={() => downloadCsv(result, regionLabel)}>
              <Icon d="M10 3v9m0 0-3.5-3.5M10 12l3.5-3.5M4 15.5h12" />CSV
            </button>
            <button type="button" className="ghost" onClick={() => window.print()}>
              <Icon d="M6 7V3h8v4M6 14H4V8h12v6h-2M6 11h8v6H6z" />PDF
            </button>
          </div>
        </div>
        <Verdict trend={days} />
        <h2>{result.insights.headline}</h2>
      </header>

      <div className="tiles">
        <Tile label={`${d.label} days trend`} value={days.sufficient ? `${signed(days.ols.slope_per_decade, 1)} / decade` : '–'}
          note={days.sufficient ? `${days.significant ? 'Significant' : 'Not significant'} · Mann-Kendall ${pText(days.mann_kendall.p_value)}` : 'Too few years'} />
        <Tile label="Threshold" value={`${fmt(d.threshold)} ${d.units}`}
          note={`${d.percentile}th percentile, ${d.baseline[0]}–${d.baseline[1]}`} />
        <Tile label="Events detected" value={result.event_count}
          note={`${d.min_duration}+ day${d.min_duration > 1 ? 's' : ''} above threshold`} />
        <Tile label="Data cleaned" value={`${q.outliers + q.implausible + q.interpolated} days`}
          note={`${q.days_missing} still missing · ${q.years_excluded.length} years excluded`} />
      </div>

      <figure className="card">
        <figcaption>
          <strong>{d.label} days per year</strong>
          <span>{d.text}</span>
        </figcaption>
        <HazardDaysChart result={result} />
      </figure>

      <figure className="card">
        <figcaption>
          <strong>Frequency, duration and intensity</strong>
          <SharedLegend />
        </figcaption>
        <div className="multiples">
          <Panel title="Events per year"><MetricChart result={result} metric="events" yTitle="events" /></Panel>
          <Panel title="Longest event (days)"><MetricChart result={result} metric="longest_event" yTitle="days" /></Panel>
          <Panel title={intensityLabel}><MetricChart result={result} metric="mean_intensity" yTitle={d.units} decimals={1} /></Panel>
          <Panel title={maxLabel}><MetricChart result={result} metric="annual_max" yTitle={d.units} decimals={1} /></Panel>
        </div>
      </figure>

      <div className="two-col">
        <section className="card">
          <h3>What the data says</h3>
          <ul>{result.insights.details.map((t) => <li key={t}>{t}</li>)}</ul>
          <div className="table-scroll"><TrendTable result={result} /></div>
        </section>
        <section className="card">
          <h3>Read with care</h3>
          <ul className="muted">{result.insights.caveats.map((t) => <li key={t}>{t}</li>)}</ul>
          <h3>Data</h3>
          <p className="muted small">
            {q.source}. Grid point{q.grid_points.length > 1 ? 's' : ''}:{' '}
            {q.grid_points.map((g) => `${fmt(g.lat, 2)}, ${fmt(g.lon, 2)} (${fmt(g.elevation, 0)} m)`).join('; ')}.
            {' '}{q.days} days analysed; {q.implausible} impossible values and {q.outliers} isolated spikes removed,
            {' '}{q.interpolated} short gaps interpolated.
            {result.cached ? ' Served from cache.' : ''}
          </p>
        </section>
      </div>

      <section className="card">
        <h3>Longest events</h3>
        <div className="table-scroll"><EventsTable events={result.events} units={d.units} /></div>
      </section>

      <details className="card">
        <summary>Yearly data table</summary>
        <YearlyTable result={result} />
      </details>
    </section>
  )
}

function Icon({ d }) {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="icon">
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/** Status pill: icon + label, never colour alone. Only "significant" earns a direction. */
function Verdict({ trend }) {
  if (!trend.sufficient) return <span className="verdict neutral">Not enough years for a trend</span>
  if (!trend.significant) {
    return <span className="verdict neutral"><Icon d="M4 10h12" />No significant trend</span>
  }
  const up = trend.ols.slope_per_decade > 0
  return (
    <span className={`verdict ${up ? 'up' : 'down'}`}>
      <Icon d={up ? 'M10 16V4m0 0-5 5m5-5 5 5' : 'M10 4v12m0 0-5-5m5 5 5-5'} />
      Significant {up ? 'increase' : 'decrease'}
    </span>
  )
}

export function ResultsSkeleton() {
  return (
    <div className="results" aria-label="Loading analysis">
      <div className="card hero"><div className="sk sk-line short" /><div className="sk sk-line" /><div className="sk sk-line mid" /></div>
      <div className="tiles">{[0, 1, 2, 3].map((i) => <div key={i} className="tile"><div className="sk sk-line short" /><div className="sk sk-block" /></div>)}</div>
      <div className="card"><div className="sk sk-chart" /></div>
    </div>
  )
}

function Tile({ label, value, note }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <span className="tile-value">{value}</span>
      <span className="tile-note">{note}</span>
    </div>
  )
}

function Panel({ title, children }) {
  return (
    <div className="panel-chart">
      <h4>{title}</h4>
      {children}
    </div>
  )
}

const METRIC_LABELS = {
  events: 'Events / yr', hazard_days: 'Hazard days / yr', longest_event: 'Longest event (days)',
  mean_intensity: 'Mean excess', annual_max: 'Annual maximum',
}

function TrendTable({ result }) {
  return (
    <table className="num">
      <thead>
        <tr><th>Metric</th><th>OLS / decade</th><th>95% CI</th><th>Sen / decade</th><th>MK p</th></tr>
      </thead>
      <tbody>
        {Object.entries(METRIC_LABELS).map(([k, label]) => {
          const t = result.trends[k]
          if (!t.sufficient) return <tr key={k}><td>{label}</td><td colSpan={4}>too few years ({t.n})</td></tr>
          return (
            <tr key={k} className={t.significant ? 'sig' : ''}>
              <td>{label}</td>
              <td>{signed(t.ols.slope_per_decade)}</td>
              <td>{signed(t.ols.ci95_per_decade[0])} to {signed(t.ols.ci95_per_decade[1])}</td>
              <td>{signed(t.sen_slope_per_decade)}</td>
              <td>{pValue(t.mann_kendall.p_value)}{t.significant ? ' *' : ''}</td>
            </tr>
          )
        })}
      </tbody>
      <caption>* significant at 5% (Mann-Kendall). Sen = Theil-Sen slope.</caption>
    </table>
  )
}

function EventsTable({ events, units }) {
  if (!events.length) return <p className="muted">No events in this period.</p>
  return (
    <table className="num">
      <thead><tr><th>Start</th><th>End</th><th>Days</th><th>Peak ({units})</th><th>Mean excess ({units})</th></tr></thead>
      <tbody>
        {events.map((e) => (
          <tr key={e.start}><td>{e.start}</td><td>{e.end}</td><td>{e.duration}</td><td>{fmt(e.peak)}</td><td>{signed(e.mean_excess)}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

function YearlyTable({ result }) {
  return (
    <div className="table-scroll">
      <table className="num">
        <thead>
          <tr><th>Year</th><th>Events</th><th>Days</th><th>Longest</th><th>Mean excess</th><th>Annual max</th><th>5-yr mean days</th><th>Coverage</th></tr>
        </thead>
        <tbody>
          {result.yearly.map((r) => (
            <tr key={r.year} className={r.included ? '' : 'excluded'}>
              <td>{r.year}</td><td>{r.events ?? '–'}</td><td>{r.hazard_days ?? '–'}</td><td>{r.longest_event ?? '–'}</td>
              <td>{fmt(r.mean_intensity, 2)}</td><td>{fmt(r.annual_max)}</td><td>{fmt(r.hazard_days_5yr)}</td>
              <td>{Math.round(r.coverage * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
