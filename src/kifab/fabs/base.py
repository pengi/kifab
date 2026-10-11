"""The fab plugin interface and registry.

A fab (JLCPCB today) is a plugin: a :class:`Fab` subclass registered under the
entry-point group ``kifab.fabs``. The built-in fabs are also available when kifab
runs from a source tree without being installed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib.metadata import entry_points

from kifab.core.issues import Issue
from kifab.core.manufacturing import Bom, BuildOutputs, FileArtifact, Placement
from kifab.fabs.rules import DesignRuleProfile, RuleOptions

ENTRY_POINT_GROUP = "kifab.fabs"
#: The fab offered first (and used where no project names one).
DEFAULT_FAB = "jlcpcb"


class UnknownFab(Exception):
    pass


class Fab(ABC):
    """A fabrication service."""

    #: Registry key, used in ``kifab.toml`` (``fab = "jlcpcb"``).
    name: str = ""
    #: Human readable name.
    title: str = ""
    #: Suppliers a project uses unless ``kifab.toml`` names others (first is
    #: preferred).
    default_suppliers: tuple[str, ...] = ()
    #: Library source a project uses unless ``kifab.toml`` names another.
    default_library_source: str = "easyeda"

    @property
    def library_name(self) -> str:
        """Default nickname of the vendored parts library (``lib/<name>/``).

        A project can name another (``[parts] library`` in ``kifab.toml``).
        """
        return self.name

    @abstractmethod
    def profiles(self) -> tuple[str, ...]:
        """Names of the design-rule profiles this fab offers; the first is default."""

    @abstractmethod
    def design_rules(self, profile: str, options: RuleOptions) -> DesignRuleProfile:
        """The capabilities as KiCad constraints and custom rules."""

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
    def bundle_files(self, built: BuildOutputs) -> list[FileArtifact]:
        """Files made from the plotted outputs, e.g. the zip uploaded as "PCB"."""


def _builtin() -> dict[str, type[Fab]]:
    from kifab.fabs.aisler import AislerFab
    from kifab.fabs.jlcpcb import JlcpcbFab

    return {JlcpcbFab.name: JlcpcbFab, AislerFab.name: AislerFab}


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
