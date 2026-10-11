"""JLCPCB capabilities as a KiCad design-rule profile.

Values come from https://jlcpcb.com/capabilities/pcb-capabilities, as used
on a board the author ordered from JLCPCB, for standard FR-4 with 1 oz outer
copper. Where JLCPCB offers tighter limits at
extra cost, the standard (no surcharge) limit is used.
"""

from __future__ import annotations

from kifab.fabs.rules import (
    BoardConstraints,
    DesignRuleProfile,
    DruConstraint,
    DruRule,
    RuleOptions,
)

SOURCE = "https://jlcpcb.com/capabilities/pcb-capabilities"

OUTER_TRACK = 0.127  # width and spacing, 1 oz outer copper (5 mil)
INNER_TRACK = 0.09  # width and spacing, inner layers (3.5 mil)


def _c(kind: str, min: float | None = None, max: float | None = None) -> DruConstraint:
    return DruConstraint(kind, min, max)


def standard(options: RuleOptions, library: str = "jlcpcb") -> DesignRuleProfile:
    multilayer = options.layers > 2
    floor = INNER_TRACK if multilayer else OUTER_TRACK
    constraints = BoardConstraints(
        min_clearance=floor,
        min_track_width=floor,
        min_connection=floor,
        min_via_diameter=0.5,
        # JLCPCB enlarges PTH annular rings to 0.15 mm in production; 0.25 mm is the
        # board-setup minimum the reference board was ordered with. No custom rule
        # for it on purpose: one matching all plated items would override this.
        min_via_annular_width=0.25,
        min_through_hole_diameter=0.3,
        min_hole_to_hole=0.25,
        min_hole_clearance=0.25,
        min_copper_edge_clearance=0.3,
    )
    rules: list[DruRule] = [
        DruRule(
            "Track width, outer layer (1oz copper)",
            (_c("track_width", OUTER_TRACK),),
            "A.Type == 'track'",
            "outer",
        ),
        DruRule(
            "Track spacing, outer layer (1oz copper)",
            (_c("clearance", OUTER_TRACK),),
            "A.Type == 'track' && B.Type == A.Type",
            "outer",
        ),
    ]
    if multilayer:
        rules += [
            DruRule(
                "Track width, inner layer",
                (_c("track_width", INNER_TRACK),),
                "A.Type == 'track'",
                "inner",
            ),
            DruRule(
                "Track spacing, inner layer",
                (_c("clearance", INNER_TRACK),),
                "A.Type == 'track' && B.Type == A.Type",
                "inner",
            ),
        ]
    rules += [
        DruRule(
            "Silkscreen text",
            (_c("text_thickness", 0.15), _c("text_height", 1.0)),
            "A.Type == 'Text' || A.Type == 'Text Box'",
            "?.Silkscreen",
        ),
        DruRule(
            "Pad to Silkscreen",
            (_c("silk_clearance", 0.15),),
            "A.Type == 'pad' && B.Layer == '?.Silkscreen'",
            "outer",
        ),
    ]
    if options.relax_fab_footprint_silk:
        rules.append(
            DruRule(
                f"Pad to Silkscreen ({library} library footprints)",
                (_c("silk_clearance", 0.0),),
                "A.Type == 'pad' && B.Layer == '?.Silkscreen' && "
                f"A.memberOfFootprint('{library}:*')",
                "outer",
                comment=(
                    "JLCPCB's own (EasyEDA) footprints draw silkscreen 0.01-0.14 mm",
                    "from the pads. They are kept unmodified so they match what JLCPCB",
                    "assembles from, and JLCPCB clips silkscreen off the pads. Later",
                    "rules win, so this relaxes the rule above for them only.",
                ),
            )
        )
    rules += [
        DruRule(
            "Edge (routed) to track clearance",
            (_c("edge_clearance", 0.3),),
            "A.Type == 'track'",
        ),
        DruRule(
            "Hole diameter",
            (_c("hole_size", 0.2, 6.3),),
            comment=("All holes; the via/PTH/NPTH rules below are stricter.",),
        ),
        DruRule(
            "Hole (NPTH) diameter",
            (_c("hole_size", 0.5),),
            "!A.isPlated()",
            "outer",
        ),
        DruRule(
            "Hole (castellated) diameter",
            (_c("hole_size", 0.6),),
            "A.Type == 'pad' && A.Fabrication_Property == 'Castellated pad'",
            "outer",
        ),
        DruRule(
            "Clearance: hole to hole (perimeter), different nets",
            (_c("hole_to_hole", 0.5),),
            "A.Net != B.Net",
            "outer",
        ),
        DruRule(
            "Clearance: hole to hole (perimeter), same net",
            (_c("hole_to_hole", 0.254),),
            "A.Net == B.Net",
            "outer",
        ),
        DruRule(
            "Clearance: track to NPTH hole (perimeter)",
            (_c("hole_clearance", 0.254),),
            "!A.isPlated() && B.Type == 'track' && A.Net != B.Net",
        ),
        DruRule(
            "Clearance: track to PTH hole perimeter",
            (_c("hole_clearance", 0.33),),
            "A.isPlated() && B.Type == 'track' && A.Net != B.Net",
        ),
        DruRule(
            "Clearance: track to pad",
            (_c("clearance", 0.2),),
            "A.Type == 'pad' && B.Type == 'track' && A.Net != B.Net",
        ),
        DruRule(
            "Clearance: pad/via to pad/via",
            (_c("clearance", OUTER_TRACK),),
            "A.isPlated() && B.isPlated() && A.Net != B.Net",
            "outer",
        ),
    ]
    return DesignRuleProfile(
        fab="jlcpcb",
        name="standard",
        options=options,
        constraints=constraints,
        rules=tuple(rules),
        source=SOURCE,
    )
