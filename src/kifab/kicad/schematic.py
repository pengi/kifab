"""Read KiCad schematics: placed symbols, their fields and LCSC numbers, sub-sheets."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from kifab.kicad import sexpr
from kifab.kicad.libid import LibId

#: Symbol fields that may hold an LCSC part number (compared case-insensitively).
LCSC_FIELDS = (
    "LCSC",
    "LCSC Part #",
    "LCSC Part",
    "LCSC#",
    "JLCPCB Part #",
    "JLCPCB",
    "JLC",
)
_LCSC = re.compile(r"\bC\d{2,}\b", re.IGNORECASE)
_REF_KEY = re.compile(r"([A-Za-z#_]*)(\d*)(.*)")


def ref_sort_key(ref: str) -> tuple[str, int, str]:
    """Natural order for designators: R2 before R10."""
    m = _REF_KEY.fullmatch(ref)
    assert m is not None  # the pattern matches any string
    return (m.group(1), int(m.group(2) or 0), m.group(3))


def sorted_refs(refs: Iterable[str]) -> list[str]:
    return sorted(set(refs), key=ref_sort_key)


def find_lcsc(props: dict[str, str], fields: Iterable[str] = LCSC_FIELDS) -> str | None:
    """The LCSC number in the first matching field, upper-cased, or ``None``."""
    wanted = {f.lower() for f in fields}
    for name, value in props.items():
        if name.lower() in wanted:
            m = _LCSC.search(value)
            if m:
                return m.group(0).upper()
    return None


@dataclass(frozen=True)
class PlacedSymbol:
    """One placed symbol unit, for one sheet instance (one reference designator)."""

    reference: str
    lib_id: LibId
    unit: int
    value: str
    footprint: str
    properties: dict[str, str]
    sheet: Path
    dnp: bool
    in_bom: bool
    on_board: bool
    is_power: bool
    lcsc: str | None

    @property
    def footprint_id(self) -> LibId | None:
        return LibId.parse(self.footprint) if self.footprint else None

    @property
    def is_virtual(self) -> bool:
        """Power symbols, flags and other ``#`` references: not real parts."""
        return self.is_power or self.reference.startswith("#")


@dataclass(frozen=True)
class LibSymbol:
    """A symbol definition cached in the schematic's ``lib_symbols`` section."""

    lib_id: LibId
    is_power: bool


@dataclass(frozen=True)
class Schematic:
    """A root schematic with all its sub-sheets."""

    root: Path
    sheets: tuple[Path, ...]
    symbols: tuple[PlacedSymbol, ...]
    lib_symbols: tuple[LibSymbol, ...]
    missing_sheets: tuple[str, ...] = ()

    def parts(self) -> list[PlacedSymbol]:
        """Real parts (no power/flag symbols), one entry per reference (first unit)."""
        seen: dict[str, PlacedSymbol] = {}
        for sym in self.symbols:
            if not sym.is_virtual and sym.reference not in seen:
                seen[sym.reference] = sym
        return [seen[r] for r in sorted_refs(seen)]


@dataclass
class _Walk:
    project: str | None
    fields: tuple[str, ...]
    sheets: list[Path] = field(default_factory=list)
    symbols: list[PlacedSymbol] = field(default_factory=list)
    lib_symbols: dict[LibId, LibSymbol] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def _instance_refs(node: sexpr.Node, project: str | None) -> list[tuple[str, int]]:
    """``(reference, unit)`` pairs of every sheet instance of a placed symbol."""
    out: list[tuple[str, int]] = []
    for inst in sexpr.children(node, "instances"):
        for proj in sexpr.children(inst, "project"):
            if project is not None and sexpr.atom(proj) != project:
                continue
            for path in sexpr.children(proj, "path"):
                ref = sexpr.atom(sexpr.first(path, "reference"))
                unit = sexpr.atom(sexpr.first(path, "unit"))
                if ref:
                    out.append((ref, int(unit) if unit and unit.isdigit() else 1))
    return out


def _read_sheet(path: Path, walk: _Walk) -> None:
    path = path.resolve()
    if path in walk.sheets:
        return
    walk.sheets.append(path)
    tree = sexpr.parse(path.read_text(encoding="utf-8"))
    libs = sexpr.first(tree, "lib_symbols")
    if libs is not None:
        for lib_sym in sexpr.children(libs, "symbol"):
            name = sexpr.atom(lib_sym)
            if name:
                lib_id = LibId.parse(name)
                is_power = sexpr.first(lib_sym, "power") is not None
                walk.lib_symbols.setdefault(lib_id, LibSymbol(lib_id, is_power))
    for node in tree:
        if not isinstance(node, list):
            continue
        kind = sexpr.head(node)
        if kind == "symbol" and sexpr.first(node, "lib_id") is not None:
            _add_symbol(node, path, walk)
        elif kind == "sheet":
            props = sexpr.properties(node)
            name = props.get("Sheetfile") or props.get("Sheet file")
            if not name:
                continue
            sub = path.parent / name
            if sub.is_file():
                _read_sheet(sub, walk)
            elif name not in walk.missing:
                walk.missing.append(name)


def _add_symbol(node: sexpr.Node, sheet: Path, walk: _Walk) -> None:
    lib_id = LibId.parse(sexpr.atom(sexpr.first(node, "lib_id")) or "")
    props = sexpr.properties(node)
    unit_text = sexpr.atom(sexpr.first(node, "unit"))
    default_unit = int(unit_text) if unit_text and unit_text.isdigit() else 1
    instances = _instance_refs(node, walk.project)
    if not instances and sexpr.first(node, "instances") is None:
        # old files without an instances section: the field holds the reference
        instances = [(props.get("Reference", "?"), default_unit)]
    lib_sym = walk.lib_symbols.get(lib_id)
    is_power = lib_sym.is_power if lib_sym else lib_id.nickname == "power"
    for ref, unit in instances:
        walk.symbols.append(
            PlacedSymbol(
                reference=ref,
                lib_id=lib_id,
                unit=unit,
                value=props.get("Value", ""),
                footprint=props.get("Footprint", ""),
                properties=props,
                sheet=sheet,
                dnp=sexpr.flag(node, "dnp"),
                in_bom=sexpr.flag(node, "in_bom", default=True),
                on_board=sexpr.flag(node, "on_board", default=True),
                is_power=is_power,
                lcsc=find_lcsc(props, walk.fields),
            )
        )


def read_schematic(
    root: Path,
    project: str | None = None,
    fields: Iterable[str] = LCSC_FIELDS,
) -> Schematic:
    """Read ``root`` and every sub-sheet it references (cycle-safe).

    ``project`` limits the sheet instances to that project's (a sheet file can be
    shared between projects); ``None`` takes all of them.
    """
    walk = _Walk(project=project, fields=tuple(fields))
    # lib_symbols of the root are needed before its symbols are classified; they
    # come first in the file, so a single pass in file order is enough.
    _read_sheet(Path(root), walk)
    return Schematic(
        root=Path(root).resolve(),
        sheets=tuple(walk.sheets),
        symbols=tuple(walk.symbols),
        lib_symbols=tuple(sorted(walk.lib_symbols.values(), key=lambda s: s.lib_id)),
        missing_sheets=tuple(walk.missing),
    )
