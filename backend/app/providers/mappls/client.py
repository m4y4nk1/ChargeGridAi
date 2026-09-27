"""Mappls (MapmyIndia) adapter stub (Section 7.1 row 13).

Deliberately unimplemented: Mappls' terms forbid showing its content on,
or next to, any non-Mappls map (docs/verification.md item 8), and this app
has no Mappls-branded canvas. Wiring it up needs a terms review and a
product decision about where its data could legally appear.
"""

from app.providers.base import POI, LicenseClass, POICategory, Point, Polygon


class MapplsNotAvailable(RuntimeError):
    pass


class MapplsPOIProvider:
    name = "mappls"
    license_class = LicenseClass.RESTRICTED_COMMERCIAL

    async def search_nearby(
        self, center: Point, radius_m: int, categories: list[POICategory]
    ) -> list[POI]:
        raise MapplsNotAvailable(
            "Mappls adapter pending terms review (docs/verification.md item 8)"
        )

    async def search_text(self, query: str, bias: Polygon | None) -> list[POI]:
        raise MapplsNotAvailable(
            "Mappls adapter pending terms review (docs/verification.md item 8)"
        )
