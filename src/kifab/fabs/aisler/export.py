"""AISLER order files: the upload zip, plus BOM and placement CSVs to read.

The zip has the layout AISLER's own KiCad plugin ("Push for KiCad",
github.com/AislerHQ/PushForKiCad, MIT) uploads and AISLER accepted from the
user: ``<board>-<layer>.gbr`` gerbers, Excellon drill files, ``netlist.ipc``
(IPC-D-356), ``components.json`` (parts with position, MPN, package) and
``odb.zip`` (the board as ODB++). A plain zip with gerbers and CSV files was
not accepted. The CSVs are for people (and other tools), not for AISLER.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from kifab.core.manufacturing import (
    Bom,
    BuildOutputs,
    FileArtifact,
    OrderFiles,
    Placement,
)
from kifab.core.partref import field_specs, sku_key
from kifab.core.reproducible import deterministic_zip
from kifab.fabs.files import csv_bytes
from kifab.kicad.board import Side
from kifab.kicad.schematic import ref_sort_key

BOM_COLUMNS = ("Designator", "Quantity", "Value", "Footprint", "Manufacturer", "MPN")
CPL_COLUMNS = ("Ref", "Val", "Package", "PosX", "PosY", "Rot", "Side")
__all__ = ["bom_csv", "components_json", "cpl_csv", "upload_zip"]


#: kicad-cli layer file names -> the names AISLER's KiCad plugin uploads.
_LAYER_NAMES = {"F_Silkscreen": "F_SilkS", "B_Silkscreen": "B_SilkS"}
NETLIST_NAME = "netlist.ipc"
COMPONENTS_NAME = "components.json"
ODB_NAME = "odb.zip"


def _gerber_name(board: str, path: Path) -> str:
    """``board-F_Silkscreen.gto`` -> ``board-F_SilkS.gbr`` (the plugin's names)."""
    layer = (
        path.stem[len(board) + 1 :] if path.stem.startswith(board + "-") else path.stem
    )
    return f"{board}-{_LAYER_NAMES.get(layer, layer)}.gbr"


def components_json(order: OrderFiles) -> bytes:
    """The parts to place, in the plugin's ``components.json`` layout.

    Positions in mm with y up, as KiCad's position export writes them (the
    plugin negates pcbnew's y the same way); ``mpn`` from the BOM (``null``
    when unknown). Only the parts in the placement: DNP parts and, unless
    assembled, through-hole parts are left out, as from the CPL.
    """
    mpn = {r: line.part.mpn for line in order.bom.lines for r in line.references}
    parts = sorted(order.placement.parts, key=lambda p: ref_sort_key(p.reference))
    return json.dumps(
        [
            {
                "pos_x": p.x,
                "pos_y": p.y,
                "rotation": p.rotation,
                "side": "top" if p.side is Side.TOP else "bottom",
                "designator": p.reference,
                "mpn": mpn.get(p.reference) or None,
                "pack": p.package,
                "value": p.value,
                "mount_type": "tht" if p.through_hole else "smt",
                "place": True,
            }
            for p in parts
        ]
    ).encode("utf-8")


def _odb_zip(odb: Path, stamp: datetime | None) -> bytes:
    members: list[tuple[str, bytes]] = []
    for path in sorted(odb.rglob("*")):
        rel = path.relative_to(odb).as_posix()
        members.append((rel + "/", b"") if path.is_dir() else (rel, path.read_bytes()))
    return deterministic_zip(members, stamp)


def upload_zip(built: BuildOutputs, order: OrderFiles) -> FileArtifact:
    """``<board>_aisler.zip``, laid out like what AISLER's KiCad plugin uploads.

    Gerbers (``<board>-<layer>.gbr``), drill files, the IPC-D-356 netlist,
    ``components.json`` (BOM and placement in one) and the board as ODB++.
    """
    board = built.board_name
    members = [(_gerber_name(board, p), p.read_bytes()) for p in built.gerbers]
    members += [(p.name, p.read_bytes()) for p in built.drills if p.suffix == ".drl"]
    if built.netlist is not None:
        members.append((NETLIST_NAME, built.netlist.read_bytes()))
    members.append((COMPONENTS_NAME, components_json(order)))
    if built.odb is not None:
        members.append((ODB_NAME, _odb_zip(built.odb, built.stamp)))
    return FileArtifact(f"{board}_aisler.zip", deterministic_zip(members, built.stamp))


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
