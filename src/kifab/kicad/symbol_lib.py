"""Read and write KiCad symbol libraries (``.kicad_sym``)."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from pathlib import Path

from kifab.kicad import sexpr

#: File format version written by kifab (KiCad 9 format; KiCad 10 reads it).
SYMBOL_LIB_VERSION = "20241209"
#: From this format (KiCad 8) the description is a ``Description`` field; older
#: files (easyeda2kicad writes 20211014) keep it in ``ki_description``.
DESCRIPTION_FIELD_VERSION = "20231120"
_DESCRIPTION_NAMES = ("Description", "ki_description")
#: From this format (KiCad 9) hidden flags are written ``(hide yes)``.
HIDE_YES_VERSION = "20241209"
_NO_NAME = ("", "~")


def _hide_flag(version: str) -> sexpr.Item:
    if version >= HIDE_YES_VERSION:
        return [sexpr.Sym("hide"), sexpr.Sym("yes")]
    return sexpr.Sym("hide")


def _pins(node: sexpr.Node) -> list[sexpr.Node]:
    """All pin nodes of a symbol, in its unit sub-symbols."""
    out: list[sexpr.Node] = []
    for item in node:
        if isinstance(item, list):
            if sexpr.head(item) == "pin":
                out.append(item)
            elif sexpr.head(item) == "symbol":
                out += _pins(item)
    return out


def _set_hidden(node: sexpr.Node, name: str, version: str) -> None:
    """Add a hide flag to ``(pin_names ...)``/``(pin_numbers ...)``, creating it."""
    existing = sexpr.first(node, name)
    if existing is None:
        node.insert(2, [sexpr.Sym(name), _hide_flag(version)])
        return
    if any(
        item == "hide" or (isinstance(item, list) and sexpr.head(item) == "hide")
        for item in existing[1:]
    ):
        return
    existing.append(_hide_flag(version))


@dataclass(frozen=True)
class LibrarySymbol:
    """One top-level symbol of a library, with its S-expression."""

    name: str
    node: sexpr.Node

    @property
    def is_power(self) -> bool:
        return sexpr.first(self.node, "power") is not None

    @property
    def extends(self) -> str | None:
        return sexpr.atom(sexpr.first(self.node, "extends"))

    @property
    def description(self) -> str:
        props = sexpr.properties(self.node)
        return next((props[n] for n in _DESCRIPTION_NAMES if props.get(n)), "")

    def field_value(self, name: str) -> str:
        """The value of field ``name`` (exact name), ``""`` if absent."""
        return sexpr.properties(self.node).get(name, "")

    def with_description(self, text: str, version: str) -> LibrarySymbol:
        """A copy whose description is ``text``, written as ``version`` expects."""
        name = (
            "Description" if version >= DESCRIPTION_FIELD_VERSION else "ki_description"
        )
        return self._with_property(name, text, version, _DESCRIPTION_NAMES)

    def with_property(self, name: str, text: str, version: str) -> LibrarySymbol:
        """A copy with field ``name`` set to ``text`` (a hidden field)."""
        return self._with_property(name, text, version, (name,))

    def _with_property(
        self, name: str, text: str, version: str, replaces: tuple[str, ...]
    ) -> LibrarySymbol:
        node: sexpr.Node = [
            item
            for item in self.node
            if not (
                isinstance(item, list)
                and sexpr.head(item) == "property"
                and sexpr.atom(item) in replaces
            )
        ]
        new = version >= DESCRIPTION_FIELD_VERSION
        prop: sexpr.Node = [sexpr.Sym("property"), name, text]
        if not new:  # KiCad 6/7 properties carry an id
            ids = [
                int(i)
                for p in sexpr.children(self.node, "property")
                if (i := sexpr.atom(sexpr.first(p, "id"))) and i.isdigit()
            ]
            prop.append([sexpr.Sym("id"), sexpr.Sym(str(max(ids, default=-1) + 1))])
        prop.append([sexpr.Sym("at"), sexpr.Sym("0"), sexpr.Sym("0"), sexpr.Sym("0")])
        font: sexpr.Node = [
            sexpr.Sym("font"),
            [sexpr.Sym("size"), sexpr.Sym("1.27"), sexpr.Sym("1.27")],
        ]
        hide: sexpr.Item = (
            [sexpr.Sym("hide"), sexpr.Sym("yes")] if new else sexpr.Sym("hide")
        )
        prop.append([sexpr.Sym("effects"), font, hide])
        # after the last property, before the drawings
        last = max(
            (
                i
                for i, item in enumerate(node)
                if isinstance(item, list) and sexpr.head(item) == "property"
            ),
            default=1,
        )
        node.insert(last + 1, prop)
        return LibrarySymbol(self.name, node)

    def with_tidy_pins(self, version: str) -> LibrarySymbol:
        """Pin names that only repeat the pin number are dropped.

        easyeda2kicad names the pins of simple parts "1", "2", ..., which KiCad
        then prints inside the body next to the numbers. When no pin keeps a
        real name, pin names are hidden altogether, and for two-pin parts
        (R, C, L) the pin numbers too, like KiCad's own ``Device:R``. Real
        names (GND, P0.00/XL1, ...) stay.
        """
        node = copy.deepcopy(self.node)
        pins = _pins(node)
        if not pins:
            return self
        named = 0
        for pin in pins:
            name_node = sexpr.first(pin, "name")
            number = sexpr.atom(sexpr.first(pin, "number")) or ""
            name = sexpr.atom(name_node) if name_node is not None else None
            if name_node is not None and name is not None:
                if name == number or name in _NO_NAME:
                    name_node[1] = "~"
                else:
                    named += 1
        if named == 0:
            _set_hidden(node, "pin_names", version)
            if len({sexpr.atom(sexpr.first(p, "number")) for p in pins}) == 2:
                _set_hidden(node, "pin_numbers", version)
        return LibrarySymbol(self.name, node)

    def digest(self) -> str:
        """sha256 of the symbol in canonical (KiCad) formatting."""
        return hashlib.sha256(sexpr.dumps(self.node).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SymbolLibrary:
    symbols: tuple[LibrarySymbol, ...]
    version: str = SYMBOL_LIB_VERSION
    generator: str = "kifab"

    def names(self) -> set[str]:
        return {s.name for s in self.symbols}

    def get(self, name: str) -> LibrarySymbol | None:
        return next((s for s in self.symbols if s.name == name), None)

    def to_node(self) -> sexpr.Node:
        """The library as a node, symbols sorted by name (deterministic output)."""
        node: sexpr.Node = [
            sexpr.Sym("kicad_symbol_lib"),
            [sexpr.Sym("version"), sexpr.Sym(self.version)],
            [sexpr.Sym("generator"), self.generator],
        ]
        node.extend(s.node for s in sorted(self.symbols, key=lambda s: s.name))
        return node

    def dumps(self) -> str:
        return sexpr.dumps(self.to_node())


def parse_symbol_library(text: str) -> SymbolLibrary:
    tree = sexpr.parse(text)
    if sexpr.head(tree) != "kicad_symbol_lib":
        raise sexpr.SexprError("not a KiCad symbol library")
    symbols = tuple(
        LibrarySymbol(name, node)
        for node in sexpr.children(tree, "symbol")
        if (name := sexpr.atom(node))
    )
    return SymbolLibrary(
        symbols=symbols,
        version=sexpr.atom(sexpr.first(tree, "version")) or SYMBOL_LIB_VERSION,
        generator=sexpr.atom(sexpr.first(tree, "generator")) or "",
    )


def read_symbol_library(path: Path) -> SymbolLibrary:
    return parse_symbol_library(Path(path).read_text(encoding="utf-8"))
