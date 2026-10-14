import { useEffect, useState } from 'react'
import { CircleMarker, MapContainer, Rectangle, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'

const clampLat = (v) => Math.max(-85, Math.min(85, v))
const round = (v) => Math.round(v * 1000) / 1000

/** Click to pick a point; in box mode, drag to draw a lat-lon box. */
export default function RegionMap({ region, drawBox, onChange }) {
  const fields = region.type === 'point' ? [region.lat, region.lon] : [region.south, region.north, region.west, region.east]
  const drawable = fields.every((v) => typeof v === 'number' && Number.isFinite(v))
  return (
    <MapContainer className="map" center={[25, 75]} zoom={3} worldCopyJump>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <Interaction drawBox={drawBox} pointMode={region.type === 'point'} onChange={onChange} />
      {drawable && <FitRegion region={region} />}
      {drawable && (region.type === 'point' ? (
        <CircleMarker center={[region.lat, region.lon]} radius={7} pathOptions={{ weight: 2 }} />
      ) : (
        <Rectangle bounds={[[region.south, region.west], [region.north, region.east]]} pathOptions={{ weight: 2 }} />
      ))}
    </MapContainer>
  )
}

function Interaction({ drawBox, pointMode, onChange }) {
  const [anchor, setAnchor] = useState(null)
  const [corner, setCorner] = useState(null)
  const map = useMapEvents({
    click(e) {
      if (!pointMode) return
      const p = e.latlng.wrap()
      onChange({ type: 'point', lat: round(clampLat(p.lat)), lon: round(p.lng), label: null })
    },
    mousedown(e) {
      if (!drawBox) return
      setAnchor(e.latlng.wrap())
      setCorner(e.latlng.wrap())
    },
    mousemove(e) {
      if (anchor) setCorner(e.latlng.wrap())
    },
    mouseup(e) {
      if (!anchor) return
      const b = e.latlng.wrap()
      setAnchor(null)
      if (Math.abs(b.lat - anchor.lat) < 0.05 || Math.abs(b.lng - anchor.lng) < 0.05) return
      onChange({
        type: 'box',
        south: round(clampLat(Math.min(anchor.lat, b.lat))),
        north: round(clampLat(Math.max(anchor.lat, b.lat))),
        west: round(Math.min(anchor.lng, b.lng)),
        east: round(Math.max(anchor.lng, b.lng)),
        label: null,
      })
    },
  })

  useEffect(() => {
    if (drawBox) map.dragging.disable()
    else map.dragging.enable()
  }, [drawBox, map])

  return anchor && corner ? <Rectangle bounds={[anchor, corner]} pathOptions={{ weight: 1, dashArray: '4' }} /> : null
}

/** Bring the region into view after a search, or when typed coordinates leave the visible map.
    Clicks and drags happen inside the view, so they never move the map. */
function FitRegion({ region }) {
  const map = useMap()
  useEffect(() => {
    const bounds = region.type === 'box'
      ? [[region.south, region.west], [region.north, region.east]]
      : [[region.lat, region.lon], [region.lat, region.lon]]
    if (!region.label && map.getBounds().contains(bounds)) return
    if (region.type === 'box') map.fitBounds(bounds, { padding: [20, 20] })
    else map.setView([region.lat, region.lon], Math.max(map.getZoom(), 7))
  }, [region, map])
  return null
}
