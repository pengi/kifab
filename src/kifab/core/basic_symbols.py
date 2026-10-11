"""The ``kifab`` basic-symbols library shipped into every project at ``init``.

Power rails, ground and the power flag, so a schematic needs no KiCad stock
library. Simple drawings of kifab's own; written in the KiCad 9 symbol-library
format, which KiCad 10 reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kifab.kicad.sexpr import Node, Sym
from kifab.kicad.symbol_lib import LibrarySymbol, SymbolLibrary


class Shape(Enum):
    RAIL = "rail"  # arrow up: +3V3, VCC, ...
    GROUND = "ground"
    FLAG = "flag"


@dataclass(frozen=True)
class BasicSymbol:
    name: str
    shape: Shape
    description: str


BASIC_SYMBOLS = (
    BasicSymbol("+1V8", Shape.RAIL, "+1.8V supply rail"),
    BasicSymbol("+3V3", Shape.RAIL, "+3.3V supply rail"),
    BasicSymbol("+5V", Shape.RAIL, "+5V supply rail"),
    BasicSymbol("+12V", Shape.RAIL, "+12V supply rail"),
    BasicSymbol("VBUS", Shape.RAIL, "USB VBUS supply rail"),
    BasicSymbol("VCC", Shape.RAIL, "VCC supply rail"),
    BasicSymbol("VDD", Shape.RAIL, "VDD supply rail"),
    BasicSymbol("GND", Shape.GROUND, "Ground"),
    BasicSymbol("GNDA", Shape.GROUND, "Analog ground"),
    BasicSymbol("PWR_FLAG", Shape.FLAG, "Marks a net as driven by a power source"),
)


def _s(name: str) -> Sym:
    return Sym(name)


def _effects(hide: bool = False) -> Node:
    node: Node = [_s("effects"), [_s("font"), [_s("size"), _s("1.27"), _s("1.27")]]]
    if hide:
        node.append([_s("hide"), _s("yes")])
    return node


def _prop(name: str, value: str, at: tuple[str, str], hide: bool) -> Node:
    return [
        _s("property"),
        name,
        value,
        [_s("at"), _s(at[0]), _s(at[1]), _s("0")],
        _effects(hide),
    ]


def _polyline(points: list[tuple[str, str]]) -> Node:
    pts: Node = [_s("pts")]
    pts.extend([_s("xy"), _s(x), _s(y)] for x, y in points)
    return [
        _s("polyline"),
        pts,
        [_s("stroke"), [_s("width"), _s("0")], [_s("type"), _s("default")]],
        [_s("fill"), [_s("type"), _s("none")]],
    ]


def _pin(kind: str, angle: str) -> Node:
    return [
        _s("pin"),
        _s(kind),
        _s("line"),
        [_s("at"), _s("0"), _s("0"), _s(angle)],
        [_s("length"), _s("0")],
        [_s("name"), "", _effects()],
        [_s("number"), "1", _effects()],
    ]


def _symbol(sym: BasicSymbol) -> Node:
    # kifab's own drawings: ground = three bars, rail = a T, flag = a diamond
    if sym.shape is Shape.GROUND:
        ref, value_at, pin_kind, pin_angle = "#PWR", ("0", "-3.81"), "power_in", "270"
        drawings = [
            _polyline([("0", "0"), ("0", "-1.016")]),
            _polyline([("-1.524", "-1.016"), ("1.524", "-1.016")]),
            _polyline([("-1.016", "-1.524"), ("1.016", "-1.524")]),
            _polyline([("-0.508", "-2.032"), ("0.508", "-2.032")]),
        ]
    elif sym.shape is Shape.RAIL:
        ref, value_at, pin_kind, pin_angle = "#PWR", ("0", "3.556"), "power_in", "90"
        drawings = [
            _polyline([("0", "0"), ("0", "1.524")]),
            _polyline([("-1.016", "1.524"), ("1.016", "1.524")]),
        ]
    else:
        ref, value_at, pin_kind, pin_angle = "#FLG", ("0", "3.81"), "power_out", "90"
        drawings = [
            _polyline([("0", "0"), ("0", "1.016")]),
            _polyline(
                [("0", "1.016"), ("0.762", "1.778"), ("0", "2.54")]
                + [("-0.762", "1.778"), ("0", "1.016")]
            ),
        ]
    keywords = "flag power" if sym.shape is Shape.FLAG else "global power"
    node: Node = [_s("symbol"), sym.name, [_s("power")]]
    node += [
        [_s("pin_numbers"), [_s("hide"), _s("yes")]],
        [_s("pin_names"), [_s("offset"), _s("0")], [_s("hide"), _s("yes")]],
        [_s("exclude_from_sim"), _s("no")],
        [_s("in_bom"), _s("yes")],
        [_s("on_board"), _s("yes")],
        _prop("Reference", ref, ("0", "-6.35"), hide=True),
        _prop("Value", sym.name, value_at, hide=False),
        _prop("Footprint", "", ("0", "0"), hide=True),
        _prop("Datasheet", "", ("0", "0"), hide=True),
        _prop("Description", sym.description, ("0", "0"), hide=True),
        _prop("ki_keywords", keywords, ("0", "0"), hide=True),
        [_s("symbol"), f"{sym.name}_0_1", *drawings],
        [_s("symbol"), f"{sym.name}_1_1", _pin(pin_kind, pin_angle)],
        [_s("embedded_fonts"), _s("no")],
    ]
    return node


def basic_library() -> SymbolLibrary:
    return SymbolLibrary(
        symbols=tuple(LibrarySymbol(s.name, _symbol(s)) for s in BASIC_SYMBOLS),
        generator="kifab",
    )
