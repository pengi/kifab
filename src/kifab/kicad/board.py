"""Read KiCad boards (``.kicad_pcb``) and footprint files (``.kicad_mod``)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from kifab.kicad import sexpr
from kifab.kicad.libid import LibId
from kifab.kicad.schematic import find_lcsc, sorted_refs


class Side(Enum):
    TOP = "top"
    BOTTOM = "bottom"


@dataclass(frozen=True)
class Pad:
    number: str
    kind: str  # smd, thru_hole, np_thru_hole, connect

    @property
    def drilled(self) -> bool:
        return self.kind in ("thru_hole", "np_thru_hole")


@dataclass(frozen=True)
class Footprint:
    """What a footprint node holds, on a board or in a library file.

    ``attributes`` are the words of ``(attr ...)``: ``smd``, ``through_hole``,
    ``board_only``, ``exclude_from_pos_files``, ``exclude_from_bom``, ``dnp`` ...
    """

    lib_id: LibId
    attributes: frozenset[str]
    models: tuple[str, ...]
    pads: tuple[Pad, ...]
    properties: dict[str, str]

    @property
    def smd(self) -> bool:
        return "smd" in self.attributes

    @property
    def through_hole(self) -> bool:
        return "through_hole" in self.attributes

    @property
    def has_drilled_pads(self) -> bool:
        return any(p.drilled for p in self.pads)


@dataclass(frozen=True)
class BoardFootprint:
    """A footprint placed on a board."""

    footprint: Footprint
    reference: str
    value: str
    side: Side
    x: float
    y: float
    rotation: float
    lcsc: str | None

    @property
    def lib_id(self) -> LibId:
        return self.footprint.lib_id

    @property
    def dnp(self) -> bool:
        return "dnp" in self.footprint.attributes

    @property
    def is_virtual(self) -> bool:
        """``board_only`` items (mounting holes, logos) and ``#`` references."""
        return "board_only" in self.footprint.attributes or self.reference.startswith(
            "#"
        )


@dataclass(frozen=True)
class Layer:
    number: int
    name: str  # canonical name, e.g. "F.SilkS"
    kind: str  # signal, power, mixed, jumper, user
    user_name: str | None  # e.g. "F.Silkscreen"

    @property
    def is_copper(self) -> bool:
        return self.name.endswith(".Cu")

    @property
    def display_name(self) -> str:
        return self.user_name or self.name


@dataclass(frozen=True)
class Board:
    path: Path
    layers: tuple[Layer, ...]
    footprints: tuple[BoardFootprint, ...]

    @property
    def copper_layers(self) -> tuple[str, ...]:
        """Copper layer names in stack order: F.Cu, In1.Cu, ..., B.Cu."""

        def order(name: str) -> int:
            if name == "F.Cu":
                return 0
            if name == "B.Cu":
                return 10_000
            return int(name[2:-3]) if name[2:-3].isdigit() else 5_000

        return tuple(sorted((l.name for l in self.layers if l.is_copper), key=order))

    def by_reference(self) -> dict[str, BoardFootprint]:
        """Real parts by reference (virtual items left out)."""
        return {fp.reference: fp for fp in self.footprints if not fp.is_virtual}

    def references(self) -> list[str]:
        return sorted_refs(self.by_reference())


def _float(text: str | None, default: float = 0.0) -> float:
    try:
        return float(text) if text is not None else default
    except ValueError:
        return default


def parse_footprint(node: sexpr.Node) -> Footprint:
    """``(footprint "lib:name" ...)`` (or legacy ``(module ...)``) -> :class:`Footprint`."""
    attr = sexpr.first(node, "attr")
    pads = tuple(
        Pad(number=sexpr.atom(p) or "", kind=sexpr.atom(p, 2) or "")
        for p in sexpr.children(node, "pad")
    )
    models = tuple(
        m for m in (sexpr.atom(n) for n in sexpr.children(node, "model")) if m
    )
    props = sexpr.properties(node)
    # KiCad 5 style text fields
    for text in sexpr.children(node, "fp_text"):
        kind, value = sexpr.atom(text), sexpr.atom(text, 2)
        if kind == "reference" and value is not None:
            props.setdefault("Reference", value)
        elif kind == "value" and value is not None:
            props.setdefault("Value", value)
    return Footprint(
        lib_id=LibId.parse(sexpr.atom(node) or ""),
        attributes=frozenset(sexpr.atoms(attr)) if attr is not None else frozenset(),
        models=models,
        pads=pads,
        properties=props,
    )


def _board_footprint(node: sexpr.Node) -> BoardFootprint:
    fp = parse_footprint(node)
    at = sexpr.first(node, "at")
    layer = sexpr.atom(sexpr.first(node, "layer")) or "F.Cu"
    return BoardFootprint(
        footprint=fp,
        reference=fp.properties.get("Reference", ""),
        value=fp.properties.get("Value", ""),
        side=Side.BOTTOM if layer == "B.Cu" else Side.TOP,
        x=_float(sexpr.atom(at, 1)),
        y=_float(sexpr.atom(at, 2)),
        rotation=_float(sexpr.atom(at, 3)),
        lcsc=find_lcsc(fp.properties),
    )


def _layers(tree: sexpr.Node) -> tuple[Layer, ...]:
    node = sexpr.first(tree, "layers")
    if node is None:
        return ()
    out: list[Layer] = []
    for item in node[1:]:
        if not isinstance(item, list) or len(item) < 3:
            continue
        number, name, kind = (sexpr.atom(item, i) for i in (0, 1, 2))
        if number is None or name is None or kind is None or not number.isdigit():
            continue
        out.append(Layer(int(number), name, kind, sexpr.atom(item, 3)))
    return tuple(out)


def read_board(path: Path) -> Board:
    tree = sexpr.parse(Path(path).read_text(encoding="utf-8"))
    return Board(
        path=Path(path).resolve(),
        layers=_layers(tree),
        footprints=tuple(
            _board_footprint(n) for n in sexpr.children(tree, "footprint")
        ),
    )


def read_footprint_file(path: Path) -> Footprint:
    """Read a library footprint (``.kicad_mod``, current or legacy ``module`` format)."""
    tree = sexpr.parse(Path(path).read_text(encoding="utf-8"))
    if sexpr.head(tree) not in ("footprint", "module"):
        raise sexpr.SexprError(f"{Path(path).name} is not a footprint file")
    return parse_footprint(tree)
