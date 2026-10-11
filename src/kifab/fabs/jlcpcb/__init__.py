"""JLCPCB: PCB fabrication and assembly, parts from LCSC."""

from __future__ import annotations

from kifab.core.issues import Code, Issue, warning
from kifab.core.manufacturing import (
    Bom,
    BuildOutputs,
    FileArtifact,
    OrderFiles,
    Placement,
)
from kifab.fabs.base import Fab
from kifab.fabs.jlcpcb import export, rules
from kifab.fabs.rules import DesignRuleProfile, RuleOptions


class JlcpcbFab(Fab):
    name = "jlcpcb"
    title = "JLCPCB"
    default_suppliers = ("jlcpcb",)
    default_library_source = "easyeda"

    def profiles(self) -> tuple[str, ...]:
        return ("standard",)

    def design_rules(self, profile: str, options: RuleOptions) -> DesignRuleProfile:
        if profile != "standard":
            raise ValueError(f"unknown JLCPCB rules profile {profile!r}")
        return rules.standard(options, options.library or self.library_name)

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
            if not line.part.sku("lcsc")
        ]

    def export_bom(self, bom: Bom, board_name: str) -> FileArtifact:
        return FileArtifact(f"{board_name}_bom_jlcpcb.csv", export.bom_csv(bom))

    def export_placement(self, placement: Placement, board_name: str) -> FileArtifact:
        return FileArtifact(f"{board_name}_cpl_jlcpcb.csv", export.cpl_csv(placement))

    def bundle_files(
        self, built: BuildOutputs, order: OrderFiles
    ) -> list[FileArtifact]:
        return [export.gerber_zip(built)]  # BOM and CPL are uploaded separately
