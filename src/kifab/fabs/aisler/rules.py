"""AISLER capabilities as KiCad design-rule profiles, one per surface finish.

AISLER's limits depend on the finish: HASL ("simple" boards) or ENIG
("complex" boards: needed for small drills, fine tracks and castellated
holes). Values from AISLER's published design rules (community post "PCB
Design Rules", last updated 2024-05-12) for 35 um copper.

UNVERIFIED (no access to aisler.net from where this was written; check the
current page before relying on a design at these limits): every value below,
and in particular the annular ring (0.2 mm, reported by a user as a hard
limit), copper to board edge, hole to hole and hole to copper, which the post
does not give; kifab uses conservative values for those.
"""

from __future__ import annotations

from dataclasses import dataclass

from kifab.fabs.rules import (
    BoardConstraints,
    DesignRuleProfile,
    DruConstraint,
    DruRule,
    RuleOptions,
)

SOURCE = "https://community.aisler.net/t/pcb-design-rules/41"
#: Copper to board edge, hole to hole, hole to copper: not in AISLER's post;
#: conservative values (UNVERIFIED).
EDGE_CLEARANCE = 0.3
HOLE_TO_HOLE = 0.3
HOLE_CLEARANCE = 0.25
ANNULAR_RING = 0.2


@dataclass(frozen=True)
class Finish:
    name: str
    title: str
    min_drill: float
    track: float  # minimum track width
    spacing: float  # minimum spacing
    track_6l: float  # boards with 6 or more layers (width and spacing)


FINISHES = {
    "hasl": Finish("hasl", "HASL", 0.3, 0.2, 0.15, 0.15),
    "enig": Finish("enig", "ENIG", 0.25, 0.125, 0.125, 0.1),
}


def _c(kind: str, min: float | None = None, max: float | None = None) -> DruConstraint:
    return DruConstraint(kind, min, max)


def profile(finish: Finish, options: RuleOptions) -> DesignRuleProfile:
    six = options.layers >= 6
    track = finish.track_6l if six else finish.track
    spacing = finish.track_6l if six else finish.spacing
    floor = min(track, spacing)
    constraints = BoardConstraints(
        min_clearance=spacing,
        min_track_width=track,
        min_connection=floor,
        min_via_diameter=round(finish.min_drill + 2 * ANNULAR_RING, 4),
        min_via_annular_width=ANNULAR_RING,
        min_through_hole_diameter=finish.min_drill,
        min_hole_to_hole=HOLE_TO_HOLE,
        min_hole_clearance=HOLE_CLEARANCE,
        min_copper_edge_clearance=EDGE_CLEARANCE,
    )
    note = f"AISLER {finish.title}, 35 um copper" + (", 6+ layers" if six else "")
    rules = (
        DruRule(
            f"Track width ({note})",
            (_c("track_width", track),),
            "A.Type == 'track'",
        ),
        DruRule(
            f"Track spacing ({note})",
            (_c("clearance", spacing),),
            "A.Type == 'track' && B.Type == A.Type",
        ),
        DruRule(
            "Hole diameter",
            (_c("hole_size", finish.min_drill),),
            comment=(f"Smallest drill with {finish.title}.",),
        ),
        DruRule(
            "Edge (routed) to track clearance",
            (_c("edge_clearance", EDGE_CLEARANCE),),
            "A.Type == 'track'",
            comment=("Not in AISLER's published rules; a conservative value.",),
        ),
    )
    return DesignRuleProfile(
        fab="aisler",
        name=finish.name,
        options=options,
        constraints=constraints,
        rules=rules,
        source=SOURCE,
    )
