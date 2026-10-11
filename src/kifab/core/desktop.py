"""A desktop launcher (freedesktop ``.desktop`` file and icon) for ``kifab-gui``.

A wheel cannot install per-user files, so ``kifab-gui --install-desktop`` writes
them into ``$XDG_DATA_HOME`` (default ``~/.local/share``). Linux only.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from kifab.core.kicad_plugin import PluginError, find_kifab_gui

DESKTOP_NAME = "kifab.desktop"
ICON_NAME = "kifab.svg"


@dataclass(frozen=True)
class DesktopInstall:
    desktop_file: Path
    icon_file: Path
    kifab_gui: Path


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def desktop_entry(kifab_gui: Path, icon: Path) -> str:
    # Exec quoting: a double-quoted argument, with \ and " escaped.
    exe = '"' + str(kifab_gui).replace("\\", "\\\\").replace('"', '\\"') + '"'
    lines = (
        "[Desktop Entry]",
        "Type=Application",
        "Name=kifab",
        "Comment=KiCad projects with the fab built in",
        f"Exec={exe} %f",
        f"Icon={icon}",
        "Terminal=false",
        "Categories=Development;Electronics;Engineering;",
        "Keywords=KiCad;PCB;JLCPCB;fabrication;",
        "StartupWMClass=kifab-gui",
    )
    return "\n".join(lines) + "\n"


def _paths(dest: Path | None) -> tuple[Path, Path]:
    base = dest or data_home()
    return (
        base / "applications" / DESKTOP_NAME,
        base / "icons" / "hicolor" / "scalable" / "apps" / ICON_NAME,
    )


def _check_platform() -> None:
    if not sys.platform.startswith("linux"):
        raise PluginError("desktop launchers are only supported on Linux")


def install_desktop(
    dest: Path | None = None, *, kifab_gui: Path | None = None
) -> DesktopInstall:
    """Write the launcher and icon under ``dest`` (default: the XDG data home)."""
    _check_platform()
    gui = kifab_gui or find_kifab_gui()
    desktop, icon = _paths(dest)
    icon.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    svg = files("kifab.gui.assets").joinpath("kifab-icon.svg").read_bytes()
    icon.write_bytes(svg)
    desktop.write_text(desktop_entry(gui, icon), encoding="utf-8")
    return DesktopInstall(desktop, icon, gui)


def uninstall_desktop(dest: Path | None = None) -> tuple[Path, ...]:
    """Remove what :func:`install_desktop` wrote; returns the files removed."""
    _check_platform()
    removed = []
    for path in _paths(dest):
        if path.is_file():
            path.unlink()
            removed.append(path)
    return tuple(removed)


__all__ = ["DesktopInstall", "install_desktop", "uninstall_desktop"]
