import { useCallback, useEffect, useRef, useState } from 'react'
import { apiGet } from './api/client'
import RegionPicker from './components/RegionPicker'
import Results, { ResultsSkeleton } from './components/Results'

const DEFAULT_REGION = { type: 'point', lat: 28.61, lon: 77.21, label: 'New Delhi, India' }
const DEFAULT_PERIOD = { start_year: 1990, end_year: 2020 }

function regionParams(region) {
  return region.type === 'point'
    ? { lat: region.lat, lon: region.lon }
    : { south: region.south, north: region.north, west: region.west, east: region.east }
}

function regionLabel(region) {
  if (region.label) return region.label.split(',').slice(0, 2).join(',')
  return region.type === 'point'
    ? `${region.lat}, ${region.lon}`
    : `Box ${region.south}…${region.north}°N, ${region.west}…${region.east}°E`
}

function periodPresets(meta) {
  const last = meta.years.max
  return [
    { label: '1961–1990', start_year: 1961, end_year: 1990 },
    { label: '1991–2020', start_year: 1991, end_year: 2020 },
    { label: 'Last 30 yrs', start_year: last - 29, end_year: last },
    { label: `All (${meta.years.min}–)`, start_year: meta.years.min, end_year: last },
  ]
}

export default function App() {
  const [meta, setMeta] = useState(null)
  const [region, setRegion] = useState(DEFAULT_REGION)
  const [form, setForm] = useState({ hazard: 'heatwave', ...DEFAULT_PERIOD, percentile: '', min_duration: '' })
  const [result, setResult] = useState(null)
  const [resultLabel, setResultLabel] = useState('')
  const [status, setStatus] = useState({ loading: false, error: '' })
  const inflight = useRef(null)

  const analyse = useCallback(async (params, label) => {
    inflight.current?.abort()
    const controller = new AbortController()
    inflight.current = controller
    setStatus({ loading: true, error: '' })
    try {
      const data = await apiGet('/analysis/', params, { signal: controller.signal })
      setResult(data)
      setResultLabel(label)
      setStatus({ loading: false, error: '' })
    } catch (err) {
      if (err.name === 'AbortError') return
      const wait = err.retryAfter ? ` Try again in about ${err.retryAfter} s.` : ''
      setStatus({ loading: false, error: err.message + wait })
    }
  }, [])

  // Load limits/defaults from the API, then show the default region.
  useEffect(() => {
    const controller = new AbortController()
    apiGet('/meta/', {}, { signal: controller.signal })
      .then((m) => {
        setMeta(m)
        analyse({ hazard: 'heatwave', ...regionParams(DEFAULT_REGION), ...DEFAULT_PERIOD }, regionLabel(DEFAULT_REGION))
      })
      .catch((err) => err.name !== 'AbortError' && setStatus({ loading: false, error: `Can't reach the API. ${err.message}` }))
    return () => {
      controller.abort()
      inflight.current?.abort()
    }
  }, [analyse])

  const hazards = meta?.hazards ?? [{ id: 'heatwave', label: 'Heatwave' }]
  const hazard = hazards.find((h) => h.id === form.hazard)
  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value })
  const presetOn = (p) => Number(form.start_year) === p.start_year && Number(form.end_year) === p.end_year

  function submit(e) {
    e.preventDefault()
    analyse({ ...form, ...regionParams(region) }, regionLabel(region))
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg className="logo" viewBox="0 0 32 32" aria-hidden="true">
            <rect width="32" height="32" rx="8" fill="var(--accent)" />
            <path d="M6 22h4l3-9 4 13 3-8 2 4h4" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <div>
            <h1>Climate Hazard Trend Analyzer</h1>
            <p>How often do heatwaves and heavy rain happen here, and is that changing?</p>
          </div>
        </div>
        {meta && <span className="chip">ERA5 reanalysis · {meta.years.min}–{meta.years.max}</span>}
      </header>

      <div className="layout">
        <form className="controls no-print" onSubmit={submit}>
          <RegionPicker region={region} onChange={setRegion} maxBoxDeg={meta?.limits.max_box_deg ?? 15} />

          <fieldset className="panel">
            <legend><span className="step">2</span>Hazard and period</legend>

            <div className="segmented" role="radiogroup" aria-label="Hazard">
              {hazards.map((h) => (
                <button key={h.id} type="button" role="radio" aria-checked={form.hazard === h.id}
                  className={form.hazard === h.id ? 'on' : ''}
                  onClick={() => setForm({ ...form, hazard: h.id, percentile: '', min_duration: '' })}>
                  {h.label}
                </button>
              ))}
            </div>

            <div className="row">
              <label>
                From
                <input type="number" value={form.start_year} min={meta?.years.min} max={meta?.years.max} onChange={set('start_year')} required />
              </label>
              <label>
                To
                <input type="number" value={form.end_year} min={meta?.years.min} max={meta?.years.max} onChange={set('end_year')} required />
              </label>
            </div>
            {meta && (
              <div className="presets" aria-label="Period presets">
                {periodPresets(meta).map((p) => (
                  <button key={p.label} type="button" className={presetOn(p) ? 'on' : ''}
                    onClick={() => setForm({ ...form, start_year: p.start_year, end_year: p.end_year })}>
                    {p.label}
                  </button>
                ))}
              </div>
            )}

            <details className="advanced">
              <summary>Definition</summary>
              <div className="row">
                <label>
                  Percentile
                  <input type="number" step="0.1" min={meta?.limits.percentile[0]} max={meta?.limits.percentile[1]}
                    placeholder={hazard?.default_percentile} value={form.percentile} onChange={set('percentile')} />
                </label>
                <label>
                  Min. days
                  <input type="number" min={meta?.limits.min_duration[0]} max={meta?.limits.min_duration[1]}
                    placeholder={hazard?.default_min_duration} value={form.min_duration} onChange={set('min_duration')} />
                </label>
              </div>
              {meta && <p className="hint">Threshold = this percentile of daily values in the {meta.baseline[0]}–{meta.baseline[1]} baseline.</p>}
            </details>
          </fieldset>

          {/* Pinned to the bottom of the sidebar (desktop) or the screen (phone), so it never needs scrolling to. */}
          <div className="submit-bar">
            <button type="submit" className="primary" disabled={status.loading || !meta}>
              {status.loading && <span className="spinner" aria-hidden="true" />}
              {status.loading ? 'Analysing' : 'Analyse'}
            </button>
            {status.loading && <p className="hint center">First analysis of a place downloads decades of data; this can take a few seconds.</p>}
          </div>
        </form>

        <main aria-busy={status.loading}>
          {status.error && (
            <div className="callout" role="alert">
              <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="M10 5.5v5.5M10 13.6v.4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
              <div><strong>Couldn't run the analysis</strong><p>{status.error}</p></div>
            </div>
          )}
          {result ? (
            <div className={status.loading ? 'stale' : ''}>
              {status.loading && <div className="progress" aria-hidden="true" />}
              <Results result={result} regionLabel={resultLabel} />
            </div>
          ) : status.loading ? <ResultsSkeleton /> : !status.error && (
            <p className="placeholder">Choose a region and press Analyse.</p>
          )}
          <footer className="no-print">
            Weather data: <a href="https://open-meteo.com/">Open-Meteo</a> (ECMWF ERA5, CC BY 4.0) · Geocoding: © OpenStreetMap contributors
          </footer>
        </main>
      </div>
    </div>
  )
}
