"""JLCPCB's parts library: what JLCPCB assembles from (LCSC numbers)."""

from __future__ import annotations

from kifab.core.net import HttpClient, UrllibClient, offline_root
from kifab.suppliers.base import Catalog, Supplier
from kifab.suppliers.jlcpcb.search import FixtureClient, JlcpcbCatalog


def default_client() -> HttpClient:
    root = offline_root()
    return FixtureClient(root) if root else UrllibClient()


class JlcpcbSupplier(Supplier):
    name = "jlcpcb"
    title = "JLCPCB"
    sku_kind = "lcsc"

    def catalog(self, client: HttpClient | None = None) -> Catalog:
        return JlcpcbCatalog(client or default_client())
