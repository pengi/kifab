"""``kifab.toml``: the project's kifab configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kifab.core import toml
from kifab.fabs.rules import RuleOptions

CONFIG_NAME = "kifab.toml"
#: KiCad boards have an even number of copper layers (a one-sided board is a
#: 2-layer board with nothing on B.Cu).
SUPPORTED_LAYERS = (2, 4, 6, 8, 10, 12, 14, 16, 18, 20)


class ConfigError(Exception):
    """``kifab.toml`` is missing a value or has a wrong one."""


@dataclass(frozen=True)
class BoardConfig:
    layers: int = 2


@dataclass(frozen=True)
class RulesConfig:
    profile: str = ""  # empty: the fab's default profile
    relax_fab_footprint_silk: bool = True


@dataclass(frozen=True)
class BundleConfig:
    #: False: through-hole parts are left out of BOM/CPL (hand-soldered).
    assemble_through_hole: bool = False


@dataclass(frozen=True)
class PartsConfig:
    """Where parts come from; empty values mean the fab's defaults."""

    #: Suppliers for search, stock and price, preferred first.
    suppliers: tuple[str, ...] = ()
    #: Where vendored symbols, footprints and 3D models come from.
    library_source: str = ""
    #: Nickname of the project library parts are vendored into (``lib/<name>/``).
    library: str = ""

    def is_default(self) -> bool:
        return self == PartsConfig()


@dataclass(frozen=True)
class Config:
    fab: str
    project: str  # KiCad project name (the .kicad_pro stem)
    board: BoardConfig = field(default_factory=BoardConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)
    bundle: BundleConfig = field(default_factory=BundleConfig)
    parts: PartsConfig = field(default_factory=PartsConfig)

    def rule_options(self) -> RuleOptions:
        return RuleOptions(
            layers=self.board.layers,
            relax_fab_footprint_silk=self.rules.relax_fab_footprint_silk,
            library=self.parts.library,
        )

    def dumps(self) -> str:
        parts = [
            "# kifab project configuration (https://github.com/pengi/kifab)\n",
            toml.table(None, {"fab": self.fab, "project": self.project}),
            toml.table("board", {"layers": self.board.layers}),
            toml.table(
                "rules",
                {
                    "profile": self.rules.profile,
                    "relax_fab_footprint_silk": self.rules.relax_fab_footprint_silk,
                },
            ),
            toml.table(
                "bundle",
                {"assemble_through_hole": self.bundle.assemble_through_hole},
            ),
        ]
        if not self.parts.is_default():
            values: dict[str, toml.Value] = {}
            if self.parts.suppliers:
                values["suppliers"] = list(self.parts.suppliers)
            if self.parts.library_source:
                values["library_source"] = self.parts.library_source
            if self.parts.library:
                values["library"] = self.parts.library
            parts.append(toml.table("parts", values))
        return "\n".join(parts)

    def save(self, directory: Path) -> Path:
        path = Path(directory) / CONFIG_NAME
        path.write_text(self.dumps(), encoding="utf-8")
        return path


def _get(data: dict[str, Any], key: str, kind: type, default: Any, where: str) -> Any:
    if key not in data:
        return default
    value = data[key]
    if kind is int and isinstance(value, bool) or not isinstance(value, kind):
        raise ConfigError(f"{where}{key} must be a {kind.__name__}, got {value!r}")
    return value


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def parse_config(text: str) -> Config:
    try:
        data = toml.loads(text)
    except toml.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG_NAME}: {exc}") from exc
    fab = _get(data, "fab", str, None, "")
    project = _get(data, "project", str, None, "")
    if not fab:
        raise ConfigError(f'{CONFIG_NAME} must name the fab (fab = "jlcpcb")')
    if not project:
        raise ConfigError(f"{CONFIG_NAME} must name the KiCad project (project = ...)")
    board, rules, bundle, parts = (
        _section(data, n) for n in ("board", "rules", "bundle", "parts")
    )
    suppliers = _get(parts, "suppliers", list, [], "parts.")
    if not all(isinstance(s, str) and s for s in suppliers):
        raise ConfigError("parts.suppliers must be a list of supplier names")
    layers = _get(board, "layers", int, 2, "board.")
    if layers not in SUPPORTED_LAYERS:
        raise ConfigError(
            f"board.layers must be an even number from 2 to 20, got {layers}"
        )
    return Config(
        fab=fab,
        project=project,
        board=BoardConfig(layers=layers),
        rules=RulesConfig(
            profile=_get(rules, "profile", str, "", "rules."),
            relax_fab_footprint_silk=_get(
                rules, "relax_fab_footprint_silk", bool, True, "rules."
            ),
        ),
        bundle=BundleConfig(
            assemble_through_hole=_get(
                bundle, "assemble_through_hole", bool, False, "bundle."
            ),
        ),
        parts=PartsConfig(
            suppliers=tuple(suppliers),
            library_source=_get(parts, "library_source", str, "", "parts."),
            library=_get(parts, "library", str, "", "parts."),
        ),
    )


def load_config(directory: Path) -> Config | None:
    """The config in ``directory``, or ``None`` if there is no ``kifab.toml``."""
    path = Path(directory) / CONFIG_NAME
    if not path.is_file():
        return None
    return parse_config(path.read_text(encoding="utf-8"))
