"""What a schematic symbol says about the physical part it stands for.

A part is identified by its manufacturer part number (MPN) and manufacturer;
suppliers sell it under their own numbers (SKUs: an LCSC number, a Mouser
number). Fabs pick what they need: JLCPCB the LCSC number, fabs that buy from
distributors the MPN. Field names are matched case-insensitively.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

#: Symbol fields that may hold the manufacturer part number.
MPN_FIELDS = (
    "MPN",
    "Manufacturer Part Number",
    "Manufacturer_Part_Number",
    "Mfr Part Number",
    "MFR Part #",
    "Mfr. No.",
)
#: Symbol fields that may hold the manufacturer.
MANUFACTURER_FIELDS = ("Manufacturer", "Manufacturer_Name", "MFR", "Mfr")
#: The field names kifab writes into vendored symbols.
MPN_FIELD, MANUFACTURER_FIELD = MPN_FIELDS[0], MANUFACTURER_FIELDS[0]

MPN_KEY, MANUFACTURER_KEY = "mpn", "manufacturer"
_SKU_PREFIX = "sku:"


def sku_key(kind: str) -> str:
    """The :class:`FieldSpec` key of a supplier number kind (``sku:lcsc``)."""
    return _SKU_PREFIX + kind


@dataclass(frozen=True)
class PartRef:
    mpn: str = ""
    manufacturer: str = ""
    skus: tuple[tuple[str, str], ...] = ()  # (kind, number), sorted by kind

    @classmethod
    def with_sku(cls, kind: str, number: str, mpn: str = "") -> PartRef:
        return cls(mpn=mpn, skus=((kind, number),) if number else ())

    def sku(self, kind: str) -> str:
        """The supplier number of ``kind`` (e.g. ``"lcsc"``), ``""`` if none."""
        return next((n for k, n in self.skus if k == kind), "")

    @property
    def empty(self) -> bool:
        return not (self.mpn or self.skus)

    @property
    def label(self) -> str:
        """Short text for messages: the first supplier number, else the MPN."""
        return self.skus[0][1] if self.skus else self.mpn

    def values(self) -> dict[str, str]:
        """``{FieldSpec key: value}`` of what is set."""
        out = {sku_key(k): n for k, n in self.skus}
        if self.mpn:
            out[MPN_KEY] = self.mpn
        if self.manufacturer:
            out[MANUFACTURER_KEY] = self.manufacturer
        return out

    @classmethod
    def from_values(cls, values: Mapping[str, str]) -> PartRef:
        """The inverse of :meth:`values` (empty values are dropped)."""
        skus = tuple(
            sorted(
                (k[len(_SKU_PREFIX) :], v.strip())
                for k, v in values.items()
                if k.startswith(_SKU_PREFIX) and v.strip()
            )
        )
        return cls(
            values.get(MPN_KEY, "").strip(),
            values.get(MANUFACTURER_KEY, "").strip(),
            skus,
        )


def _text(value: str) -> str | None:
    return value.strip() or None


@dataclass(frozen=True)
class FieldSpec:
    """Which symbol fields hold one piece of a :class:`PartRef`."""

    key: str  # "mpn", "manufacturer", "sku:<kind>"
    names: tuple[str, ...]  # candidate field names, preferred first
    parse: Callable[[str], str | None] = _text

    def read(self, props: Mapping[str, str]) -> str:
        """The value of the first matching field that parses, ``""`` if none."""
        wanted = {n.lower() for n in self.names}
        for name, value in props.items():
            if name.lower() in wanted and (found := self.parse(value)):
                return found
        return ""

    def field_name(self, props: Mapping[str, str]) -> str | None:
        """The name of the field :meth:`read` takes its value from."""
        wanted = {n.lower() for n in self.names}
        for name, value in props.items():
            if name.lower() in wanted and self.parse(value):
                return name
        return None


MPN_SPEC = FieldSpec(MPN_KEY, MPN_FIELDS)
MANUFACTURER_SPEC = FieldSpec(MANUFACTURER_KEY, MANUFACTURER_FIELDS)


def field_specs() -> tuple[FieldSpec, ...]:
    """MPN, manufacturer and every registered supplier's number fields."""
    from kifab.suppliers.base import available_suppliers

    by_kind: dict[str, FieldSpec] = {}
    for cls in available_suppliers().values():
        supplier = cls()
        if not supplier.sku_kind or not supplier.sku_fields:
            continue
        known = by_kind.get(supplier.sku_kind)
        if known is None:
            by_kind[supplier.sku_kind] = FieldSpec(
                sku_key(supplier.sku_kind), supplier.sku_fields, supplier.parse_sku
            )
        else:  # suppliers sharing a number kind: accept every field name
            names = known.names + tuple(
                n for n in supplier.sku_fields if n not in known.names
            )
            by_kind[supplier.sku_kind] = FieldSpec(known.key, names, known.parse)
    return (MPN_SPEC, MANUFACTURER_SPEC, *(by_kind[k] for k in sorted(by_kind)))


def part_ref(
    props: Mapping[str, str], specs: tuple[FieldSpec, ...] | None = None
) -> PartRef:
    """The :class:`PartRef` a symbol's fields describe."""
    specs = field_specs() if specs is None else specs
    return PartRef.from_values({s.key: s.read(props) for s in specs})
