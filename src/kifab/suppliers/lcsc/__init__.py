"""LCSC: the distributor (www.lcsc.com), by LCSC number.

Fabs that buy parts themselves (Aisler) list LCSC as a source. LCSC has no
public parts API kifab can use, so this supplier answers from JLCPCB's parts
search, which lists LCSC's parts by the same numbers: MPN, manufacturer,
package, description and price breaks match. UNVERIFIED: the stock is JLCPCB's
assembly stock, which may differ from what LCSC sells; JLCPCB's Basic/Extended
classes do not apply and are dropped.
"""

from __future__ import annotations

import dataclasses

from kifab.core.net import HttpClient
from kifab.core.parts import LibraryType, PartHit, SearchPage, SearchSort
from kifab.kicad.schematic import LCSC_FIELDS, find_lcsc
from kifab.suppliers.base import Catalog, Supplier
from kifab.suppliers.jlcpcb import default_client
from kifab.suppliers.jlcpcb.search import JlcpcbCatalog

PRODUCT_URL = "https://www.lcsc.com/product-detail/{id}.html"


def _as_lcsc(hit: PartHit) -> PartHit:
    return dataclasses.replace(
        hit, library_type=LibraryType.UNKNOWN, url=PRODUCT_URL.format(id=hit.id)
    )


class LcscCatalog:
    """:class:`Catalog` over JLCPCB's search, without JLCPCB's part classes."""

    def __init__(self, inner: Catalog) -> None:
        self.inner = inner

    def search(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 10,
        basic_only: bool = False,
        sort: SearchSort = SearchSort.RELEVANCE,
    ) -> SearchPage:
        found = self.inner.search(query, page=page, page_size=page_size, sort=sort)
        return dataclasses.replace(found, hits=tuple(_as_lcsc(h) for h in found.hits))

    def lookup(self, part_id: str) -> PartHit | None:
        hit = self.inner.lookup(part_id)
        return _as_lcsc(hit) if hit else None

    def lookup_mpn(self, mpn: str, manufacturer: str = "") -> PartHit | None:
        hit = self.inner.lookup_mpn(mpn, manufacturer)
        return _as_lcsc(hit) if hit else None

    def image(self, url: str) -> bytes:
        return self.inner.image(url)


class LcscSupplier(Supplier):
    name = "lcsc"
    title = "LCSC"
    sku_kind = "lcsc"
    sku_fields = LCSC_FIELDS

    def parse_sku(self, text: str) -> str | None:
        return find_lcsc({"LCSC": text}, ("LCSC",))

    def catalog(self, client: HttpClient | None = None) -> Catalog:
        return LcscCatalog(JlcpcbCatalog(client or default_client()))
