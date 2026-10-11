"""The library-source plugin interface and registry.

A library source supplies a part's symbol, footprint and 3D model (EasyEDA via
easyeda2kicad today). It is independent of the fab and of the supplier: the
same footprint serves every supplier of a part. Library sources are plugins: a
:class:`LibrarySource` subclass registered under the entry-point group
``kifab.libraries``; the built-ins are also available from a source tree.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol, runtime_checkable

ENTRY_POINT_GROUP = "kifab.libraries"


class UnknownLibrarySource(Exception):
    pass


class FetchError(Exception):
    """A part's library data could not be fetched."""


@dataclass(frozen=True)
class FetchedPart:
    """What a fetch produced, in a scratch directory (not yet vendored)."""

    part_id: str
    symbol_library: Path | None  # a .kicad_sym with the part's symbol(s)
    footprints: tuple[Path, ...]  # .kicad_mod files
    models: tuple[Path, ...]  # 3D models (.wrl, .step)
    log: str
    source: str  # tool and version, recorded in kifab.lock


class PartSource(Protocol):
    def fetch(self, part_id: str, workdir: Path) -> FetchedPart:
        """Fetch ``part_id`` into ``workdir``; raise :class:`FetchError`."""


@runtime_checkable
class CachingPartSource(PartSource, Protocol):
    """A part source that keeps fetched parts; ``forget`` forces a fresh fetch."""

    def forget(self, part_id: str) -> None: ...


class LibrarySource(ABC):
    """Where symbols, footprints and 3D models of parts come from."""

    #: Registry key, used in ``kifab.toml`` (``[parts] library_source``).
    name: str = ""
    #: Human readable name.
    title: str = ""
    #: The kind of part number it fetches by (a supplier's ``sku_kind``, e.g.
    #: ``"lcsc"``): a part is vendored from this source by that number.
    sku_kind: str = ""

    @abstractmethod
    def part_source(self) -> PartSource:
        """The fetcher (``$KIFAB_OFFLINE`` serves fixtures)."""


def _builtin() -> dict[str, type[LibrarySource]]:
    from kifab.libraries.easyeda import EasyedaLibrary

    return {EasyedaLibrary.name: EasyedaLibrary}


def available_library_sources() -> dict[str, type[LibrarySource]]:
    """Every registered library source by name (built-ins plus plugins)."""
    sources = _builtin()
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        if ep.name in sources:
            continue
        cls = ep.load()
        if isinstance(cls, type) and issubclass(cls, LibrarySource):
            sources[ep.name] = cls
    return dict(sorted(sources.items()))


def get_library_source(name: str) -> LibrarySource:
    sources = available_library_sources()
    try:
        return sources[name]()
    except KeyError:
        known = ", ".join(sources) or "none"
        raise UnknownLibrarySource(
            f"unknown library source {name!r} (known: {known})"
        ) from None
