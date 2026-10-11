"""JLCPCB's parts library: what JLCPCB assembles from (LCSC numbers)."""

from __future__ import annotations

from kifab.core.net import HttpClient, UrllibClient, offline_root
from kifab.kicad.schematic import LCSC_FIELDS, find_lcsc
from kifab.suppliers.base import Catalog, Supplier
from kifab.suppliers.jlcpcb.search import FixtureClient, JlcpcbCatalog


def default_client() -> HttpClient:
    root = offline_root()
    return FixtureClient(root) if root else UrllibClient()


class JlcpcbSupplier(Supplier):
    name = "jlcpcb"
    title = "JLCPCB"
    sku_kind = "lcsc"
    sku_fields = LCSC_FIELDS

    def parse_sku(self, text: str) -> str | None:
        return find_lcsc({"LCSC": text}, ("LCSC",))

    def catalog(self, client: HttpClient | None = None) -> Catalog:
        return JlcpcbCatalog(client or default_client())
