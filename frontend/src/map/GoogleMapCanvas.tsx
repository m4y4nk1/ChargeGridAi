/**
 * Google Maps canvas (ADR 0002): the only surface allowed to show RESTRICTED_GOOGLE
 * content (Google Solar results). Satellite imagery centred on a site, with markers for
 * the plan's sites. Without a browser key it says so instead of faking a map.
 */
import { APIProvider, Map, Marker } from '@vis.gl/react-google-maps'

export interface GoogleMarker {
  id: string
  lat: number
  lng: number
  title: string
  highlight?: boolean
}

export function GoogleMapCanvas({
  center,
  markers = [],
  zoom = 18,
}: {
  center: { lat: number; lng: number }
  markers?: GoogleMarker[]
  zoom?: number
}) {
  const key = import.meta.env.VITE_GOOGLE_MAPS_BROWSER_KEY as string | undefined
  if (!key) {
    return (
      <div
        role="status"
        className="flex h-full items-center justify-center bg-paper p-6 text-center text-sm text-muted"
      >
        Google Maps isn't configured: set VITE_GOOGLE_MAPS_BROWSER_KEY in .env and restart the web
        app.
      </div>
    )
  }
  return (
    <APIProvider apiKey={key}>
      <Map
        defaultCenter={center}
        defaultZoom={zoom}
        mapTypeId="hybrid"
        gestureHandling="greedy"
        streetViewControl={false}
        style={{ width: '100%', height: '100%' }}
      >
        {markers.map((m) => (
          <Marker
            key={m.id}
            position={{ lat: m.lat, lng: m.lng }}
            title={m.title}
            opacity={m.highlight ? 1 : 0.7}
          />
        ))}
      </Map>
    </APIProvider>
  )
}
