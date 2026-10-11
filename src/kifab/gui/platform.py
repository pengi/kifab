"""Pick a Qt platform plugin that can start, before Qt aborts the process.

Qt's X11 plugin (xcb) needs system libraries that pip cannot install
(``libxcb-cursor`` since Qt 6.5, plus ``libxcb-icccm``, ``libxkbcommon-x11`` and
friends). If one is missing, Qt prints a hint (naming only libxcb-cursor) and
aborts. kifab-gui loads the plugin first to find out: the dynamic loader names
the missing library. On a Wayland session it then uses Qt's Wayland plugin;
otherwise it says what to install.
"""

from __future__ import annotations

import ctypes
import importlib.util
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

#: The system libraries Qt 6's xcb plugin loads, as Debian/Ubuntu packages.
APT_PACKAGES = (
    "libxcb-cursor0",
    "libxcb-icccm4",
    "libxcb-keysyms1",
    "libxcb-shape0",
    "libxcb-render-util0",
    "libxcb-image0",
    "libxcb-randr0",
    "libxcb-xkb1",
    "libxkbcommon-x11-0",
)
_MISSING = re.compile(r"(lib[\w.+-]+?\.so[.\d]*): cannot open shared object file")


@dataclass(frozen=True)
class PlatformChoice:
    platform: str | None  # value for QT_QPA_PLATFORM, None: let Qt decide
    error: str | None = None  # set when Qt cannot start


def xcb_plugin() -> Path | None:
    """PySide6's X11 platform plugin, found without importing Qt."""
    spec = importlib.util.find_spec("PySide6")
    if spec is None or spec.origin is None:
        return None
    plugin = Path(spec.origin).parent / "Qt" / "plugins" / "platforms" / "libqxcb.so"
    return plugin if plugin.is_file() else None


def missing_xcb_library(plugin: Path | None = None) -> str | None:
    """The first system library the xcb plugin cannot load, or ``None`` if it loads."""
    plugin = plugin or xcb_plugin()
    if plugin is None:
        return None  # nothing to check; let Qt report
    try:
        ctypes.CDLL(str(plugin))
    except OSError as exc:
        m = _MISSING.search(str(exc))
        return m.group(1) if m else str(exc)
    return None


def install_hint(missing: str) -> str:
    return (
        f"Qt's X11 support needs system libraries; {missing} is missing.\n"
        "  Debian/Ubuntu: sudo apt install " + " ".join(APT_PACKAGES) + "\n"
        "  Fedora:        sudo dnf install xcb-util-cursor xcb-util-wm "
        "xcb-util-keysyms xcb-util-renderutil xcb-util-image libxkbcommon-x11\n"
        "  Arch:          sudo pacman -S xcb-util-cursor xcb-util-wm "
        "xcb-util-keysyms xcb-util-renderutil xcb-util-image libxkbcommon-x11\n"
        "(or run on a Wayland session, or set QT_QPA_PLATFORM yourself)"
    )


def choose_platform(
    env: Mapping[str, str],
    missing: Callable[[], str | None] = missing_xcb_library,
    system: str = sys.platform,
) -> PlatformChoice:
    if not system.startswith("linux") or env.get("QT_QPA_PLATFORM"):
        return PlatformChoice(None)
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        return PlatformChoice(
            None, "no display: neither WAYLAND_DISPLAY nor DISPLAY is set"
        )
    lib = missing()
    if lib is None:
        return PlatformChoice(None)
    if env.get("WAYLAND_DISPLAY"):
        return PlatformChoice("wayland")
    return PlatformChoice(None, install_hint(lib))
