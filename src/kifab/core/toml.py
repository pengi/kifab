"""TOML reading (stdlib ``tomllib`` / ``tomli``) and a small deterministic writer.

The writer covers what kifab writes: tables of strings, ints, floats, bools and
lists of those, plus arrays of tables. Keys are written in the given order.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from typing import Any, Union

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

TOMLDecodeError = tomllib.TOMLDecodeError

Scalar = Union[str, int, float, bool]
Value = Union[Scalar, Sequence[Scalar]]


def loads(text: str) -> dict[str, Any]:
    return tomllib.loads(text)


def _scalar(value: Scalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def value(v: Value) -> str:
    """A TOML value; lists of more than one item get one item per line (small diffs)."""
    if isinstance(v, (str, int, float, bool)):
        return _scalar(v)
    if len(v) <= 1:
        return "[" + ", ".join(_scalar(x) for x in v) + "]"
    return "[\n" + "".join(f"    {_scalar(x)},\n" for x in v) + "]"


def table(name: str | None, items: Mapping[str, Value]) -> str:
    """``[name]`` followed by ``key = value`` lines (no header if ``name`` is None)."""
    lines = [f"[{name}]"] if name else []
    lines += [f"{_key(k)} = {value(v)}" for k, v in items.items()]
    return "\n".join(lines) + "\n"


def array_table(name: str, items: Mapping[str, Value]) -> str:
    lines = [f"[[{name}]]"] + [f"{_key(k)} = {value(v)}" for k, v in items.items()]
    return "\n".join(lines) + "\n"


def _key(key: str) -> str:
    if key and all(c.isalnum() or c in "-_" for c in key):
        return key
    return _scalar(key)
