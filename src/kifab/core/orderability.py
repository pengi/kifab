"""Orderability: can the fab assemble this BOM, and what does it cost?

Per BOM line: the part exists, enough is in stock for ``boards`` boards, and
whether it is a Basic, Preferred or Extended part (Extended parts carry a setup
fee per part type). Uses the fab's live catalogue (``$KIFAB_OFFLINE`` for the
captured fixtures).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kifab.core.issues import Code, Issue, error, info, warning
from kifab.core.manufacturing import Bom, BomLine
from kifab.core.parts import CatalogError, LibraryType, PartHit
from kifab.core.project import Project
from kifab.suppliers.base import Catalog
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
        return {s.line.part_number: s.snapshot() for s in self.lines}


def check_availability(
    bom: Bom, catalog: Catalog, *, boards: int = 1
) -> AvailabilityReport:
    report = AvailabilityReport(boards=boards)
    for line in bom.lines:
        if not line.part_number:
            continue  # reported by Fab.check_parts
        refs = ",".join(line.references)
        needed = len(line.references) * boards
        try:
            hit = catalog.lookup(line.part_number)
        except CatalogError as exc:
            report.issues.append(error(Code.CATALOG_ERROR, str(exc), ref=refs))
            break  # the catalogue is unreachable; do not hammer it
        report.lines.append(LineStatus(line, needed, hit))
        pn = line.part_number
        if hit is None:
            report.issues.append(
                error(
                    Code.PART_NOT_FOUND, f"{pn} is not in the fab's catalogue", ref=refs
                )
            )
            continue
        if hit.stock < needed:
            report.issues.append(
                error(
                    Code.OUT_OF_STOCK,
                    f"{pn} ({hit.mpn}): {hit.stock} in stock, {needed} needed",
                    ref=refs,
                    hint="pick an alternative with `kifab component search`",
                )
            )
        elif hit.stock < needed * LOW_STOCK_MARGIN:
            report.issues.append(
                warning(
                    Code.LOW_STOCK,
                    f"{pn} ({hit.mpn}): only {hit.stock} in stock for {needed} needed",
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
    groups: dict[tuple[str, str, str], list[str]] = {}
    for part in sch.parts():
        if part.dnp or not part.in_bom or part.reference in tht:
            continue
        key = (part.value, part.footprint, part.lcsc or "")
        groups.setdefault(key, []).append(part.reference)
    lines = [
        BomLine(value, tuple(sorted_refs(refs)), footprint, pn)
        for (value, footprint, pn), refs in groups.items()
    ]
    lines.sort(key=lambda line: ref_sort_key(line.references[0]))
    return Bom(tuple(lines))
