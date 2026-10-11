"""The fab plugin interface and registry.

A fab (JLCPCB today) is a plugin: a :class:`Fab` subclass registered under the
entry-point group ``kifab.fabs``. The built-in fabs are also available when kifab
runs from a source tree without being installed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol

from kifab.core.issues import Issue
from kifab.core.manufacturing import Bom, BuildOutputs, FileArtifact, Placement
from kifab.core.net import HttpClient
from kifab.core.parts import PartHit, SearchPage
from kifab.fabs.rules import DesignRuleProfile, RuleOptions

ENTRY_POINT_GROUP = "kifab.fabs"


class UnknownFab(Exception):
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


class Catalog(Protocol):
    """The fab's parts catalogue (online; ``$KIFAB_OFFLINE`` serves fixtures)."""

    def search(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 10,
        basic_only: bool = False,
    ) -> SearchPage: ...

    def lookup(self, part_id: str) -> PartHit | None:
        """The part with exactly this number, or ``None`` if the fab has none."""


class Fab(ABC):
    """A fabrication service."""

    #: Registry key, used in ``kifab.toml`` (``fab = "jlcpcb"``).
    name: str = ""
    #: Human readable name.
    title: str = ""

    @property
    def library_name(self) -> str:
        """Nickname of the project library vendored parts go into (``lib/<name>/``)."""
        return self.name

    @abstractmethod
    def profiles(self) -> tuple[str, ...]:
        """Names of the design-rule profiles this fab offers; the first is default."""

    @abstractmethod
    def design_rules(self, profile: str, options: RuleOptions) -> DesignRuleProfile:
        """The capabilities as KiCad constraints and custom rules."""

    @property
    @abstractmethod
    def part_number_fields(self) -> tuple[str, ...]:
        """Schematic field names that hold this fab's part number (first preferred)."""

    @abstractmethod
    def check_parts(self, bom: Bom) -> list[Issue]:
        """Problems with the BOM for ordering (offline: e.g. missing part numbers)."""

    @abstractmethod
    def export_bom(self, bom: Bom, board_name: str) -> FileArtifact: ...

    @abstractmethod
    def export_placement(
        self, placement: Placement, board_name: str
    ) -> FileArtifact: ...

    @abstractmethod
    def catalog(self, client: HttpClient | None = None) -> Catalog:
        """The parts catalogue; ``client`` replaces the default HTTP client."""

    @abstractmethod
    def part_source(self) -> PartSource:
        """Where symbols, footprints and 3D models of the fab's parts come from."""

    @abstractmethod
    def bundle_files(self, built: BuildOutputs) -> list[FileArtifact]:
        """Files made from the plotted outputs, e.g. the zip uploaded as "PCB"."""


def _builtin() -> dict[str, type[Fab]]:
    from kifab.fabs.jlcpcb import JlcpcbFab

    return {JlcpcbFab.name: JlcpcbFab}


def available_fabs() -> dict[str, type[Fab]]:
    """Every registered fab by name (built-ins plus installed plugins)."""
    fabs = _builtin()
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        if ep.name in fabs:
            continue
        cls = ep.load()
        if isinstance(cls, type) and issubclass(cls, Fab):
            fabs[ep.name] = cls
    return dict(sorted(fabs.items()))


def get_fab(name: str) -> Fab:
    fabs = available_fabs()
    try:
        return fabs[name]()
    except KeyError:
        known = ", ".join(fabs) or "none"
        raise UnknownFab(f"unknown fab {name!r} (known: {known})") from None
