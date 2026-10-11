"""Where a project's parts come from: the fab, its suppliers and library source.

The three roles are independent plugins. A project combines them in
``kifab.toml``::

    fab = "aisler"
    [parts]
    suppliers = ["lcsc"]          # search, stock, price; preferred first
    library_source = "easyeda"    # symbols, footprints, 3D models
    library = "parts"             # nickname of lib/<name>/

Empty values mean the fab's defaults (JLCPCB: supplier ``jlcpcb``, library
source ``easyeda``, library ``jlcpcb``).
"""

from __future__ import annotations

from dataclasses import dataclass

from kifab.core.config import ConfigError, PartsConfig
from kifab.core.net import HttpClient
from kifab.fabs.base import Fab
from kifab.libraries.base import (
    LibrarySource,
    PartSource,
    UnknownLibrarySource,
    get_library_source,
)
from kifab.suppliers.base import (
    Catalog,
    Supplier,
    SupplierCatalog,
    UnknownSupplier,
    get_supplier,
)


@dataclass(frozen=True)
class Sourcing:
    fab: Fab
    suppliers: tuple[Supplier, ...]  # preferred first; at least one
    library: LibrarySource
    library_name: str  # nickname of the vendored library (lib/<name>/)

    @property
    def supplier(self) -> Supplier:
        """The preferred supplier (searches go to it)."""
        return self.suppliers[0]

    def catalog(self, client: HttpClient | None = None) -> Catalog:
        """The preferred supplier's catalogue."""
        return self.supplier.catalog(client)

    def supplier_catalogs(
        self, client: HttpClient | None = None
    ) -> tuple[SupplierCatalog, ...]:
        """Every supplier's catalogue, preferred first (availability checks)."""
        return tuple(
            SupplierCatalog(s.name, s.title, s.sku_kind, s.catalog(client))
            for s in self.suppliers
        )

    def part_source(self) -> PartSource:
        return self.library.part_source()

    def library_supplier(self) -> Supplier | None:
        """A configured supplier that sells by the library source's part numbers.

        Its catalogue describes what is vendored (descriptions, MPNs).
        """
        return next(
            (s for s in self.suppliers if s.sku_kind == self.library.sku_kind), None
        )

    def library_catalog(self, client: HttpClient | None = None) -> Catalog | None:
        supplier = self.library_supplier()
        return supplier.catalog(client) if supplier else None

    def with_supplier(self, name: str) -> Sourcing:
        """The same, searching ``name`` first (``--supplier``)."""
        try:
            first = get_supplier(name)
        except UnknownSupplier as exc:
            raise ConfigError(str(exc)) from None
        rest = tuple(s for s in self.suppliers if s.name != name)
        return Sourcing(self.fab, (first, *rest), self.library, self.library_name)


def sourcing_for(fab: Fab, parts: PartsConfig | None = None) -> Sourcing:
    """The fab's defaults, overridden by ``parts`` (raises :class:`ConfigError`)."""
    parts = parts or PartsConfig()
    names = parts.suppliers or fab.default_suppliers
    if not names:
        raise ConfigError(
            f"{fab.title} has no default supplier; name one in kifab.toml "
            "([parts] suppliers = [...])"
        )
    try:
        suppliers = tuple(get_supplier(n) for n in names)
        library = get_library_source(parts.library_source or fab.default_library_source)
    except (UnknownSupplier, UnknownLibrarySource) as exc:
        raise ConfigError(f"kifab.toml: {exc}") from None
    return Sourcing(fab, suppliers, library, parts.library or fab.library_name)
