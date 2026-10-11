"""JLCPCB order files: gerber zip, BOM and placement (CPL) in JLCPCB's columns."""

from __future__ import annotations

import csv
import io
import re

from kifab.core.manufacturing import Bom, BuildOutputs, FileArtifact, Placement
from kifab.core.reproducible import deterministic_zip
from kifab.kicad.board import Side
from kifab.kicad.schematic import ref_sort_key

BOM_COLUMNS = ("Comment", "Designator", "Footprint", "LCSC Part #")
CPL_COLUMNS = ("Designator", "Mid X", "Mid Y", "Layer", "Rotation")
#: Files uploaded as "PCB": gerbers (incl. inner layers .g1, .g2 ...), job, drill.
_ZIP_MEMBER = re.compile(r".*\.(gtl|gbl|gts|gbs|gto|gbo|gtp|gbp|gm1|g\d+|gbrjob|drl)$")


def _csv(rows: list[list[str]], quote_all: bool) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(
        buf,
        quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def bom_csv(bom: Bom) -> bytes:
    rows = [list(BOM_COLUMNS)]
    for line in bom.lines:
        rows.append(
            [line.value, ",".join(line.references), line.footprint, line.part_number]
        )
    return _csv(rows, quote_all=True)


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
    return _csv(rows, quote_all=False)


def gerber_zip(built: BuildOutputs) -> FileArtifact:
    members = [
        (p.name, p.read_bytes())
        for p in (*built.gerbers, *built.drills, *([built.job] if built.job else []))
        if _ZIP_MEMBER.match(p.name)
    ]
    return FileArtifact(
        f"{built.board_name}_gerbers.zip", deterministic_zip(members, built.stamp)
    )
