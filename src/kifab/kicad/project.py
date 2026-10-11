"""KiCad project discovery, lock-file detection and ``.kicad_pro`` editing."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class InputError(Exception):
    """The given path is not something kifab can work with."""


@dataclass(frozen=True)
class KicadProject:
    """A KiCad project: ``<directory>/<name>.kicad_pro`` and its siblings."""

    directory: Path
    name: str

    @property
    def pro(self) -> Path:
        return self.directory / f"{self.name}.kicad_pro"

    @property
    def sch(self) -> Path:
        return self.directory / f"{self.name}.kicad_sch"

    @property
    def pcb(self) -> Path:
        return self.directory / f"{self.name}.kicad_pcb"

    @property
    def dru(self) -> Path:
        return self.directory / f"{self.name}.kicad_dru"

    def rel(self, path: Path) -> str:
        """``path`` relative to the project directory (posix), or absolute."""
        try:
            return Path(path).resolve().relative_to(self.directory).as_posix()
        except ValueError:
            return Path(path).as_posix()

    @classmethod
    def resolve(cls, path: Path) -> KicadProject:
        """Accept a ``.kicad_pro``/``_sch``/``_pcb`` or a directory with one project."""
        path = Path(path)
        if not path.exists():
            raise InputError(f"{path} does not exist")
        if path.is_dir():
            pros = sorted(path.glob("*.kicad_pro"))
            if len(pros) == 1:
                return cls.resolve(pros[0])
            if len(pros) > 1:
                names = ", ".join(p.name for p in pros)
                raise InputError(f"{path} contains several projects: {names}")
            raise InputError(f"no .kicad_pro found in {path}")
        if path.suffix not in (".kicad_pro", ".kicad_sch", ".kicad_pcb"):
            raise InputError(
                f"expected a .kicad_pro, .kicad_sch, .kicad_pcb or a directory, "
                f"got {path.name}"
            )
        pro = path.with_suffix(".kicad_pro")
        if not pro.is_file():
            raise InputError(f"project file {pro.name} not found next to {path.name}")
        project = cls(pro.parent.resolve(), pro.stem)
        if not project.sch.is_file():
            raise InputError(f"root schematic {project.sch.name} not found")
        return project


def open_locks(directory: Path) -> list[Path]:
    """KiCad's lock files (``~<file>.lck``) in ``directory``: files open in KiCad."""
    return sorted(Path(directory).glob("~*.lck"))


def locked_files(directory: Path) -> list[str]:
    """Names of the files KiCad has open in ``directory`` (from the lock files)."""
    return [p.name[1 : -len(".lck")] for p in open_locks(directory)]


def find_projects(start: Path) -> list[Path]:
    """Every ``.kicad_pro`` in the git repository containing ``start`` (or below it).

    Hidden directories and KiCad's ``*-backups`` directories are skipped.
    """
    start = Path(start).resolve()
    root = next((p for p in (start, *start.parents) if (p / ".git").exists()), start)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and not d.endswith("-backups")
        )
        found += [
            Path(dirpath) / f for f in sorted(filenames) if f.endswith(".kicad_pro")
        ]
    return found


# ---------------------------------------------------------------- .kicad_pro


_RULES_PATH = ("board", "design_settings", "rules")
_SETTINGS_PATH = ("board", "design_settings")


@dataclass(frozen=True)
class NetClass:
    """Sizes of a net class (Board Setup > Net Classes); None: not set (inherits)."""

    name: str
    clearance: float | None = None
    track_width: float | None = None
    via_diameter: float | None = None
    via_drill: float | None = None
    diff_pair_width: float | None = None
    diff_pair_gap: float | None = None


@dataclass(frozen=True)
class ViaSize:
    diameter: float
    drill: float


@dataclass(frozen=True)
class DiffPairSize:
    width: float
    gap: float


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class ProjectFile:
    """A ``.kicad_pro`` file. Only the parts kifab manages are exposed, typed.

    The JSON is kept as KiCad wrote it and written back in KiCad's layout
    (2-space indent, sorted keys, UTF-8), so untouched settings stay byte-identical.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @classmethod
    def parse(cls, text: str) -> ProjectFile:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("a .kicad_pro must contain a JSON object")
        return cls(data)

    @classmethod
    def load(cls, path: Path) -> ProjectFile:
        return cls.parse(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def new(cls, name: str) -> ProjectFile:
        """A minimal project file; KiCad fills in its defaults on first save."""
        return cls(
            {
                "board": {"design_settings": {"rules": {}}},
                "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
                "meta": {"filename": f"{name}.kicad_pro", "version": 3},
            }
        )

    def dumps(self) -> str:
        return (
            json.dumps(self._data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        )

    def save(self, path: Path) -> None:
        Path(path).write_text(self.dumps(), encoding="utf-8")

    def _section(self, keys: tuple[str, ...], create: bool) -> dict[str, Any] | None:
        node: Any = self._data
        for key in keys:
            if not isinstance(node, dict):
                return None
            if key not in node:
                if not create:
                    return None
                node[key] = {}
            node = node[key]
        return node if isinstance(node, dict) else None

    def board_rules(self) -> dict[str, float]:
        """Numeric values of Board Setup > Design Rules > Constraints."""
        section = self._section(_RULES_PATH, create=False) or {}
        return {
            k: float(v)
            for k, v in section.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }

    def net_classes(self) -> list[NetClass]:
        settings = self._data.get("net_settings")
        classes = settings.get("classes") if isinstance(settings, dict) else None
        out = []
        for c in classes if isinstance(classes, list) else []:
            if isinstance(c, dict):
                out.append(
                    NetClass(
                        name=str(c.get("name", "")),
                        clearance=_number(c.get("clearance")),
                        track_width=_number(c.get("track_width")),
                        via_diameter=_number(c.get("via_diameter")),
                        via_drill=_number(c.get("via_drill")),
                        diff_pair_width=_number(c.get("diff_pair_width")),
                        diff_pair_gap=_number(c.get("diff_pair_gap")),
                    )
                )
        return out

    def _sizes(self, key: str) -> list[Any]:
        section = self._section(_SETTINGS_PATH, create=False) or {}
        value = section.get(key)
        return value if isinstance(value, list) else []

    def track_widths(self) -> list[float]:
        """Predefined track widths (Board Setup > Pre-defined Sizes); 0 is the
        net-class entry and left out."""
        return [w for v in self._sizes("track_widths") if (w := _number(v))]

    def via_sizes(self) -> list[ViaSize]:
        out = []
        for v in self._sizes("via_dimensions"):
            if isinstance(v, dict):
                d, h = _number(v.get("diameter")), _number(v.get("drill"))
                if d and h:
                    out.append(ViaSize(d, h))
        return out

    def diff_pair_sizes(self) -> list[DiffPairSize]:
        out = []
        for v in self._sizes("diff_pair_dimensions"):
            if isinstance(v, dict):
                w, g = _number(v.get("width")), _number(v.get("gap"))
                if w and g:
                    out.append(DiffPairSize(w, g))
        return out

    def set_board_rules(self, values: Mapping[str, float | int]) -> None:
        """Set constraint values (ints stay ints, like KiCad's spoke count)."""
        section = self._section(_RULES_PATH, create=True)
        if section is None:
            raise ValueError("board.design_settings.rules is not an object")
        section.update(values)
