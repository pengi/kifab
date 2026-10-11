"""Typed results of a fab's parts catalogue (search, lookup, availability)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LibraryType(Enum):
    """JLCPCB's part classes; other fabs map onto these."""

    BASIC = "basic"  # no setup fee
    PREFERRED = "preferred"  # "preferred extended": no setup fee
    EXTENDED = "extended"  # setup fee per part type
    UNKNOWN = "unknown"

    @property
    def setup_fee(self) -> bool:
        return self is LibraryType.EXTENDED


@dataclass(frozen=True)
class PriceBreak:
    min_qty: int
    max_qty: int | None  # None: no upper bound
    unit_price: float  # USD

    def covers(self, qty: int) -> bool:
        return qty >= self.min_qty and (self.max_qty is None or qty <= self.max_qty)


@dataclass(frozen=True)
class PartHit:
    """One part as the fab's catalogue describes it."""

    id: str  # the fab's part number, e.g. "C123456"
    mpn: str
    manufacturer: str
    package: str
    description: str
    category: str
    stock: int
    library_type: LibraryType
    prices: tuple[PriceBreak, ...] = ()
    datasheet: str = ""
    url: str = ""
    min_order: int = 1
    attributes: tuple[tuple[str, str], ...] = ()

    def unit_price(self, qty: int = 1) -> float | None:
        for brk in self.prices:
            if brk.covers(qty):
                return brk.unit_price
        return None

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "mpn": self.mpn,
            "manufacturer": self.manufacturer,
            "package": self.package,
            "description": self.description,
            "category": self.category,
            "stock": self.stock,
            "type": self.library_type.value,
            "unit_price": self.unit_price(1),
            "prices": [
                {"min": p.min_qty, "max": p.max_qty, "price": p.unit_price}
                for p in self.prices
            ],
            "datasheet": self.datasheet,
            "url": self.url,
        }


@dataclass(frozen=True)
class SearchPage:
    query: str
    hits: tuple[PartHit, ...]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return (
            (self.total + self.page_size - 1) // self.page_size if self.page_size else 0
        )


class CatalogError(Exception):
    """The fab's catalogue could not be reached or answered nonsense."""
