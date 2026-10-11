"""Design-rule profiles: a fab's capabilities in KiCad's terms.

A profile has two halves, both written into the project so KiCad's own DRC
enforces them:

* :class:`BoardConstraints`: Board Setup > Design Rules > Constraints, stored in
  the ``.kicad_pro`` (``board.design_settings.rules``). Absolute minimums.
* :class:`DruRule`: custom rules in the ``.kicad_dru`` for what the board setup
  cannot express (outer vs inner layers, NPTH vs PTH, net-dependent spacing,
  a library-scoped silkscreen exception).
"""

from __future__ import annotations

from dataclasses import dataclass, fields


def _mm(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{text}mm"


@dataclass(frozen=True)
class DruConstraint:
    """``(constraint <kind> (min ..) (max ..))``."""

    kind: str
    min: float | None = None
    max: float | None = None

    def render(self) -> str:
        parts = [f"(constraint {self.kind}"]
        if self.min is not None:
            parts.append(f" (min {_mm(self.min)})")
        if self.max is not None:
            parts.append(f" (max {_mm(self.max)})")
        return "".join(parts) + ")"


@dataclass(frozen=True)
class DruRule:
    """One custom rule. ``comment`` lines say where the value comes from."""

    name: str
    constraints: tuple[DruConstraint, ...]
    condition: str | None = None
    layer: str | None = None
    comment: tuple[str, ...] = ()

    def render(self) -> str:
        lines = [f"# {c}" if c else "#" for c in self.comment]
        lines.append(f'(rule "{self.name}"')
        if self.layer is not None:
            layer = (
                self.layer if self.layer in ("outer", "inner") else f'"{self.layer}"'
            )
            lines.append(f"\t(layer {layer})")
        if self.condition is not None:
            lines.append(f'\t(condition "{self.condition}")')
        lines.extend(f"\t{c.render()}" for c in self.constraints)
        lines.append(")")
        return "\n".join(lines)


@dataclass(frozen=True)
class BoardConstraints:
    """Board-setup minimums; field names are the ``.kicad_pro`` keys (mm)."""

    min_clearance: float
    min_track_width: float
    min_connection: float
    min_via_diameter: float
    min_via_annular_width: float
    min_through_hole_diameter: float
    min_hole_to_hole: float
    min_hole_clearance: float
    min_copper_edge_clearance: float

    def values(self) -> dict[str, float]:
        return {f.name: float(getattr(self, f.name)) for f in fields(self)}


@dataclass(frozen=True)
class RuleOptions:
    """What the profile depends on (from ``kifab.toml``)."""

    layers: int = 2
    relax_fab_footprint_silk: bool = True


@dataclass(frozen=True)
class DesignRuleProfile:
    fab: str
    name: str
    options: RuleOptions
    constraints: BoardConstraints
    rules: tuple[DruRule, ...]
    source: str = ""

    def describe(self) -> str:
        return f'{self.fab} "{self.name}" profile, {self.options.layers} layers'
