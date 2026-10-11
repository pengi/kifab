"""Read and write KiCad symbol libraries (``.kicad_sym``)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from kifab.kicad import sexpr

#: File format version written by kifab (KiCad 9 format; KiCad 10 reads it).
SYMBOL_LIB_VERSION = "20241209"


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
