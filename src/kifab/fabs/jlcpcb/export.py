"""JLCPCB order files: gerber zip, BOM and placement (CPL) in JLCPCB's columns."""

from __future__ import annotations

from kifab.core.manufacturing import Bom, Placement
from kifab.fabs.files import csv_bytes, gerber_zip
from kifab.kicad.board import Side
from kifab.kicad.schematic import ref_sort_key

BOM_COLUMNS = ("Comment", "Designator", "Footprint", "LCSC Part #")
CPL_COLUMNS = ("Designator", "Mid X", "Mid Y", "Layer", "Rotation")
__all__ = ["bom_csv", "cpl_csv", "gerber_zip"]  # gerber_zip: uploaded as "PCB"


def bom_csv(bom: Bom) -> bytes:
    rows = [list(BOM_COLUMNS)]
    for line in bom.lines:
        rows.append(
            [
                line.value,
                ",".join(line.references),
                line.footprint,
                line.part.sku("lcsc"),
            ]
        )
    return csv_bytes(rows, quote_all=True)


def _rotation(degrees: float) -> str:
    return f"{degrees % 360:g}"


def cpl_csv(placement: Placement) -> bytes:
    """KiCad's origin and rotation are used unchanged. That is right as long as the
    footprints are JLCPCB's own (EasyEDA) ones, whose origin and orientation JLCPCB
    assembles from (pcbfab.md section 3); no per-part corrections."""
    rows = [list(CPL_COLUMNS)]
    for part in sorted(placement.parts, key=lambda p: ref_sort_key(p.reference)):
        rows.append(
            [
                part.reference,
                f"{part.x:.4f}mm",
                f"{part.y:.4f}mm",
                "Top" if part.side is Side.TOP else "Bottom",
                _rotation(part.rotation),
            ]
        )
    return csv_bytes(rows, quote_all=False)
