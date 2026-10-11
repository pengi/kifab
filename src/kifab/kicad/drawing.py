"""The graphics of a footprint or a library symbol, as plain shapes for previews.

Reads what is needed to recognise a part: copper pads, outlines on the
silkscreen, fab and courtyard layers, and a symbol's body and pins. Text is
left out. Coordinates are millimetres in the file's own axes (footprints: y
down; symbols: y up, see :attr:`Drawing.y_up`).

Footprint arcs come in two syntaxes: ``(start)(mid)(end)`` (KiCad 6+) and
``(start <centre>)(end <start point>)(angle <deg>)`` (KiCad 5, which
easyeda2kicad writes); both become :class:`Arc` with three points.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from kifab.kicad import sexpr
from kifab.kicad.symbol_lib import SymbolLibrary


class Layer(Enum):
    COPPER = "copper"
    SILK = "silk"
    FAB = "fab"
    COURTYARD = "courtyard"
    BODY = "body"  # symbol graphics
    OTHER = "other"


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Segment:
    start: Point
    end: Point
    width: float
    layer: Layer


@dataclass(frozen=True)
class Circle:
    center: Point
    radius: float
    width: float
    layer: Layer
    filled: bool = False


@dataclass(frozen=True)
class Arc:
    start: Point
    mid: Point
    end: Point
    width: float
    layer: Layer


@dataclass(frozen=True)
class Polygon:
    points: tuple[Point, ...]
    width: float
    layer: Layer
    filled: bool = False
    closed: bool = True


@dataclass(frozen=True)
class Pad:
    number: str
    shape: str  # rect, roundrect, circle, oval, trapezoid, custom
    center: Point
    width: float
    height: float
    rotation: float  # degrees
    drill: float = 0.0  # 0: SMD


@dataclass(frozen=True)
class Pin:
    """A symbol pin: ``tip`` is where the wire connects."""

    tip: Point
    end: Point  # where it meets the body
    number: str
    name: str


Graphic = Segment | Circle | Arc | Polygon
Shape = Graphic | Pad | Pin


@dataclass(frozen=True)
class Bounds:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass(frozen=True)
class Drawing:
    shapes: tuple[Shape, ...]
    y_up: bool = False  # symbols: the y axis points up

    def points(self) -> list[Point]:
        out: list[Point] = []
        for s in self.shapes:
            if isinstance(s, Segment):
                out += [s.start, s.end]
            elif isinstance(s, Circle):
                c, r = s.center, s.radius
                out += [Point(c.x - r, c.y - r), Point(c.x + r, c.y + r)]
            elif isinstance(s, Arc):
                out += [s.start, s.mid, s.end]
            elif isinstance(s, Polygon):
                out += list(s.points)
            elif isinstance(s, Pad):
                r = math.hypot(s.width, s.height) / 2
                out += [
                    Point(s.center.x - r, s.center.y - r),
                    Point(s.center.x + r, s.center.y + r),
                ]
            else:
                out += [s.tip, s.end]
        return out

    def bounds(self) -> Bounds | None:
        pts = self.points()
        if not pts:
            return None
        return Bounds(
            min(p.x for p in pts),
            min(p.y for p in pts),
            max(p.x for p in pts),
            max(p.y for p in pts),
        )


def _num(text: str | None, default: float = 0.0) -> float:
    try:
        return float(text) if text is not None else default
    except ValueError:
        return default


def _point(node: sexpr.Node | None) -> Point | None:
    if node is None:
        return None
    return Point(_num(sexpr.atom(node, 1)), _num(sexpr.atom(node, 2)))


def _width(node: sexpr.Node) -> float:
    stroke = sexpr.first(node, "stroke")
    width = sexpr.first(stroke, "width") if stroke else sexpr.first(node, "width")
    return _num(sexpr.atom(width))


def _filled(node: sexpr.Node) -> bool:
    fill = sexpr.first(node, "fill")
    if fill is None:
        return False
    kind = sexpr.atom(sexpr.first(fill, "type")) or sexpr.atom(fill)
    return kind not in (None, "none", "no")


def _layer(name: str | None) -> Layer:
    if not name:
        return Layer.OTHER
    if name.endswith(".Cu"):
        return Layer.COPPER
    if name.endswith((".SilkS", ".Silkscreen")):
        return Layer.SILK
    if name.endswith(".Fab"):
        return Layer.FAB
    if name.endswith((".CrtYd", ".Courtyard")):
        return Layer.COURTYARD
    return Layer.OTHER


def _rotate(p: Point, around: Point, degrees: float) -> Point:
    a = math.radians(degrees)
    dx, dy = p.x - around.x, p.y - around.y
    return Point(
        around.x + dx * math.cos(a) - dy * math.sin(a),
        around.y + dx * math.sin(a) + dy * math.cos(a),
    )


def _points(node: sexpr.Node) -> tuple[Point, ...]:
    pts = sexpr.first(node, "pts")
    if pts is None:
        return ()
    return tuple(p for xy in sexpr.children(pts, "xy") if (p := _point(xy)))


def _fp_shape(node: sexpr.Node) -> Graphic | None:
    kind = sexpr.head(node).removeprefix("fp_")
    layer = _layer(sexpr.atom(sexpr.first(node, "layer")))
    width = _width(node)
    start = _point(sexpr.first(node, "start"))
    end = _point(sexpr.first(node, "end"))
    if kind == "line" and start and end:
        return Segment(start, end, width, layer)
    if kind == "rect" and start and end:
        corners = (start, Point(end.x, start.y), end, Point(start.x, end.y))
        return Polygon(corners, width, layer, _filled(node))
    if kind == "circle":
        center = _point(sexpr.first(node, "center")) or start
        if center and end:
            radius = math.hypot(end.x - center.x, end.y - center.y)
            return Circle(center, radius, width, layer, _filled(node))
    if kind == "arc" and start and end:
        mid = _point(sexpr.first(node, "mid"))
        if mid:
            return Arc(start, mid, end, width, layer)
        angle = sexpr.atom(sexpr.first(node, "angle"))
        if angle is not None:  # KiCad 5: start = centre, end = first point
            sweep = _num(angle)
            first = end
            return Arc(
                first,
                _rotate(first, start, sweep / 2),
                _rotate(first, start, sweep),
                width,
                layer,
            )
    if kind == "poly":
        pts = _points(node)
        if pts:
            return Polygon(pts, width, layer, _filled(node) or layer is Layer.COPPER)
    return None


def _pad(node: sexpr.Node) -> Pad | None:
    at = sexpr.first(node, "at")
    size = sexpr.first(node, "size")
    if at is None or size is None:
        return None
    drill = sexpr.first(node, "drill")
    drill_size = 0.0
    if drill is not None:
        values = [a for a in sexpr.atoms(drill) if a != "oval"]
        drill_size = _num(values[0]) if values else 0.0
    return Pad(
        number=sexpr.atom(node) or "",
        shape=sexpr.atom(node, 3) or "rect",
        center=Point(_num(sexpr.atom(at, 1)), _num(sexpr.atom(at, 2))),
        width=_num(sexpr.atom(size, 1)),
        height=_num(sexpr.atom(size, 2)),
        rotation=_num(sexpr.atom(at, 3)),
        drill=drill_size,
    )


def footprint_drawing(node: sexpr.Node) -> Drawing:
    """The shapes of a ``(footprint ...)`` or legacy ``(module ...)`` node."""
    shapes: list[Shape] = []
    for child in node[1:]:
        if not isinstance(child, list):
            continue
        name = sexpr.head(child)
        if name == "pad":
            pad = _pad(child)
            if pad is not None:
                shapes.append(pad)
        elif name.startswith("fp_") and name != "fp_text":
            shape = _fp_shape(child)
            if shape is not None and shape.layer is not Layer.OTHER:
                shapes.append(shape)
    return Drawing(tuple(shapes))


def _sym_shape(node: sexpr.Node) -> Shape | None:
    kind = sexpr.head(node)
    width = _width(node)
    filled = _filled(node)
    if kind == "rectangle":
        start = _point(sexpr.first(node, "start"))
        end = _point(sexpr.first(node, "end"))
        if start and end:
            corners = (start, Point(end.x, start.y), end, Point(start.x, end.y))
            return Polygon(corners, width, Layer.BODY, filled)
    elif kind == "polyline":
        pts = _points(node)
        if pts:
            closed = len(pts) > 2 and pts[0] == pts[-1]
            return Polygon(pts, width, Layer.BODY, filled and closed, closed)
    elif kind == "circle":
        center = _point(sexpr.first(node, "center"))
        radius = _num(sexpr.atom(sexpr.first(node, "radius")))
        if center:
            return Circle(center, radius, width, Layer.BODY, filled)
    elif kind == "arc":
        start = _point(sexpr.first(node, "start"))
        mid = _point(sexpr.first(node, "mid"))
        end = _point(sexpr.first(node, "end"))
        if start and mid and end:
            return Arc(start, mid, end, width, Layer.BODY)
    elif kind == "pin":
        at = sexpr.first(node, "at")
        if at is None:
            return None
        tip = Point(_num(sexpr.atom(at, 1)), _num(sexpr.atom(at, 2)))
        angle = math.radians(_num(sexpr.atom(at, 3)))
        length = _num(sexpr.atom(sexpr.first(node, "length")))
        end = Point(tip.x + length * math.cos(angle), tip.y + length * math.sin(angle))
        return Pin(
            tip,
            end,
            number=sexpr.atom(sexpr.first(node, "number")) or "",
            name=sexpr.atom(sexpr.first(node, "name")) or "",
        )
    return None


def symbol_drawing(node: sexpr.Node, unit: int = 1) -> Drawing:
    """Body and pins of a library ``(symbol ...)`` for ``unit`` (and shared parts).

    Sub-symbols are named ``<name>_<unit>_<style>``; unit 0 is common to all
    units and style 1 is the normal body style.
    """
    shapes: list[Shape] = []
    for sub in sexpr.children(node, "symbol"):
        name = sexpr.atom(sub) or ""
        parts = name.rsplit("_", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            if int(parts[1]) not in (0, unit) or int(parts[2]) not in (0, 1):
                continue
        for child in sub[1:]:
            if isinstance(child, list):
                shape = _sym_shape(child)
                if shape is not None:
                    shapes.append(shape)
    return Drawing(tuple(shapes), y_up=True)


def read_footprint_drawing(path: Path) -> Drawing:
    tree = sexpr.parse(Path(path).read_text(encoding="utf-8"))
    if sexpr.head(tree) not in ("footprint", "module"):
        raise sexpr.SexprError(f"{Path(path).name} is not a footprint file")
    return footprint_drawing(tree)


def library_symbol_drawing(library: SymbolLibrary, name: str) -> Drawing | None:
    """The drawing of symbol ``name``, following ``extends`` to its parent."""
    seen: set[str] = set()
    sym = library.get(name)
    while sym is not None and sym.extends and sym.name not in seen:
        seen.add(sym.name)
        sym = library.get(sym.extends)
    return symbol_drawing(sym.node) if sym is not None else None


def arc_points(arc: Arc, steps: int = 24) -> tuple[Point, ...]:
    """Points along ``arc`` (start, through mid, to end); a line if collinear."""
    (ax, ay), (bx, by), (cx, cy) = (
        (arc.start.x, arc.start.y),
        (arc.mid.x, arc.mid.y),
        (arc.end.x, arc.end.y),
    )
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return (arc.start, arc.end)
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / d
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / d
    r = math.hypot(ax - ux, ay - uy)
    a0 = math.atan2(ay - uy, ax - ux)
    am = math.atan2(by - uy, bx - ux)
    a1 = math.atan2(cy - uy, cx - ux)

    def ccw(frm: float, to: float) -> float:
        return (to - frm) % (2 * math.pi)

    sweep = ccw(a0, a1)
    if ccw(a0, am) > sweep:  # mid is not on the counter-clockwise way: go clockwise
        sweep -= 2 * math.pi
    return tuple(
        Point(
            ux + r * math.cos(a0 + sweep * i / steps),
            uy + r * math.sin(a0 + sweep * i / steps),
        )
        for i in range(steps + 1)
    )
