import { useState } from 'react'
import { apiGet } from '../api/client'
import RegionMap from './RegionMap'

const BOX_FIELDS = ['north', 'south', 'west', 'east']

/** City/state search, map picking and exact coordinates; all edit the same region. */
export default function RegionPicker({ region, onChange, maxBoxDeg }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [drawBox, setDrawBox] = useState(false)

  async function search() {
    if (query.trim().length < 2) return
    setSearching(true)
    setSearchError('')
    try {
      const data = await apiGet('/geocode/', { q: query.trim() })
      setResults(data.results)
      if (!data.results.length) setSearchError('No places found.')
    } catch (err) {
      setSearchError(err.message)
    } finally {
      setSearching(false)
    }
  }

  function pick(place) {
    onChange({ ...place.region, label: place.label })
    setResults([])
  }

  function setType(type) {
    if (type === region.type) return
    if (type === 'box') {
      const lat = region.lat, lon = region.lon
      onChange({ type, south: lat - 1, north: lat + 1, west: lon - 1, east: lon + 1, label: null })
    } else {
      onChange({ type, lat: (region.south + region.north) / 2, lon: (region.west + region.east) / 2, label: null })
    }
    setDrawBox(type === 'box')
  }

  const setField = (field) => (e) => onChange({ ...region, [field]: e.target.value === '' ? '' : Number(e.target.value), label: null })

  return (
    <fieldset className="panel">
      <legend><span className="step">1</span>Region</legend>

      {/* Not a <form>: this sits inside the analysis form. Enter searches instead of analysing. */}
      <div className="search" role="search">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              search()
            }
          }}
          placeholder="City or state, e.g. Rajasthan"
          maxLength={100}
          aria-label="Search a city or state"
        />
        <button type="button" onClick={search} disabled={searching}>{searching ? 'Searching' : 'Search'}</button>
      </div>
      {searchError && <p className="hint error">{searchError}</p>}
      {results.length > 0 && (
        <ul className="places">
          {results.map((p) => (
            <li key={`${p.lat},${p.lon},${p.label}`}>
              <button type="button" onClick={() => pick(p)}>
                <span>{p.label}</span>
                <small>{p.kind} · analysed as {p.region.type === 'box' ? 'area' : 'point'}</small>
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="segmented" role="radiogroup" aria-label="Region type">
        {['point', 'box'].map((t) => (
          <button key={t} type="button" role="radio" aria-checked={region.type === t}
            className={region.type === t ? 'on' : ''} onClick={() => setType(t)}>
            {t === 'point' ? 'Point' : 'Lat-lon box'}
          </button>
        ))}
      </div>

      <RegionMap region={region} drawBox={region.type === 'box' && drawBox} onChange={onChange} />
      <p className="hint">
        {region.type === 'point' ? 'Click the map to move the point.' : (
          <>
            <label className="inline">
              <input type="checkbox" checked={drawBox} onChange={(e) => setDrawBox(e.target.checked)} /> Drag on the map to draw a box
            </label>
            {' '}(max {maxBoxDeg}° per side)
          </>
        )}
      </p>

      <div className="coords">
        {(region.type === 'point' ? ['lat', 'lon'] : BOX_FIELDS).map((f) => (
          <label key={f}>
            {f}
            <input type="number" step="0.01" value={region[f]} onChange={setField(f)} />
          </label>
        ))}
      </div>
      {region.label && <p className="selected-place">{region.label}</p>}
    </fieldset>
  )
}
