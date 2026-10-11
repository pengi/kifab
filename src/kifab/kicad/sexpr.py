"""KiCad S-expression reader and deterministic writer.

Quoted strings become plain :class:`str`; bare atoms (keywords, numbers, ``yes``)
become :class:`Sym`, a ``str`` subclass, so ``(property "Reference" "R1")`` parses
to ``[Sym("property"), "Reference", "R1"]``. Numbers stay as the text KiCad wrote,
so a parse/write round trip reproduces a KiCad file byte for byte.

The writer formats like KiCad's own ``KICAD_FORMAT::Prettify`` (tab indentation,
one list per line, short atom-only lists inline, runs of ``(xy ...)`` points kept
on one line up to column 99), so files written by kifab look like files saved by
KiCad and re-saving them in KiCad gives small diffs.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Union

_TOKEN = re.compile(r'\(|\)|"((?:[^"\\]|\\.)*)"|[^\s()"]+')
_UNESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


class Sym(str):
    """A bare (unquoted) atom such as ``symbol``, ``yes`` or ``1.27``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Sym({str.__repr__(self)})"


Atom = Union[Sym, str]
Node = list[Union["Node", str]]
"""A list node; its items are nested nodes or atoms (``Sym`` or quoted ``str``)."""
Item = Union[Node, str]


class SexprError(ValueError):
    """The text is not a well-formed S-expression."""


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: _UNESCAPES.get(m.group(1), m.group(1)), text)


def parse(text: str) -> Node:
    """Parse one top-level expression and return it as a nested list."""
    stack: list[Node] = [[]]
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        if token == "(":
            stack.append([])
        elif token == ")":
            if len(stack) < 2:
                raise SexprError("unbalanced ')' in S-expression")
            node = stack.pop()
            stack[-1].append(node)
        elif token.startswith('"'):
            stack[-1].append(_unescape(match.group(1)))
        else:
            stack[-1].append(Sym(token))
    if len(stack) != 1:
        raise SexprError("unbalanced '(' in S-expression")
    top = stack[0]
    if len(top) != 1 or not isinstance(top[0], list):
        raise SexprError("expected exactly one top-level list")
    return top[0]


# ---------------------------------------------------------------- navigation


def head(item: Item) -> str:
    """Name of a node: ``(symbol ...)`` -> ``"symbol"``; ``""`` for anything else."""
    if isinstance(item, list) and item and isinstance(item[0], Sym):
        return str(item[0])
    return ""


def children(node: Node, name: str) -> Iterator[Node]:
    """Direct child nodes called ``name``."""
    for item in node:
        if isinstance(item, list) and head(item) == name:
            yield item


def first(node: Node, name: str) -> Node | None:
    """The first direct child node called ``name``, if any."""
    return next(children(node, name), None)


def atom(node: Node | None, index: int = 1) -> str | None:
    """The atom at ``index`` of ``node`` (``(at 1 2)`` -> ``"1"``) as a plain ``str``."""
    if node is None or index >= len(node):
        return None
    value = node[index]
    return str(value) if isinstance(value, str) else None


def atoms(node: Node) -> list[str]:
    """All atoms of ``node`` after its head, as plain ``str``."""
    return [str(item) for item in node[1:] if isinstance(item, str)]


def flag(node: Node, name: str, default: bool = False) -> bool:
    """Value of a ``(name yes|no)`` child; a bare ``name`` atom counts as yes."""
    child = first(node, name)
    if child is None:
        return default
    value = atom(child)
    return value is None or value == "yes"


def properties(node: Node) -> dict[str, str]:
    """``(property "Name" "Value" ...)`` children as ``{Name: Value}``."""
    out: dict[str, str] = {}
    for prop in children(node, "property"):
        name, value = atom(prop, 1), atom(prop, 2)
        if name is not None and value is not None:
            out[name] = value
    return out


# ---------------------------------------------------------------- writing


def quote(text: str) -> str:
    """Quote a string the way KiCad's output formatter does."""
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def number(value: float) -> Sym:
    """Format a number like KiCad: no exponent, no trailing zeros (``1.0`` -> ``1``)."""
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return Sym(text)


def _compact(item: Item, out: list[str]) -> None:
    if isinstance(item, list):
        out.append("(")
        for i, sub in enumerate(item):
            if i:
                out.append(" ")
            _compact(sub, out)
        out.append(")")
    elif isinstance(item, Sym):
        out.append(str(item))
    else:
        out.append(quote(item))


def dumps(node: Node) -> str:
    """Serialise ``node`` in KiCad's file format (with a trailing newline)."""
    parts: list[str] = []
    _compact(node, parts)
    return prettify("".join(parts))


def prettify(source: str) -> str:
    """Port of KiCad's ``KICAD_FORMAT::Prettify`` (non-compact mode)."""
    xy_column_limit = 99
    token_wrap_threshold = 72

    out: list[str] = []
    depth = 0
    last_non_ws = ""
    in_quote = False
    inserted_space = False
    in_multiline = False
    in_xy = False
    column = 0
    backslashes = 0
    n = len(source)

    def next_non_ws(i: int) -> str:
        j = i + 1
        while j < n and source[j] in " \t\r\n":
            j += 1
        return source[j] if j < n else ""

    i = 0
    while i < n:
        char = source[i]
        nxt = next_non_ws(i)
        if char in " \t\r\n" and not in_quote:
            if (
                not inserted_space
                and depth > 0
                and last_non_ws != "("
                and nxt != ")"
                and nxt != "("
            ):
                if in_xy or column < token_wrap_threshold:
                    out.append(" ")
                    column += 1
                else:
                    out.append("\n" + "\t" * depth)
                    column = depth
                    in_multiline = True
                inserted_space = True
        else:
            inserted_space = False
            if char == "(" and not in_quote:
                is_xy = source.startswith("xy ", i + 1)
                if not out:
                    out.append("(")
                    column += 1
                elif in_xy and is_xy and column < xy_column_limit:
                    out.append(" (")
                    column += 2
                else:
                    out.append("\n" + "\t" * depth + "(")
                    column = depth + 1
                in_xy = is_xy
                depth += 1
            elif char == ")" and not in_quote:
                if depth > 0:
                    depth -= 1
                if last_non_ws == ")" or in_multiline:
                    out.append("\n" + "\t" * depth + ")")
                    column = depth + 1
                    in_multiline = False
                else:
                    out.append(")")
                    column += 1
            else:
                if char == "\\":
                    backslashes += 1
                elif char == '"':
                    if backslashes % 2 == 0:
                        in_quote = not in_quote
                    backslashes = 0
                else:
                    backslashes = 0
                out.append(char)
                column += 1
            last_non_ws = char
        i += 1
    out.append("\n")
    return "".join(out)
