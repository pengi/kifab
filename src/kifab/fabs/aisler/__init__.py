"""AISLER (aisler.net): PCBs and assembly, parts bought from distributors.

AISLER sources assembly parts itself (LCSC, Mouser, DigiKey, Farnell, Arrow,
Würth, TI, ...) by MPN, so the default supplier is LCSC with EasyEDA libraries
(the same parts and footprints as for JLCPCB); ``[parts] suppliers`` can name
others.
"""

from __future__ import annotations

from kifab.core.issues import Code, Issue, info, warning
from kifab.core.manufacturing import Bom, BuildOutputs, FileArtifact, Placement
from kifab.fabs.aisler import export, rules
from kifab.fabs.base import Fab
from kifab.fabs.rules import DesignRuleProfile, RuleOptions


class AislerFab(Fab):
    name = "aisler"
    title = "AISLER"
    default_suppliers = ("lcsc",)
    default_library_source = "easyeda"

    def profiles(self) -> tuple[str, ...]:
        return tuple(rules.FINISHES)  # hasl first: the stricter, valid for both

    def design_rules(self, profile: str, options: RuleOptions) -> DesignRuleProfile:
        finish = rules.FINISHES.get(profile)
        if finish is None:
            known = ", ".join(rules.FINISHES)
            raise ValueError(f"unknown AISLER rules profile {profile!r} ({known})")
        return rules.profile(finish, options)

    def check_parts(self, bom: Bom) -> list[Issue]:
        issues: list[Issue] = []
        for line in bom.lines:
            refs = ",".join(line.references)
            if line.part.empty:
                issues.append(
                    warning(
                        Code.NO_PART_NUMBER,
                        f"no MPN or supplier number for {line.value} "
                        f"({line.footprint}); AISLER has to guess the part",
                        ref=refs,
                        hint="add MPN and Manufacturer fields to the symbol",
                    )
                )
            elif not line.part.mpn:
                issues.append(
                    info(
                        Code.NO_PART_NUMBER,
                        f"no MPN for {line.part.label}; AISLER matches it by "
                        "the supplier number (check its guess)",
                        ref=refs,
                        hint="vendor the part again to add the MPN field",
                    )
                )
        return issues

    def export_bom(self, bom: Bom, board_name: str) -> FileArtifact:
        return FileArtifact(f"{board_name}_bom_aisler.csv", export.bom_csv(bom))

    def export_placement(self, placement: Placement, board_name: str) -> FileArtifact:
        return FileArtifact(f"{board_name}_cpl_aisler.csv", export.cpl_csv(placement))

    def bundle_files(self, built: BuildOutputs) -> list[FileArtifact]:
        return [export.gerber_zip(built)]
