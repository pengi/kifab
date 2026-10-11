"""Typed manufacturing data passed between the bundle pipeline and the fab plugins."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kifab.kicad.board import Side
from kifab.kicad.schematic import sorted_refs

#: Column labels kifab asks kicad-cli for; fab plugins map them to their format.
BOM_VALUE, BOM_REFS, BOM_FOOTPRINT, BOM_PART = "Value", "Reference", "Footprint", "Part"


@dataclass(frozen=True)
class BomLine:
    value: str
    references: tuple[str, ...]
    footprint: str
    part_number: str  # the fab's part number (e.g. LCSC), "" when missing


@dataclass(frozen=True)
class Bom:
    lines: tuple[BomLine, ...]

    @property
    def part_count(self) -> int:
        return sum(len(line.references) for line in self.lines)

    def without(self, refs: set[str]) -> Bom:
        """The BOM without ``refs`` (lines that become empty are dropped)."""
        lines = []
        for line in self.lines:
            kept = tuple(r for r in line.references if r not in refs)
            if kept:
                lines.append(
                    BomLine(line.value, kept, line.footprint, line.part_number)
                )
        return Bom(tuple(lines))


@dataclass(frozen=True)
class PlacedPart:
    reference: str
    value: str
    package: str
    x: float  # mm, KiCad's position export (y up)
    y: float
    rotation: float  # degrees, as KiCad exports it
    side: Side


@dataclass(frozen=True)
class Placement:
    parts: tuple[PlacedPart, ...]

    def references(self) -> set[str]:
        return {p.reference for p in self.parts}

    def only(self, refs: set[str]) -> Placement:
        return Placement(tuple(p for p in self.parts if p.reference in refs))


@dataclass(frozen=True)
class FileArtifact:
    """A file to write into the bundle (deterministic content)."""

    name: str
    data: bytes


@dataclass(frozen=True)
class BuildOutputs:
    """What kicad-cli produced for one board, in the staging directory."""

    board_name: str
    gerbers: tuple[Path, ...]
    drills: tuple[Path, ...]
    job: Path | None
    others: tuple[Path, ...]  # drill maps etc.
    stamp: datetime | None = None  # the reproducible time stamp of this build


# ---------------------------------------------------------------- parsing


class ParseError(ValueError):
    pass


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def parse_kicad_bom(text: str) -> Bom:
    """``kicad-cli sch export bom`` CSV made with kifab's labels -> :class:`Bom`."""
    lines = []
    for row in _rows(text):
        try:
            refs = [r.strip() for r in row[BOM_REFS].split(",") if r.strip()]
            lines.append(
                BomLine(
                    value=row[BOM_VALUE],
                    references=tuple(sorted_refs(refs)),
                    footprint=row[BOM_FOOTPRINT],
                    part_number=row.get(BOM_PART, "").strip(),
                )
            )
        except KeyError as exc:
            raise ParseError(f"BOM export lacks column {exc}") from exc
    return Bom(tuple(line for line in lines if line.references))


def parse_kicad_pos(text: str) -> Placement:
    """``kicad-cli pcb export pos --format csv`` -> :class:`Placement`."""
    parts = []
    for row in _rows(text):
        try:
            side = row["Side"].strip().lower()
            parts.append(
                PlacedPart(
                    reference=row["Ref"],
                    value=row["Val"],
                    package=row["Package"],
                    x=float(row["PosX"]),
                    y=float(row["PosY"]),
                    rotation=float(row["Rot"]),
                    side=Side.BOTTOM if side == "bottom" else Side.TOP,
                )
            )
        except (KeyError, ValueError) as exc:
            raise ParseError(f"unexpected placement export row {row}: {exc}") from exc
    return Placement(tuple(parts))
