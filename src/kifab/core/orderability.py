"""Orderability: can the fab assemble this BOM, and what does it cost?

Per BOM line: the part exists at one of the project's suppliers, enough is in
stock for ``boards`` boards, and whether it is a Basic, Preferred or Extended
part at JLCPCB (Extended parts carry a setup fee per part type). Uses the
suppliers' live catalogues (``$KIFAB_OFFLINE`` for the synthetic fixtures).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from kifab.core.issues import Code, Issue, error, info, warning
from kifab.core.manufacturing import Bom, BomLine
from kifab.core.partref import PartRef, field_specs, part_ref
from kifab.core.parts import CatalogError, LibraryType, PartHit
from kifab.core.project import Project
from kifab.suppliers.base import SupplierCatalog
from kifab.kicad import sexpr
from kifab.kicad.board import read_board
from kifab.kicad.schematic import read_schematic, ref_sort_key, sorted_refs

#: Warn when the stock covers fewer than this many times the need.
LOW_STOCK_MARGIN = 3


@dataclass(frozen=True)
class LineStatus:
    line: BomLine
    needed: int
    hit: PartHit | None
    supplier: str = ""  # where ``hit`` is from

    @property
    def unit_price(self) -> float | None:
        return self.hit.unit_price(self.needed) if self.hit else None

    @property
    def cost(self) -> float | None:
        price = self.unit_price
        return price * self.needed if price is not None else None

    def snapshot(self) -> dict[str, object]:
        """What an order record keeps of this line."""
        hit = self.hit
        return {
            "stock": hit.stock if hit else 0,
            "type": hit.library_type.value if hit else "missing",
            "needed": self.needed,
            "unit_price": self.unit_price if self.unit_price is not None else 0.0,
            "supplier": self.supplier,
        }


@dataclass
class AvailabilityReport:
    boards: int
    lines: list[LineStatus] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def extended_types(self) -> list[str]:
        return sorted(
            {
                s.hit.id
                for s in self.lines
                if s.hit and s.hit.library_type is LibraryType.EXTENDED
            }
        )

    @property
    def parts_cost(self) -> float:
        return sum(s.cost or 0.0 for s in self.lines)

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {s.line.part.label: s.snapshot() for s in self.lines}


def _lookup(source: SupplierCatalog, part: PartRef) -> PartHit | None:
    """The part at one supplier: by its number, else by the MPN."""
    number = part.sku(source.sku_kind)
    if number:
        return source.catalog.lookup(number)
    if part.mpn:
        return source.catalog.lookup_mpn(part.mpn, part.manufacturer)
    return None


def check_availability(
    bom: Bom, sources: Sequence[SupplierCatalog], *, boards: int = 1
) -> AvailabilityReport:
    """Look each line up at the suppliers, preferred first.

    A line is found by the supplier's own number when the symbol has one, else
    by its MPN. The first supplier with enough stock is chosen, else the one
    with the most. A supplier that cannot be reached is not asked again.
    """
    report = AvailabilityReport(boards=boards)
    reachable = list(sources)
    for line in bom.lines:
        if line.part.empty:
            continue  # reported by Fab.check_parts
        refs = ",".join(line.references)
        needed = len(line.references) * boards
        found: list[tuple[SupplierCatalog, PartHit]] = []
        asked: list[SupplierCatalog] = []
        for source in list(reachable):
            try:
                hit = _lookup(source, line.part)
            except CatalogError as exc:
                report.issues.append(error(Code.CATALOG_ERROR, str(exc), ref=refs))
                reachable.remove(source)  # unreachable; do not hammer it
                continue
            asked.append(source)
            if hit is not None:
                found.append((source, hit))
                if hit.stock >= needed:
                    break
        if not asked:
            continue  # the catalogue errors are reported
        pn = line.part.label
        if not found:
            report.lines.append(LineStatus(line, needed, None))
            where = ", ".join(s.title for s in asked)
            report.issues.append(
                error(Code.PART_NOT_FOUND, f"{pn} is not found at {where}", ref=refs)
            )
            continue
        source, hit = next(
            ((s, h) for s, h in found if h.stock >= needed),
            max(found, key=lambda f: f[1].stock),
        )
        report.lines.append(LineStatus(line, needed, hit, source.name))
        at = f" at {source.title}" if len(sources) > 1 else ""
        if hit.stock < needed:
            report.issues.append(
                error(
                    Code.OUT_OF_STOCK,
                    f"{pn} ({hit.mpn}): {hit.stock} in stock{at}, {needed} needed",
                    ref=refs,
                    hint="pick an alternative with `kifab component search`",
                )
            )
        elif hit.stock < needed * LOW_STOCK_MARGIN:
            report.issues.append(
                warning(
                    Code.LOW_STOCK,
                    f"{pn} ({hit.mpn}): only {hit.stock} in stock{at} for {needed} "
                    "needed",
                    ref=refs,
                )
            )
        if hit.library_type is LibraryType.EXTENDED:
            report.issues.append(
                info(
                    Code.EXTENDED_PART,
                    f"{pn} ({hit.mpn}) is an Extended part (setup fee per type)",
                    ref=refs,
                )
            )
    return report


def schematic_bom(project: Project, *, assemble_through_hole: bool = False) -> Bom:
    """A BOM from the schematic alone (no kicad-cli): what the fab would assemble.

    DNP and not-in-BOM parts are left out; so are parts whose board footprint is
    through-hole, unless ``assemble_through_hole``.
    """
    kicad = project.kicad
    sch = read_schematic(kicad.sch, kicad.name)
    tht: set[str] = set()
    if not assemble_through_hole and kicad.pcb.is_file():
        try:
            board = read_board(kicad.pcb)
        except (OSError, sexpr.SexprError):
            board = None
        if board is not None:
            tht = {
                ref
                for ref, fp in board.by_reference().items()
                if fp.footprint.through_hole
            }
    specs = field_specs()
    groups: dict[tuple[str, str, PartRef], list[str]] = {}
    for part in sch.parts():
        if part.dnp or not part.in_bom or part.reference in tht:
            continue
        key = (part.value, part.footprint, part_ref(part.properties, specs))
        groups.setdefault(key, []).append(part.reference)
    lines = [
        BomLine(value, tuple(sorted_refs(refs)), footprint, ref)
        for (value, footprint, ref), refs in groups.items()
    ]
    lines.sort(key=lambda line: ref_sort_key(line.references[0]))
    return Bom(tuple(lines))
