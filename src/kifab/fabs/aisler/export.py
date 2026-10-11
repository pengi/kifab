"""AISLER order files: one zip with gerbers, drill files, BOM and placement.

AISLER's BOM import matches parts by what the columns say (MPN, manufacturer,
distributor numbers) and asks the user to confirm its guesses, so the BOM
carries everything kifab knows of each part. UNVERIFIED: the exact column
names AISLER's import recognises (the user's trial with a hand-made BOM
matched well); the placement columns are KiCad's own position export.
"""

from __future__ import annotations

from kifab.core.manufacturing import Bom, BuildOutputs, FileArtifact, Placement
from kifab.core.partref import field_specs, sku_key
from kifab.fabs.files import csv_bytes, gerber_zip
from kifab.kicad.board import Side
from kifab.kicad.schematic import ref_sort_key

BOM_COLUMNS = ("Designator", "Quantity", "Value", "Footprint", "Manufacturer", "MPN")
CPL_COLUMNS = ("Ref", "Val", "Package", "PosX", "PosY", "Rot", "Side")
__all__ = ["bom_csv", "cpl_csv", "gerber_zip", "upload_zip"]


def upload_zip(
    built: BuildOutputs, order_files: tuple[FileArtifact, ...]
) -> FileArtifact:
    """``<board>_aisler.zip``: what is uploaded to AISLER in one go."""
    return gerber_zip(built, order_files, f"{built.board_name}_aisler.zip")


def _sku_titles() -> dict[str, str]:
    """Column title per supplier-number kind: its preferred field name."""
    return {s.key: s.names[0] for s in field_specs()}


def bom_csv(bom: Bom) -> bytes:
    kinds = sorted({kind for line in bom.lines for kind, _ in line.part.skus})
    titles = _sku_titles()
    header = [*BOM_COLUMNS, *(titles.get(sku_key(k), k) for k in kinds)]
    rows = [header]
    for line in bom.lines:
        rows.append(
            [
                ",".join(line.references),
                str(len(line.references)),
                line.value,
                line.footprint,
                line.part.manufacturer,
                line.part.mpn,
                *(line.part.sku(k) for k in kinds),
            ]
        )
    return csv_bytes(rows, quote_all=True)


def cpl_csv(placement: Placement) -> bytes:
    rows = [list(CPL_COLUMNS)]
    for part in sorted(placement.parts, key=lambda p: ref_sort_key(p.reference)):
        rows.append(
            [
                part.reference,
                part.value,
                part.package,
                f"{part.x:.4f}",
                f"{part.y:.4f}",
                f"{part.rotation:.4f}",
                "top" if part.side is Side.TOP else "bottom",
            ]
        )
    return csv_bytes(rows, quote_all=False)
