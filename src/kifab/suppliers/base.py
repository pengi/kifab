"""The supplier plugin interface and registry.

A supplier sells parts: its catalogue gives search, lookup, stock and price.
It is independent of the fab that assembles the board (JLCPCB assembles from
its own parts library; other fabs buy from distributors) and of where the
part's symbol and footprint come from. Suppliers are plugins: a
:class:`Supplier` subclass registered under the entry-point group
``kifab.suppliers``; the built-ins are also available from a source tree.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib.metadata import entry_points
from typing import NamedTuple, Protocol

from kifab.core.net import HttpClient
from kifab.core.parts import PartHit, SearchPage, SearchSort

ENTRY_POINT_GROUP = "kifab.suppliers"


class UnknownSupplier(Exception):
    pass


class Catalog(Protocol):
    """A supplier's parts catalogue (online; ``$KIFAB_OFFLINE`` serves fixtures)."""

    def search(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 10,
        basic_only: bool = False,
        sort: SearchSort = SearchSort.RELEVANCE,
    ) -> SearchPage: ...

    def lookup(self, part_id: str) -> PartHit | None:
        """The part with exactly this number, or ``None`` if the supplier has none."""

    def lookup_mpn(self, mpn: str, manufacturer: str = "") -> PartHit | None:
        """The supplier's offer of this manufacturer part number, ``None`` if none.

        ``manufacturer``, when given, must match too (case-insensitive) where
        the supplier names one. With several offers, the one with most stock.
        """

    def image(self, url: str) -> bytes:
        """A product image of a hit (one of :attr:`PartHit.images`).

        Raises :class:`kifab.core.parts.CatalogError`.
        """


class SupplierCatalog(NamedTuple):
    """A supplier's catalogue, as the availability check asks it."""

    name: str  # the supplier's registry name
    title: str
    sku_kind: str
    catalog: Catalog


class Supplier(ABC):
    """A component supplier."""

    #: Registry key, used in ``kifab.toml`` (``[parts] suppliers``).
    name: str = ""
    #: Human readable name.
    title: str = ""
    #: The kind of part number the supplier sells by. Suppliers sharing a
    #: number space share the kind (JLCPCB's parts library and the LCSC shop
    #: both use LCSC numbers, ``"lcsc"``).
    sku_kind: str = ""
    #: Symbol fields that may hold such a number (case-insensitive), preferred
    #: first.
    sku_fields: tuple[str, ...] = ()

    def parse_sku(self, text: str) -> str | None:
        """The number in a field's text, normalised, or ``None``."""
        return text.strip() or None

    @abstractmethod
    def catalog(self, client: HttpClient | None = None) -> Catalog:
        """The catalogue; ``client`` replaces the default HTTP client."""


def _builtin() -> dict[str, type[Supplier]]:
    from kifab.suppliers.jlcpcb import JlcpcbSupplier
    from kifab.suppliers.lcsc import LcscSupplier

    return {JlcpcbSupplier.name: JlcpcbSupplier, LcscSupplier.name: LcscSupplier}


def available_suppliers() -> dict[str, type[Supplier]]:
    """Every registered supplier by name (built-ins plus installed plugins)."""
    suppliers = _builtin()
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        if ep.name in suppliers:
            continue
        cls = ep.load()
        if isinstance(cls, type) and issubclass(cls, Supplier):
            suppliers[ep.name] = cls
    return dict(sorted(suppliers.items()))


def get_supplier(name: str) -> Supplier:
    suppliers = available_suppliers()
    try:
        return suppliers[name]()
    except KeyError:
        known = ", ".join(suppliers) or "none"
        raise UnknownSupplier(f"unknown supplier {name!r} (known: {known})") from None
