# 0014: Google basemap with our layers, live Google Places, and NASA-only solar

## Status
Accepted (Phase 12, with a demo key)

## Context
With a Google Maps key available, the product owner asked to "ingest Google Maps data
and show it in the application" instead of only OpenStreetMap, and to keep NASA POWER
for solar.

The Google Maps Platform terms (docs/verification.md item 1) constrain this:
- only place IDs may be stored, and other Places content is for live display;
- Google content may not be shown on a non-Google map.

What the key allows, checked on 27 Sep 2026:
- **Enabled:** Maps JavaScript API and Places API (New).
- **Not enabled:** the Solar API is off on the project.
- **Refused until billing is on:** Geocoding.

## Decision
1. **Basemap switch.** An OpenStreetMap / Google Maps switch on the Workspace and run
   pages, remembered per browser.
   - On Google Maps our own layers are drawn with deck.gl's `GoogleMapsOverlay`:
     existing chargers, demand/gap hexagons, candidate and plan sites, and the plan's
     service areas.
   - It runs in overlaid mode; interleaved mode needs a vector Map ID.
   - Our data on a Google map is allowed. Google data on our open map is not, and never
     happens.
2. **Live Google Places, never ingested.** For a selected site, "Google places within
   500 m" calls `GET /api/v1/google/places/nearby`.
   - The call is cost-guarded: Nearby Search Pro, global list price $32 per 1,000 after
     5,000 free a month, under the monthly INR cap.
   - The response is labelled `RESTRICTED_GOOGLE`, `stored: false`, with "Places data
     © Google".
   - It is shown only on the Google map. The database keeps the usage record, not the
     places.
3. **Solar stays on NASA POWER.** Google Solar is behind `GOOGLE_SOLAR_ENABLED`
   (default off).
   - When switched on, a failed call is retried after 10 minutes rather than cached for
     good.
4. **Keys never in URLs or errors.**
   - Google calls send the key in the `X-Goog-Api-Key` header where the API allows it.
   - Error messages are rebuilt from Google's error body (the URL is dropped) and
     redacted.
   - A regression test checks this. It was added after an error message briefly
     carried the key; that copy has been purged.

## Consequences
- **Candidate generation still uses OSM POIs.** Google Places can't be warehoused, so
  candidates stay on open data. Google places are a live cross-check in the UI, not a
  data source.
- **Google Solar needs the Solar API enabled** on the key's Cloud project, and
  `GOOGLE_SOLAR_ENABLED=true`.
- **Geocoding needs billing enabled** on the project.
- **Production needs two keys:** a browser key restricted by HTTP referrer, separate
  from the server key. The demo key is used for both locally.
