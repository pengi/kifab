"""Quick search for passives: ``R 4k7``, ``C18p 0402``, ``L 10u``.

A query starting with R, C or L and a value (``4k7``, ``4.7k``, ``4700``,
``1R5``, ``18p``, ``100n``, ``4u7``; a unit such as ``Ω``, ``F`` or ``H`` may
follow) becomes a catalogue search for that value, with the rest of the text as
extra search terms (``R 4k7 0603 1%``). Optionally the neighbouring E24 values
within ±10% are searched too. Hits are kept only when they are the right kind
of part with the asked value, then ranked: exact value first, then Basic,
Preferred and Extended parts, in stock first, cheapest first.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum

from kifab.core.parts import LibraryType, PartHit, SearchSort
from kifab.suppliers.base import Catalog


class PassiveKind(Enum):
    RESISTOR = "R"
    CAPACITOR = "C"
    INDUCTOR = "L"

    @property
    def unit(self) -> str:
        return {"R": "Ω", "C": "F", "L": "H"}[self.value]

    @property
    def categories(self) -> tuple[str, ...]:
        """Words of the catalogue category that mark this kind of part."""
        return {
            "R": ("resistor",),
            "C": ("capacitor",),
            "L": ("inductor", "choke"),
        }[self.value]

    @property
    def attributes(self) -> tuple[str, ...]:
        """Catalogue attribute names that may hold the value (first found wins)."""
        return {
            "R": ("Resistance",),
            "C": ("Capacitance",),
            "L": ("Inductance",),
        }[
            self.value
        ] + ("Value",)


_PREFIX = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}
_UNITS = ("ohms", "ohm", "\u03a9", "\u2126", "F", "H")  # Ω: omega, ohm sign
_VALUE = re.compile(
    r"^(?P<int>\d*)(?:(?P<dot>[.,])(?P<frac>\d+))?(?P<prefix>[pnuµμmkKMGRr])?"
    r"(?P<tail>\d*)$"
)
_QUERY = re.compile(
    r"^\s*(?P<kind>[RCLrcl])\s*(?P<value>[0-9][^\s]*)(?:\s+(?P<rest>.*))?$"
)

_HAS_PREFIX = re.compile(r"[pnuµμm]")

#: E24 mantissas (1.0 .. 9.1)
E24 = (
    1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
    3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1,
)  # fmt: skip
ALTERNATIVE_SPAN = 0.10


def parse_value(text: str) -> float | None:
    """``"4k7"``, ``"4.7kΩ"``, ``"4700"``, ``"1R5"``, ``"18pF"`` -> the value; or None.

    The prefix may stand for the decimal point (``4k7``, ``1R5``, ``4u7``);
    ``R``/``r`` means "units". A trailing unit (Ω, ohm, F, H) is ignored.
    """
    text = text.strip()
    for unit in _UNITS:
        if text.endswith(unit) and len(text) > len(unit):
            text = text[: -len(unit)]
            break
    m = _VALUE.match(text)
    if m is None:
        return None
    whole, frac, prefix, tail = m["int"], m["frac"], m["prefix"], m["tail"]
    if tail and (frac or not prefix):
        return None  # "4.7k7", or digits after nothing
    if not whole and not frac:
        return None
    digits = whole or "0"
    decimals = frac or tail or ""
    number = float(f"{digits}.{decimals}" if decimals else digits)
    if prefix and prefix not in "Rr":
        number *= _PREFIX[prefix]
    return float(f"{number:.6g}")  # 4.7 * 1e3 -> 4700.0 exactly


def format_value(value: float) -> str:
    """Engineering notation: 4700 -> ``"4.7k"``, 1.8e-11 -> ``"18p"``, 0 -> ``"0"``."""
    if value == 0:
        return "0"
    exponent = math.floor(math.log10(abs(value)) / 3) * 3
    exponent = max(-12, min(9, exponent))
    prefix = {-12: "p", -9: "n", -6: "u", -3: "m", 0: "", 3: "k", 6: "M", 9: "G"}
    mantissa = value / 10**exponent
    text = f"{mantissa:.3g}"
    if "e" in text:  # 1000 after rounding: next prefix up
        return format_value(round(mantissa) * 10**exponent)
    return text + prefix[exponent]


@dataclass(frozen=True)
class QuickQuery:
    kind: PassiveKind
    value: float
    extra: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return format_value(self.value) + self.kind.unit

    def keyword(self, value: float | None = None) -> str:
        """The catalogue search text for ``value`` (default: the asked one)."""
        text = format_value(self.value if value is None else value) + self.kind.unit
        return " ".join((text, *self.extra))


def parse_quick(text: str) -> QuickQuery | None:
    """``"R 4k7 0603"`` -> QuickQuery(RESISTOR, 4700, ("0603",)); None otherwise."""
    m = _QUERY.match(text)
    if m is None:
        return None
    kind = PassiveKind(m["kind"].upper())
    if kind is not PassiveKind.RESISTOR and not _HAS_PREFIX.search(m["value"]):
        return None  # "C900001" is a part number, and farads/henries need a prefix
    value = parse_value(m["value"])
    if value is None:
        return None
    rest = tuple((m["rest"] or "").split())
    return QuickQuery(kind, value, rest)


def alternatives(value: float, span: float = ALTERNATIVE_SPAN) -> list[float]:
    """E24 values within ``value`` ±``span``, nearest first (``value`` itself first)."""
    if value <= 0:
        return [value]
    found = {value}
    decade = math.floor(math.log10(value))
    for d in (decade - 1, decade, decade + 1):
        for m in E24:
            v = float(f"{m * 10**d:.6g}")
            if abs(v - value) <= span * value * (1 + 1e-9):
                found.add(v)
    return sorted(found, key=lambda v: (abs(v - value), v))


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-18)


def hit_value(hit: PartHit, kind: PassiveKind) -> float | None:
    attrs = dict(hit.attributes)
    for name in kind.attributes:
        if name in attrs:
            value = parse_value(attrs[name].split()[0] if attrs[name] else "")
            if value is not None:
                return value
    return None


def is_kind(hit: PartHit, kind: PassiveKind) -> bool:
    category = hit.category.lower()
    return not category or any(word in category for word in kind.categories)


@dataclass(frozen=True)
class QuickHit:
    hit: PartHit
    value: float
    deviation: float  # relative: +0.085 for 5.1k when 4.7k was asked

    @property
    def exact(self) -> bool:
        return self.deviation == 0


@dataclass(frozen=True)
class QuickResult:
    query: QuickQuery
    hits: tuple[QuickHit, ...]
    searched: tuple[str, ...]  # the catalogue queries made


_TYPE_RANK = {
    LibraryType.BASIC: 0,
    LibraryType.PREFERRED: 1,
    LibraryType.EXTENDED: 2,
    LibraryType.UNKNOWN: 3,
}


def _rank(q: QuickHit) -> tuple[bool, int, bool, float, float, str]:
    price = q.hit.unit_price(1)
    return (
        not q.exact,
        _TYPE_RANK[q.hit.library_type],
        q.hit.stock <= 0,
        abs(q.deviation),
        price if price is not None else math.inf,
        q.hit.id,
    )


def quick_search(
    catalog: Catalog,
    query: QuickQuery,
    *,
    with_alternatives: bool = False,
    basic_only: bool = False,
    page_size: int = 50,
    sort: SearchSort = SearchSort.RELEVANCE,
) -> QuickResult:
    """Search the catalogue for ``query`` (raises CatalogError)."""
    values = alternatives(query.value) if with_alternatives else [query.value]
    found: dict[str, QuickHit] = {}
    searched: list[str] = []
    for value in values:
        keyword = query.keyword(value)
        searched.append(keyword)
        page = catalog.search(
            keyword, page_size=page_size, basic_only=basic_only, sort=sort
        )
        for hit in page.hits:
            if hit.id in found or not is_kind(hit, query.kind):
                continue
            actual = hit_value(hit, query.kind)
            if actual is None or not any(_same(actual, v) for v in values):
                continue
            deviation = (
                0.0
                if _same(actual, query.value)
                else ((actual - query.value) / query.value if query.value else math.inf)
            )
            found[hit.id] = QuickHit(hit, actual, deviation)
    hits = tuple(sorted(found.values(), key=_rank))
    return QuickResult(query, hits, tuple(searched))
