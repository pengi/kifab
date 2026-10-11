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


class SearchSort(Enum):
    """The order the fab returns search results in (it decides what fits a page)."""

    RELEVANCE = "relevance"  # the fab's own order
    STOCK = "stock"  # most in stock first
    PRICE = "price"  # cheapest (1 pc) first


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
    images: tuple[str, ...] = ()  # product photo URLs, largest first

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
            "min_order": self.min_order,
            "attributes": [list(a) for a in self.attributes],
            "images": list(self.images),
        }

    @staticmethod
    def from_json(data: object) -> PartHit:
        """Inverse of :meth:`to_json` (raises ValueError on anything else)."""
        if not isinstance(data, dict):
            raise ValueError("a part is a JSON object")
        try:
            return PartHit(
                id=str(data["id"]),
                mpn=str(data["mpn"]),
                manufacturer=str(data["manufacturer"]),
                package=str(data["package"]),
                description=str(data["description"]),
                category=str(data["category"]),
                stock=int(data["stock"]),
                library_type=LibraryType(data["type"]),
                prices=tuple(
                    PriceBreak(
                        int(p["min"]),
                        None if p["max"] is None else int(p["max"]),
                        float(p["price"]),
                    )
                    for p in data["prices"]
                ),
                datasheet=str(data.get("datasheet", "")),
                url=str(data.get("url", "")),
                min_order=int(data.get("min_order", 1)),
                attributes=tuple(
                    (str(a[0]), str(a[1])) for a in data.get("attributes", [])
                ),
                images=tuple(str(u) for u in data.get("images", [])),
            )
        except (KeyError, TypeError, IndexError) as exc:
            raise ValueError(f"not a part: {exc!r}") from exc


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
