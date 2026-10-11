"""A kifab project: a KiCad project plus ``kifab.toml`` and the vendored libraries."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kifab.core.config import CONFIG_NAME, Config, load_config
from kifab.core.sourcing import Sourcing, sourcing_for
from kifab.fabs.base import Fab, get_fab
from kifab.fabs.rules import DesignRuleProfile
from kifab.kicad.project import InputError, KicadProject, locked_files

LOCK_NAME = "kifab.lock"
LIB_DIR = "lib"
BASIC_LIB = "kifab"
BACKUP_DIR = "kifab-backups"


class KicadOpen(Exception):
    """KiCad has project files open; writing them now would lose work."""


@dataclass(frozen=True)
class Project:
    kicad: KicadProject
    config: Config | None

    @property
    def directory(self) -> Path:
        return self.kicad.directory

    @property
    def name(self) -> str:
        return self.kicad.name

    @property
    def lock_path(self) -> Path:
        return self.directory / LOCK_NAME

    @property
    def lib_dir(self) -> Path:
        return self.directory / LIB_DIR

    def rel(self, path: Path) -> str:
        return self.kicad.rel(path)

    def fab(self) -> Fab | None:
        return get_fab(self.config.fab) if self.config else None

    def sourcing(self) -> Sourcing | None:
        """Fab, suppliers and library source (``None`` without ``kifab.toml``)."""
        if self.config is None:
            return None
        return sourcing_for(get_fab(self.config.fab), self.config.parts)

    @property
    def library_name(self) -> str | None:
        """Nickname of the vendored parts library (``None`` without a config)."""
        if self.config is None:
            return None
        return self.config.parts.library or get_fab(self.config.fab).library_name

    def design_rules(self) -> DesignRuleProfile | None:
        if self.config is None:
            return None
        fab = get_fab(self.config.fab)
        profile = self.config.rules.profile or fab.profiles()[0]
        return fab.design_rules(profile, self.config.rule_options())

    def fab_library_dir(self) -> Path | None:
        name = self.library_name
        return self.lib_dir / name if name else None

    @classmethod
    def open(cls, path: Path) -> Project:
        """Open the project at ``path`` (a directory or a KiCad project file)."""
        path = Path(path)
        directory = path if path.is_dir() else path.parent
        config = load_config(directory) if directory.is_dir() else None
        if path.is_dir() and config is not None:
            kicad = KicadProject.resolve(directory / f"{config.project}.kicad_pro")
        else:
            kicad = KicadProject.resolve(path)
        if config is not None and config.project != kicad.name:
            raise InputError(
                f"{CONFIG_NAME} is for project {config.project!r}, not {kicad.name!r}"
            )
        return cls(kicad, config)

    # ------------------------------------------------------------ safe writes

    def ensure_closed(self, force: bool = False) -> None:
        """Raise :class:`KicadOpen` if KiCad has any file of the project open."""
        open_files = locked_files(self.directory)
        if open_files and not force:
            raise KicadOpen(
                "KiCad has " + ", ".join(open_files) + " open; save and close it "
                "first (or use --force if KiCad is not really running)"
            )

    def backup(self, files: list[Path], stamp: datetime | None = None) -> Path | None:
        """Copy ``files`` (those that exist) to ``kifab-backups/<time>/``."""
        existing = [f for f in files if f.is_file()]
        if not existing:
            return None
        when = (stamp or datetime.now()).strftime("%Y%m%d-%H%M%S")
        dest = self.directory / BACKUP_DIR / when
        n = 1
        while dest.exists():
            n += 1
            dest = self.directory / BACKUP_DIR / f"{when}-{n}"
        dest.mkdir(parents=True)
        for f in existing:
            shutil.copy2(f, dest / f.name)
        return dest
