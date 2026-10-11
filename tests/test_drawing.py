import math

import pytest

from kifab.kicad import sexpr
from kifab.kicad.drawing import (
    Arc,
    Circle,
    Layer,
    Pad,
    Pin,
    Point,
    Polygon,
    Segment,
    footprint_drawing,
    library_symbol_drawing,
    read_footprint_drawing,
    symbol_drawing,
)
from kifab.kicad.symbol_lib import parse_symbol_library, read_symbol_library
from tests.conftest import FIXTURES

E2K = FIXTURES / "easyeda2kicad_output"


def test_legacy_footprint() -> None:
    drawing = read_footprint_drawing(
        E2K / "C900005/easyeda.pretty/MOD-SMD_KF1.kicad_mod"
    )
    pads = [s for s in drawing.shapes if isinstance(s, Pad)]
    assert [(p.number, p.center, p.width) for p in pads] == [
        ("1", Point(-0.8, 0), 0.9),
        ("2", Point(0.8, 0), 0.9),
    ]
    assert sum(isinstance(s, Segment) for s in drawing.shapes) == 4
    (arc,) = [s for s in drawing.shapes if isinstance(s, Arc)]
    # legacy: centre (0,-1.5), first point (-1,-1.5), 180 degrees
    assert arc.start == Point(-1, -1.5)
    assert arc.mid.x == pytest.approx(0) and arc.mid.y == pytest.approx(-2.5)
    assert arc.end.x == pytest.approx(1) and arc.end.y == pytest.approx(-1.5)
    bounds = drawing.bounds()
    assert bounds is not None and (bounds.width, bounds.height) == (4, 4)
    header = read_footprint_drawing(E2K / "C900006/easyeda.pretty/HDR-TH_8P.kicad_mod")
    th = [s for s in header.shapes if isinstance(s, Pad)]
    assert len(th) == 8 and all(p.drill == 1 for p in th)
    (mark,) = [s for s in header.shapes if isinstance(s, Circle)]
    assert mark.radius == pytest.approx(0.2) and mark.layer is Layer.SILK


def test_current_footprint_syntax() -> None:
    node = sexpr.parse(
        '(footprint "x" (layer "F.Cu")'
        ' (fp_line (start 0 0) (end 1 0) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))'
        ' (fp_arc (start 0 0) (mid 1 1) (end 2 0) (stroke (width 0.1)) (layer "F.Fab"))'
        ' (fp_rect (start 0 0) (end 2 1) (stroke (width 0.05)) (fill no) (layer "F.CrtYd"))'
        ' (fp_poly (pts (xy 0 0) (xy 1 0) (xy 1 1)) (stroke (width 0)) (fill yes) (layer "F.Cu"))'
        ' (fp_line (start 0 0) (end 1 1) (layer "Dwgs.User"))'
        ' (fp_text user "x" (at 0 0) (layer "F.SilkS"))'
        ' (pad "1" thru_hole oval (at 1 2 90) (size 1.7 1) (drill oval 1 0.5) (layers "*.Cu")))'
    )
    shapes = footprint_drawing(node).shapes
    assert isinstance(shapes[0], Segment) and shapes[0].width == 0.12
    assert isinstance(shapes[1], Arc) and shapes[1].layer is Layer.FAB
    assert isinstance(shapes[2], Polygon) and shapes[2].layer is Layer.COURTYARD
    assert not shapes[2].filled and len(shapes[2].points) == 4
    assert isinstance(shapes[3], Polygon) and shapes[3].filled
    assert isinstance(shapes[4], Pad) and shapes[4].rotation == 90
    assert shapes[4].drill == 1 and shapes[4].shape == "oval"
    assert len(shapes) == 5  # user drawings and text are left out


def test_symbol() -> None:
    lib = read_symbol_library(E2K / "C900005/easyeda.kicad_sym")
    drawing = library_symbol_drawing(lib, "KF-MOD-1")
    assert drawing is not None and drawing.y_up
    body = [s for s in drawing.shapes if isinstance(s, Polygon)]
    assert len(body) == 1 and body[0].filled
    pins = [s for s in drawing.shapes if isinstance(s, Pin)]
    assert [(p.number, p.tip) for p in pins] == [
        ("1", Point(-5.08, 0)),
        ("2", Point(5.08, 0)),
    ]
    assert pins[0].end == Point(-2.54, 0)
    assert pins[1].end.x == pytest.approx(2.54) and pins[1].end.y == pytest.approx(0)
    assert library_symbol_drawing(lib, "nope") is None


def test_symbol_units_and_extends() -> None:
    lib = parse_symbol_library(
        '(kicad_symbol_lib (version 20241209) (generator "t")'
        ' (symbol "A" (symbol "A_0_1" (circle (center 0 0) (radius 1) (stroke (width 0)) (fill (type none))))'
        '  (symbol "A_1_1" (pin input line (at 0 0 90) (length 1) (name "I") (number "1")))'
        '  (symbol "A_2_1" (pin input line (at 5 0 0) (length 1) (name "J") (number "2")))'
        '  (symbol "A_1_2" (polyline (pts (xy 0 0) (xy 1 1)))))'
        ' (symbol "B" (extends "A")))'
    )
    drawing = library_symbol_drawing(lib, "B")
    assert drawing is not None
    kinds = [type(s).__name__ for s in drawing.shapes]
    assert kinds == ["Circle", "Pin"]  # unit 1, normal style only
    pin = drawing.shapes[1]
    assert isinstance(pin, Pin) and pin.end.y == pytest.approx(1)
    unit2 = symbol_drawing(lib.symbols[0].node, unit=2)
    assert [s.number for s in unit2.shapes if isinstance(s, Pin)] == ["2"]
    assert math.isclose(
        next(s for s in drawing.shapes if isinstance(s, Circle)).radius, 1
    )


def test_arc_points() -> None:
    from kifab.kicad.drawing import arc_points

    arc = Arc(Point(1, 0), Point(0, 1), Point(-1, 0), 0, Layer.SILK)
    pts = arc_points(arc, 4)
    assert pts[0].x == pytest.approx(1) and pts[-1].x == pytest.approx(-1)
    assert pts[2].y == pytest.approx(1)  # through the top
    down = arc_points(Arc(Point(1, 0), Point(0, -1), Point(-1, 0), 0, Layer.SILK), 4)
    assert down[2].y == pytest.approx(-1)  # the other way round
    line = arc_points(Arc(Point(0, 0), Point(1, 0), Point(2, 0), 0, Layer.SILK))
    assert line == (Point(0, 0), Point(2, 0))
