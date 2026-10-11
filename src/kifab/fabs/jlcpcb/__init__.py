"""JLCPCB: PCB fabrication and assembly, parts from LCSC."""

from __future__ import annotations

from kifab.core.issues import Code, Issue, warning
from kifab.core.manufacturing import Bom, BuildOutputs, FileArtifact, Placement
from kifab.core.net import HttpClient, UrllibClient, offline_root
from kifab.fabs.base import Catalog, Fab, PartSource
from kifab.fabs.jlcpcb import export, rules
from kifab.core.cache import user_cache_dir
from kifab.fabs.jlcpcb.fetch import CachedPartSource, Easyeda2kicad, FixturePartSource
from kifab.fabs.jlcpcb.search import FixtureClient, JlcpcbCatalog
from kifab.fabs.rules import DesignRuleProfile, RuleOptions
from kifab.kicad.schematic import LCSC_FIELDS


class JlcpcbFab(Fab):
    name = "jlcpcb"
    title = "JLCPCB"

    def profiles(self) -> tuple[str, ...]:
        return ("standard",)

    def design_rules(self, profile: str, options: RuleOptions) -> DesignRuleProfile:
        if profile != "standard":
            raise ValueError(f"unknown JLCPCB rules profile {profile!r}")
        return rules.standard(options, self.library_name)

    @property
    def part_number_fields(self) -> tuple[str, ...]:
        return LCSC_FIELDS

    def check_parts(self, bom: Bom) -> list[Issue]:
        return [
            warning(
                Code.NO_PART_NUMBER,
                f"no LCSC part number for {line.value} ({line.footprint}); JLCPCB "
                "cannot assemble it",
                ref=",".join(line.references),
                hint="add an LCSC field to the symbol",
            )
            for line in bom.lines
            if not line.part_number
        ]

    def export_bom(self, bom: Bom, board_name: str) -> FileArtifact:
        return FileArtifact(f"{board_name}_bom_jlcpcb.csv", export.bom_csv(bom))

    def export_placement(self, placement: Placement, board_name: str) -> FileArtifact:
        return FileArtifact(f"{board_name}_cpl_jlcpcb.csv", export.cpl_csv(placement))

    def bundle_files(self, built: BuildOutputs) -> list[FileArtifact]:
        return [export.gerber_zip(built)]

    def catalog(self, client: HttpClient | None = None) -> Catalog:
        if client is None:
            root = offline_root()
            client = FixtureClient(root) if root else UrllibClient()
        return JlcpcbCatalog(client)

    def part_source(self) -> PartSource:
        root = offline_root()
        if root:
            return FixturePartSource(root)
        return CachedPartSource(Easyeda2kicad(), user_cache_dir() / "easyeda2kicad")
